"""개발 5 통합: 실제 워커 프로세스(상시 루프)가 서버 R14 실행 중 취소에 엔진 트리를 멈추고,
다음 작업은 그 뒤에야 집는지(GPU 슬롯 1) 고정한다. 엔진은 runner.run_job 의 engine 주입점으로 바꾼다."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi.testclient import TestClient  # noqa: E402

from _sf import sf  # noqa: E402
from server.app import create_app  # noqa: E402

_spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

BASE = "http://127.0.0.1:8765"

# 자식 프로세스(워커)에서 run_job 의 engine 인자만 가짜 엔진으로 바꾼다. 운영 코드는 건드리지 않는다.
_WORKER_CODE = (
    "import sys; sys.path.insert(0, sys.argv[1]);"
    "from pathlib import Path; from worker import runner; from worker.__main__ import main;"
    "orig = runner.run_job; fake = Path(sys.argv[5]);"
    "runner.run_job = lambda conn, job_id, root, timeout=None: orig(conn, job_id, root, engine=fake);"
    "sys.exit(main([], db_path=sys.argv[2], lock_path=sys.argv[3], root=Path(sys.argv[4])))"
)

# 작업마다 손자 프로세스를 띄우고 오래 돈다. 시작 사실은 plans/<작업>/ 아래 파일로 남긴다.
FAKE_ENGINE = """
import subprocess, sys, time, pathlib
d = pathlib.Path(sys.argv[2]).parent
g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
(d / "grandchild.pid").write_text(str(g.pid))
time.sleep(120)
"""


def alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class WorkerCancelIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="통합5_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "var").mkdir()
        self.db = self.tmp / "var" / "studio.db"
        migrate_mod.migrate(self.db, backup=False)
        self.conn = migrate_mod.connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO works(title) VALUES ('달빛 수선소')")
        self.conn.execute("INSERT INTO sources(work_id, file_path, origin, rights_status) "
                          "VALUES (1, 'a.mp4', 'local_file', 'own')")
        self.fake = self.tmp / "fake_engine.py"
        self.fake.write_text(FAKE_ENGINE, encoding="utf-8")
        self.jobs = {n: self.make_job(n) for n in ("a", "b")}
        self.conn.commit()
        app = create_app(db_path=self.db)
        app.state.root = self.tmp
        self.client = TestClient(app, base_url=BASE)
        self.addCleanup(self.client.close)
        self.worker = None
        self.addCleanup(self.stop_everything)  # 가장 먼저 실행(LIFO): 워커 종료 후 임시 폴더 삭제

    def make_job(self, name):
        d = self.tmp / "plans" / name
        d.mkdir(parents=True)
        pf = d / "컷표.json"
        pf.write_text(json.dumps({"meta": {"source_video": "원본.mp4"}, "cuts": []}), encoding="utf-8")
        plan = sf.load_plan(pf)
        (d / "기획승인.json").write_text(
            json.dumps({"plan_hash": plan["_hash"], "tts_hash": sf.tts_state_hash(plan)}), encoding="utf-8")
        return self.conn.execute(
            "INSERT INTO jobs(source_id, state, plan_path, plan_hash) VALUES (1,'building',?,?)",
            (f"plans/{name}/컷표.json", plan["_hash"])).lastrowid

    def stop_everything(self):
        if self.worker is not None:
            if self.worker.poll() is None:
                self.worker.kill()
            try:
                self.worker.communicate(timeout=10)
            except Exception:  # noqa: BLE001
                pass
        for n in ("a", "b"):  # 취소 실패 시 남을 수 있는 손자 정리
            pid = self.pid_of(n)
            if pid and alive(pid):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                else:
                    os.kill(pid, 9)

    def state(self, jid):
        return self.conn.execute("SELECT state, queue_state FROM jobs WHERE id=?", (jid,)).fetchone()

    def wait_for(self, cond, timeout, what):
        end = time.time() + timeout
        while time.time() < end:
            if self.worker.poll() is not None:
                self.fail(f"워커가 죽었다 rc={self.worker.returncode}: {self.worker.stderr.read()}")
            if cond():
                return
            time.sleep(0.1)
        self.fail(f"시간 초과: {what}")

    def pid_of(self, name):
        f = self.tmp / "plans" / name / "grandchild.pid"
        try:
            return int(f.read_text()) if f.exists() and f.read_text().strip() else None
        except (OSError, ValueError):
            return None

    def cancel(self, jid):
        c = self.client
        c.get("/queue")
        r = c.post(f"/jobs/{jid}/cancel", data={"csrf_token": c.cookies.get("csrf"), "confirm": "yes"},
                   headers={"origin": BASE}, follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text[:200])

    def test_running_cancel_stops_engine_tree_and_keeps_single_slot(self):
        a, b = self.jobs["a"], self.jobs["b"]
        self.worker = subprocess.Popen(
            [sys.executable, "-c", _WORKER_CODE, str(ROOT), str(self.db), str(self.tmp / "var" / "worker.lock"),
             str(self.tmp), str(self.fake)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})

        # A 가 running + 엔진 손자 기동, B 는 슬롯 1 때문에 대기
        self.wait_for(lambda: self.pid_of("a") is not None, 40, "A 엔진 기동")
        self.assertEqual(self.state(a)[1], "running")
        self.assertEqual(self.state(b)[1], "waiting")
        gpid = self.pid_of("a")
        self.assertTrue(alive(gpid))

        # R14 취소 -> 3초 안에 손자 종료
        self.cancel(a)
        t0 = time.monotonic()
        self.wait_for(lambda: not alive(gpid), 3, "A 손자 종료(3초)")
        self.assertLess(time.monotonic() - t0, 3.5)
        self.assertEqual(tuple(self.state(a)), ("canceled", "finished"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outputs WHERE job_id=?", (a,)).fetchone()[0], 0)

        # B 는 A 엔진이 끝난 뒤에야 running
        self.wait_for(lambda: self.pid_of("b") is not None, 20, "B 엔진 기동")
        self.assertFalse(alive(gpid))
        self.assertEqual(self.state(b)[1], "running")
        self.assertEqual(tuple(self.state(a)), ("canceled", "finished"))  # 덮어쓰지 않음
        stop_ev = self.conn.execute(
            "SELECT id, at FROM job_events WHERE job_id=? AND message=?", (a, "실행 중 취소로 중지")).fetchone()
        self.assertIsNotNone(stop_ev)
        # claim_next 는 job_events 를 쓰지 않으므로 B 가 집힌 시각은 jobs.started_at 으로 비교한다
        b_started = self.conn.execute("SELECT started_at FROM jobs WHERE id=?", (b,)).fetchone()[0]
        self.assertIsNotNone(b_started)
        self.assertGreaterEqual(b_started, stop_ev[1], f"B 시작 {b_started} < A 중지 이벤트 {stop_ev[1]}")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE queue_state='running'").fetchone()[0], 1)

        # R16: B 가 실행 중 묶음에 보인다
        html = self.client.get("/queue").text
        run_part = html.split('class="label muted">실행 중', 1)[1].split('class="label muted">', 1)[0]
        self.assertIn(f'id="job-{b}"', run_part)
        self.assertNotIn(f'id="job-{a}"', run_part)

        # 정리: B 도 취소해 엔진을 놓게 한다
        bg = self.pid_of("b")
        self.cancel(b)
        self.wait_for(lambda: not alive(bg), 5, "B 손자 종료")


if __name__ == "__main__":
    unittest.main()
