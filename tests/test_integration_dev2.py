"""개발 0·2 프로세스 간 통합 테스트: 서버 마이그레이션 → 워커 프로세스 기동 거부/단일 인스턴스,
claim_next 프로세스 경쟁, 가짜 엔진 end-to-end(--once), 키 마스킹. 실제 별도 프로세스를 쓴다."""
import importlib.util
import json
import os
import multiprocessing as mp
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import __main__ as smain  # noqa: E402
from worker import __main__ as wmain  # noqa: E402
from worker import queue, runner  # noqa: E402
from worker.lock import AlreadyRunning, SingleInstance  # noqa: E402

_spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

FAKE_KEY = "sk-ABCDEFGH12345678zzSECRET"

# 별도 프로세스로 워커 main 을 임시 경로로 띄우는 한 줄 실행기(운영 코드의 기본 경로는 건드리지 않는다)
_WORKER_CODE = (
    "import sys; sys.path.insert(0, sys.argv[1]);"
    "from pathlib import Path; from worker.__main__ import main;"
    "sys.exit(main(sys.argv[5:], db_path=sys.argv[2], lock_path=sys.argv[3], root=Path(sys.argv[4])))"
)


def spawn_worker(tmp, *flags):
    return subprocess.Popen(
        [sys.executable, "-c", _WORKER_CODE, str(ROOT), str(tmp / "var" / "studio.db"),
         str(tmp / "var" / "worker.lock"), str(tmp), *flags],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, encoding="utf-8", errors="replace")


def stop(p):
    if p.poll() is None:
        p.kill()
    p.communicate(timeout=10)


class TmpBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="통합_테스트_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "var").mkdir()
        self.db = self.tmp / "var" / "studio.db"


class WorkerStartupTest(TmpBase):
    def test_start_ok_after_server_migration(self):
        smain.prepare_db(self.db)  # 0001 + 0002
        p = spawn_worker(self.tmp, "--once")
        try:
            out, err = p.communicate(timeout=60)
        finally:
            stop(p)
        self.assertEqual(p.returncode, 0, err)

    def test_refuses_when_only_0001_applied(self):
        only1 = self.tmp / "mig"
        only1.mkdir()
        shutil.copy(ROOT / "db" / "migrations" / "0001_init.sql", only1)
        migrate_mod.migrate(self.db, migrations_dir=only1, backup=False)
        p = spawn_worker(self.tmp, "--once")
        try:
            out, err = p.communicate(timeout=60)
        finally:
            stop(p)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("기동 거부", err)

    def test_refuses_without_db(self):
        p = spawn_worker(self.tmp, "--once")
        try:
            _, err = p.communicate(timeout=60)
        finally:
            stop(p)
        self.assertEqual(p.returncode, 1)
        self.assertIn("DB가 없다", err)

    def test_two_workers_only_one_gets_lock(self):
        smain.prepare_db(self.db)
        a, b = spawn_worker(self.tmp), spawn_worker(self.tmp)  # 둘 다 상시 모드
        try:
            deadline = time.time() + 40
            while time.time() < deadline and a.poll() is None and b.poll() is None:
                time.sleep(0.2)
            codes = sorted(("exit" if p.poll() is not None else "alive") for p in (a, b))
            self.assertEqual(codes, ["alive", "exit"])
            loser = a if a.poll() is not None else b
            self.assertEqual(loser.returncode, 1)
            self.assertIn("기동 거부", loser.stderr.read())
            with self.assertRaises(AlreadyRunning):  # 살아 있는 쪽이 잠금을 쥐고 있다
                SingleInstance(self.tmp / "var" / "worker.lock").acquire()
        finally:
            stop(a)
            stop(b)


