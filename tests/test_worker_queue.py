"""worker/ 테스트: 0002 마이그레이션, 집기, 슬롯, 해시 재검사, 종료 코드 대응, 단일 인스턴스, 스키마 검사."""
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from _sf import sf  # noqa: E402
from core import paths  # noqa: E402
from worker import __main__ as wmain  # noqa: E402
from worker import queue, runner  # noqa: E402
from worker.lock import AlreadyRunning, SingleInstance  # noqa: E402

_spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="워커_테스트_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = self.tmp / "var" / "studio.db"
        self.db.parent.mkdir()
        migrate_mod.migrate(self.db)
        self.conn = self.new_conn()
        self.conn.execute("INSERT INTO works(title) VALUES ('작품')")
        self.conn.execute("INSERT INTO sources(work_id, file_path, origin) VALUES (1, 'a.mp4', 'local_file')")

    def new_conn(self):
        c = migrate_mod.connect(self.db)
        self.addCleanup(c.close)
        return c

    def add_job(self, plan_path="plans/컷표.json", state="building", **kw):
        cols = {"source_id": 1, "state": state, "plan_path": plan_path,
                "plan_hash": getattr(self, "plan_hash", None), **kw}
        names = ",".join(cols)
        cur = self.conn.execute(f"INSERT INTO jobs({names}) VALUES ({','.join('?' * len(cols))})",
                                list(cols.values()))
        return cur.lastrowid

    def job(self, job_id):
        return self.conn.execute(
            "SELECT state, queue_state, slot_id, hold_reason FROM jobs WHERE id=?", (job_id,)).fetchone()

    def events(self, job_id):
        return self.conn.execute(
            "SELECT from_state, to_state, message FROM job_events WHERE job_id=? ORDER BY id",
            (job_id,)).fetchall()

    def write_plan(self, approve=True, tamper=False):
        d = self.tmp / "plans"
        d.mkdir(exist_ok=True)
        plan_file = d / "컷표.json"
        plan_file.write_text(json.dumps({"meta": {"source_video": "원본.mp4"}, "cuts": []}), encoding="utf-8")
        if approve:
            eng = sf
            plan = eng.load_plan(plan_file)
            ap = {"plan_hash": plan["_hash"], "tts_hash": eng.tts_state_hash(plan)}
            (d / "기획승인.json").write_text(json.dumps(ap), encoding="utf-8")
            self.plan_hash = plan["_hash"]
        if tamper:
            plan_file.write_text(json.dumps({"meta": {"x": 1}, "cuts": []}), encoding="utf-8")
        return plan_file


class MigrationTest(Base):
    def test_plan_path_column_and_slot_unique(self):
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(jobs)")]
        self.assertIn("plan_path", cols)
        a, b = self.add_job("plans/a.json"), self.add_job("plans/b.json")  # 0003: 활성 plan_path 유일
        self.conn.execute("UPDATE jobs SET queue_state='running', slot_id='gpu0' WHERE id=?", (a,))
        with self.assertRaises(sqlite3.IntegrityError):  # DB 도 슬롯 중복 점유를 막는다
            self.conn.execute("UPDATE jobs SET queue_state='running', slot_id='gpu0' WHERE id=?", (b,))

    def test_set_plan_path_rejects_escape(self):
        j = self.add_job(plan_path=None)
        queue.set_plan_path(self.conn, self.tmp, j, "plans/컷표.json")
        for bad in ("../x.json", "C:/x.json", "/abs.json", "\\\\srv\\s\\x.json", "a:b"):
            with self.assertRaises(paths.PathRejected, msg=bad):
                queue.set_plan_path(self.conn, self.tmp, j, bad)

    def test_read_rejects_bad_plan_path(self):
        j = self.add_job(plan_path="../밖.json")
        self.assertEqual(queue.claim_next(self.conn), j)
        self.assertEqual(runner.run_job(self.conn, j, self.tmp), ("failed", "finished"))


class ClaimTest(Base):
    def test_concurrent_claim_only_one_wins(self):
        for i in range(3):
            self.add_job(f"plans/c{i}.json")
        results, barrier = [], threading.Barrier(4)

        def worker():
            c = migrate_mod.connect(self.db)
            barrier.wait()
            try:
                results.append(queue.claim_next(c))
            finally:
                c.close()

        ts = [threading.Thread(target=worker) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len([r for r in results if r is not None]), 1)
        n = self.conn.execute("SELECT COUNT(*) FROM jobs WHERE queue_state='running'").fetchone()[0]
        self.assertEqual(n, 1)  # 슬롯 동시 점유 1

    def test_slot_busy_blocks_second_claim(self):
        a, b = self.add_job("plans/a.json"), self.add_job("plans/b.json")  # 0003: 활성 plan_path 유일
        self.assertEqual(queue.claim_next(self.conn), a)
        self.assertIsNone(queue.claim_next(self.conn))
        queue.transition(self.conn, a, "review", "finished", message="ok")
        self.assertEqual(queue.claim_next(self.conn), b)

    def test_transition_writes_event(self):
        j = self.add_job()
        queue.claim_next(self.conn)
        queue.transition(self.conn, j, "review", "finished", message="완료")
        self.assertEqual(self.events(j), [("building", "review", "완료")])
        self.assertEqual(self.job(j)[:3], ("review", "finished", None))

    def test_recover_running(self):
        j = self.add_job()
        queue.claim_next(self.conn)
        self.assertEqual(queue.recover_running(self.conn), [j])
        self.assertEqual(self.job(j)[:3], ("building", "waiting", None))
        self.assertEqual(len(self.events(j)), 1)