def _claim_proc(db, barrier, results, n):
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("db_migrate_c", ROOT / "db" / "migrate.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    conn = m.connect(db)
    from worker import queue as q
    for i in range(n):
        barrier.wait(60)
        try:
            r = q.claim_next(conn)
        except Exception as e:  # noqa: BLE001
            r = f"ERR {e!r}"
        results.put((i, r))
        barrier.wait(60)


class ClaimRaceTest(TmpBase):
    def test_two_processes_claim_exactly_one(self):
        N = 20
        smain.prepare_db(self.db)
        conn = migrate_mod.connect(self.db)
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO works(title) VALUES ('작품')")
        conn.execute("INSERT INTO sources(work_id, file_path, origin) VALUES (1, 'a.mp4', 'local_file')")
        ctx = mp.get_context("spawn")
        barrier, results = ctx.Barrier(3), ctx.Queue()
        procs = [ctx.Process(target=_claim_proc, args=(str(self.db), barrier, results, N)) for _ in range(2)]
        for p in procs:
            p.start()
        try:
            for i in range(N):
                conn.execute("UPDATE jobs SET queue_state='finished', slot_id=NULL")
                cur = conn.execute("INSERT INTO jobs(source_id, state, plan_path) VALUES (1,'building','p.json')")
                job = cur.lastrowid
                barrier.wait(60)
                barrier.wait(60)
                got = [results.get(timeout=30) for _ in range(2)]
                self.assertEqual([g[0] for g in got], [i, i])
                vals = [g[1] for g in got]
                self.assertEqual(sorted(map(str, vals)), sorted([str(job), "None"]), f"round {i}: {vals}")
                running = conn.execute("SELECT COUNT(*) FROM jobs WHERE queue_state='running'").fetchone()[0]
                self.assertEqual(running, 1)
        finally:
            for p in procs:
                p.join(timeout=30)
                if p.is_alive():
                    p.terminate()


FAKE_ENGINE = '''\
import sys, time
from pathlib import Path
mode = (Path(__file__).with_name("mode.txt")).read_text().strip()
print("엔진 출력 key=sk-ABCDEFGH12345678zzSECRET")
if mode == "ok":
    sys.exit(0)
if mode == "p0":
    print("[P0-2] 사람 확인 필요")
    sys.exit(1)
if mode == "fail1":
    print("일반 오류")
    sys.exit(1)
if mode == "fail2":
    sys.exit(2)
if mode == "hang":
    sys.stdout.flush()
    time.sleep(60)
'''


class EndToEndTest(TmpBase):
    def setUp(self):
        super().setUp()
        smain.prepare_db(self.db)
        self.conn = migrate_mod.connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO works(title) VALUES ('작품')")
        self.conn.execute("INSERT INTO sources(work_id, file_path, origin) VALUES (1, 'a.mp4', 'local_file')")
        self.fake = self.tmp / "fake_engine.py"
        self.fake.write_text(FAKE_ENGINE, encoding="utf-8")
        d = self.tmp / "plans"
        d.mkdir()
        plan_file = d / "컷표.json"
        plan_file.write_text(json.dumps({"meta": {"source_video": "원본.mp4"}, "cuts": []}), encoding="utf-8")
        eng = runner.load_engine()
        plan = eng.load_plan(plan_file)
        (d / "기획승인.json").write_text(
            json.dumps({"plan_hash": plan["_hash"], "tts_hash": eng.tts_state_hash(plan)}), encoding="utf-8")
        self.plan_hash = plan["_hash"]

    def run_case(self, mode, timeout=60):
        (self.tmp / "mode.txt").write_text(mode, encoding="utf-8")
        cur = self.conn.execute(
            "INSERT INTO jobs(source_id, state, plan_path, plan_hash) VALUES (1,'building','plans/컷표.json',?)",
            (self.plan_hash,))
        job = cur.lastrowid
        orig = runner.run_job

        def with_fake(conn, job_id, root, _timeout):  # runner.run_job 의 engine·timeout 주입점 사용
            return orig(conn, job_id, root, timeout=timeout, engine=self.fake)

        with mock.patch.object(runner, "run_job", with_fake):
            rc = wmain.main(["--once"], db_path=self.db, lock_path=self.tmp / "var" / "worker.lock",
                            root=self.tmp)
        self.assertEqual(rc, 0)
        return self.conn.execute(
            "SELECT state, queue_state, slot_id, hold_reason FROM jobs WHERE id=?", (job,)).fetchone(), job

    def test_exit_code_table(self):
        cases = [("ok", "review", "held_human", 60), ("p0", "building", "held_human", 60),
                 ("fail1", "failed", "finished", 60), ("fail2", "failed", "finished", 60),
                 ("hang", "failed", "finished", 3)]
        for mode, state, qs, to in cases:
            with self.subTest(mode=mode):
                (st, q, slot, _), job = self.run_case(mode, to)
                self.assertEqual((st, q, slot), (state, qs, None))
                ev = self.conn.execute(
                    "SELECT to_state FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT 1", (job,)).fetchone()
                self.assertEqual(ev[0], state)

    def test_hash_mismatch_holds_without_running_engine(self):
        (self.tmp / "plans" / "컷표.json").write_text(
            json.dumps({"meta": {"source_video": "원본.mp4"}, "cuts": [{}]}), encoding="utf-8")
        (st, q, _, reason), _ = self.run_case("ok")
        self.assertEqual((st, q), ("building", "held_human"))
        self.assertIn("P0", reason)

    def test_key_not_left_in_logs_or_db(self):
        for mode in ("p0", "fail1", "fail2", "hang", "ok"):
            self.run_case(mode, 3 if mode == "hang" else 60)
        dump = "\n".join(self.conn.iterdump())
        self.assertNotIn(FAKE_KEY, dump)
        self.assertNotIn("ABCDEFGH12345678zzSECRET", dump)
        logs = list((self.tmp / "var" / "logs").glob("*.log"))
        self.assertTrue(logs, "실패 경로 로그 파일이 있어야 한다")
        for f in logs:
            self.assertNotIn("ABCDEFGH12345678zzSECRET", f.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