class HashRecheckTest(Base):
    def test_mismatch_goes_held_human(self):
        self.write_plan(approve=True, tamper=True)
        j = self.add_job()
        queue.claim_next(self.conn)
        self.assertEqual(runner.run_job(self.conn, j, self.tmp), ("building", "held_human"))
        self.assertIn("P0-2", self.job(j)[3])

    def test_not_approved_goes_held_human(self):
        self.write_plan(approve=False)
        j = self.add_job()
        queue.claim_next(self.conn)
        self.assertEqual(runner.run_job(self.conn, j, self.tmp), ("building", "held_human"))

    def test_null_plan_hash_held(self):
        self.write_plan(approve=True)
        j = self.add_job(plan_hash=None)
        queue.claim_next(self.conn)
        self.assertEqual(runner.run_job(self.conn, j, self.tmp), ("building", "held_human"))
        self.assertIn("미기록", self.job(j)[3])

    def test_bad_source_video_held(self):
        plan_file = self.write_plan(approve=False)
        for bad in ("../밖.mp4", "C:/x.mp4", None):
            meta = {"source_video": bad} if bad else {}
            plan_file.write_text(json.dumps({"meta": meta, "cuts": []}), encoding="utf-8")
            eng = sf
            plan = eng.load_plan(plan_file)
            (plan_file.parent / "기획승인.json").write_text(json.dumps(
                {"plan_hash": plan["_hash"], "tts_hash": eng.tts_state_hash(plan)}), encoding="utf-8")
            self.conn.execute("DELETE FROM jobs")
            j = self.add_job(plan_hash=plan["_hash"])
            queue.claim_next(self.conn)
            with self.subTest(bad=bad):
                self.assertEqual(runner.run_job(self.conn, j, self.tmp), ("building", "held_human"))
                self.assertIn("원본 경로", self.job(j)[3])

    def test_junction_source_escape_held(self):
        import os
        import subprocess
        plan_file = self.write_plan(approve=False)
        outside = Path(tempfile.mkdtemp(prefix="밖_"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        link = plan_file.parent / "링크"
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            if r.returncode:
                self.skipTest("junction 생성 불가")
        else:
            os.symlink(outside, link)
        plan_file.write_text(json.dumps({"meta": {"source_video": "링크/a.mp4"}, "cuts": []}), encoding="utf-8")
        eng = sf
        plan = eng.load_plan(plan_file)
        (plan_file.parent / "기획승인.json").write_text(json.dumps(
            {"plan_hash": plan["_hash"], "tts_hash": eng.tts_state_hash(plan)}), encoding="utf-8")
        j = self.add_job(plan_hash=plan["_hash"])
        queue.claim_next(self.conn)
        self.assertEqual(runner.run_job(self.conn, j, self.tmp), ("building", "held_human"))
        self.assertIn("원본 경로", self.job(j)[3])
        if os.name == "nt":
            os.rmdir(link)

    def test_approved_runs_engine(self):
        self.write_plan(approve=True)
        j = self.add_job()
        queue.claim_next(self.conn)
        stub = self.tmp / "ok.py"
        stub.write_text("import sys, pathlib; pathlib.Path(sys.argv[2]).parent.joinpath('숏폼_상하템플릿.mp4').write_bytes(b'mp4'); sys.exit(0)", encoding="utf-8")
        self.assertEqual(runner.run_job(self.conn, j, self.tmp, engine=stub), ("review", "held_human"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outputs WHERE job_id=?", (j,)).fetchone()[0], 1)

    def test_success_without_output_is_held(self):  # C14
        self.write_plan(approve=True)
        j = self.add_job()
        queue.claim_next(self.conn)
        stub = self.tmp / "noout.py"
        stub.write_text("import sys; sys.exit(0)", encoding="utf-8")
        self.assertEqual(runner.run_job(self.conn, j, self.tmp, engine=stub), ("building", "held_human"))
        self.assertIn("산출물 없음", self.job(j)[3])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outputs").fetchone()[0], 0)


class ExitCodeTest(Base):
    def run_stub(self, code, text="", timeout=30, sleep=0):
        stub = self.tmp / "stub.py"
        stub.write_text(f"import sys, time, pathlib; print({text!r}); time.sleep({sleep})\n"
                        f"if {code} == 0: pathlib.Path(sys.argv[2]).parent.joinpath('숏폼_상하템플릿.mp4').write_bytes(b'x')\n"
                        f"sys.exit({code})",
                        encoding="utf-8")
        self.write_plan()
        j = self.add_job()
        queue.claim_next(self.conn)
        res = runner.run_job(self.conn, j, self.tmp, timeout=timeout, engine=stub)
        return j, res

    def test_table(self):
        cases = [
            (0, "", ("review", "held_human")),
            (1, "✗ [P0-2] 승인 뒤 바뀜", ("building", "held_human")),
            (1, "점검 위반", ("failed", "finished")),
            (2, "크래시", ("failed", "finished")),
            (3, "[P0-1] 다른 코드", ("failed", "finished")),
        ]
        for code, text, want in cases:
            with self.subTest(code=code, text=text):
                self.conn.execute("DELETE FROM jobs")
                _, res = self.run_stub(code, text)
                self.assertEqual(res, want)

    def test_failed_keeps_log(self):
        j, _ = self.run_stub(1, "점검 위반")
        logs = list((self.tmp / "var" / "logs").glob(f"job-{j}-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("점검 위반", logs[0].read_text(encoding="utf-8"))

    def test_timeout_is_failed(self):
        _, res = self.run_stub(0, "", timeout=1, sleep=30)
        self.assertEqual(res, ("failed", "finished"))


class SafetyTest(Base):
    def test_exception_becomes_failed(self):
        self.write_plan()
        j = self.add_job()
        queue.claim_next(self.conn)
        with mock.patch.object(runner.subproc, "run", side_effect=OSError("token=sk-abcdefgh12345678")):
            self.assertEqual(runner.run_job(self.conn, j, self.tmp), ("failed", "finished"))
        err = self.conn.execute("SELECT error FROM job_events WHERE job_id=?", (j,)).fetchone()[0]
        self.assertNotIn("sk-abcdefgh", err)
        self.assertIsNone(queue.claim_next(self.conn))  # poison job 이 큐에 남지 않는다

    def test_secret_masked_everywhere(self):
        secret = "sk-ABCDEFGH12345678"
        stub = self.tmp / "s.py"
        stub.write_text(f"import sys\nprint('api_key={secret}')\nprint('Bearer abcdef123456789')\nsys.exit(1)",
                        encoding="utf-8")
        self.write_plan()
        j = self.add_job()
        queue.claim_next(self.conn)
        runner.run_job(self.conn, j, self.tmp, engine=stub)
        dump = repr(self.conn.execute("SELECT * FROM job_events").fetchall())
        dump += repr(self.conn.execute("SELECT hold_reason FROM jobs").fetchall())
        for f in (self.tmp / "var" / "logs").glob("*.log"):
            dump += f.read_text(encoding="utf-8")
        self.assertNotIn(secret, dump)
        self.assertNotIn("abcdef123456789", dump)

    def test_canceled_not_overwritten(self):
        j = self.add_job()
        queue.claim_next(self.conn)
        self.conn.execute("UPDATE jobs SET state='canceled', queue_state='finished', slot_id=NULL WHERE id=?", (j,))
        self.assertFalse(queue.transition(self.conn, j, "review", "held_human"))
        self.assertEqual(self.job(j)[:2], ("canceled", "finished"))
        self.assertEqual(len(self.events(j)), 1)

    def test_set_plan_path_missing_job(self):
        with self.assertRaises(KeyError):
            queue.set_plan_path(self.conn, self.tmp, 999, "plans/컷표.json")

    def test_claim_clears_hold_reason(self):
        j = self.add_job(hold_reason="옛 사유")
        queue.claim_next(self.conn)
        self.assertIsNone(self.job(j)[3])


class StartupTest(Base):
    def test_second_instance_rejected(self):
        lock = self.tmp / "var" / "worker.lock"
        with SingleInstance(lock):
            with self.assertRaises(AlreadyRunning):
                SingleInstance(lock).acquire()
            self.assertEqual(wmain.main(["--once"], self.db, lock, self.tmp), 1)
        with SingleInstance(lock):  # 해제 뒤에는 다시 잡힌다
            pass

    def test_old_schema_refused(self):
        # 필요 버전 이상을 모두 지워 최신 < 필요 상태를 만든다(뒤에 마이그레이션이 늘어도 의도 유지)
        self.conn.execute("DELETE FROM schema_migrations WHERE version >= ?", (queue.REQUIRED_SCHEMA,))
        with self.assertRaises(queue.SchemaTooOld):
            queue.check_schema(self.conn)
        lock = self.tmp / "var" / "worker.lock"
        self.assertEqual(wmain.main(["--once"], self.db, lock, self.tmp), 1)

    def test_main_recovers_and_runs(self):
        j = self.add_job()
        queue.claim_next(self.conn)  # 죽은 워커가 남긴 running

        def fake_run(conn, job_id, root, timeout):
            queue.transition(conn, job_id, "review", "held_human", message="처리")

        with mock.patch.object(runner, "run_job", fake_run):
            self.assertEqual(wmain.main(["--once"], self.db, self.tmp / "var" / "w.lock", self.tmp), 0)
        self.assertEqual(self.job(j)[:2], ("review", "held_human"))
        self.assertTrue(any("복구" in (e[2] or "") for e in self.events(j)))

    def test_main_recovers_stale_llm_calls(self):
        j = self.add_job()
        self.conn.execute("INSERT INTO llm_calls(job_id, step, model, input_sha256, status, cost_usd, "
                          "created_at) VALUES (?, 'topic', 'm', 'h', 'started', 1, '2000-01-01T00:00:00.000Z')",
                          (j,))
        self.assertEqual(wmain.main(["--once"], self.db, self.tmp / "var" / "w2.lock", self.tmp), 0)
        self.assertEqual(self.conn.execute("SELECT status FROM llm_calls").fetchone()[0], "error")


class EnvDefaultsTest(Base):
    """CINEDRAM_DB·CINEDRAM_ROOTS: 미설정이면 기존 기본값, 설정이면 그 값을 쓴다."""

    def setUp(self):
        super().setUp()
        self.saved = {k: os.environ.pop(k, None) for k in (paths.DB_ENV, paths.ROOTS_ENV)}

    def tearDown(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        super().tearDown()

    def test_env_db_missing_refused(self):
        self.assertEqual(wmain.DB_PATH, paths.DEFAULT_DB_PATH)
        os.environ[paths.DB_ENV] = str(self.tmp / "없음.db")
        self.assertEqual(wmain.main(["--once"], lock_path=self.tmp / "var" / "w.lock", root=self.tmp), 1)

    def test_main_uses_env_db_and_root(self):
        os.environ[paths.DB_ENV] = str(self.db)
        os.environ[paths.ROOTS_ENV] = str(self.tmp)
        j = self.add_job()
        seen = []

        def fake_run(conn, job_id, root, timeout):
            seen.append(Path(root))
            queue.transition(conn, job_id, "review", "held_human", message="처리")

        with mock.patch.object(runner, "run_job", fake_run):
            self.assertEqual(wmain.main(["--once"], lock_path=self.tmp / "var" / "w.lock"), 0)
        self.assertEqual(seen, [paths.default_root()])
        self.assertEqual(self.job(j)[:2], ("review", "held_human"))

    def test_main_refuses_relative_db(self):
        os.environ[paths.DB_ENV] = "상대/cine.db"
        self.assertEqual(wmain.main(["--once"], lock_path=self.tmp / "var" / "w.lock", root=self.tmp), 1)

    def test_main_refuses_relative_roots(self):
        os.environ[paths.ROOTS_ENV] = "상대/경로"
        self.assertEqual(wmain.main(["--once"], self.db, self.tmp / "var" / "w.lock"), 1)


if __name__ == "__main__":
    unittest.main()
