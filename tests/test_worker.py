"""worker/ 큐 워커 테스트 (임시 DB, 가짜 엔진 스크립트, 표준 라이브러리만).

엔진을 임포트·실행하지 않는다. 가짜 엔진은 컷표(JSON)에 적힌 대로 출력·종료한다.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker import worker as wk  # noqa: E402

FAKE_ENGINE = textwrap.dedent("""
    import json, os, sqlite3, subprocess, sys, time
    cmd, plan = sys.argv[1], sys.argv[2]
    with open(plan, encoding="utf-8") as f:
        cfg = json.load(f)
    if cfg.get("probe_db"):
        c = sqlite3.connect(cfg["probe_db"])
        print("PROBE", c.execute("SELECT queue_state FROM jobs WHERE id=?", (cfg["job_id"],)).fetchone()[0])
        c.close()
    if cfg.get("env_out"):
        with open(cfg["env_out"], "w", encoding="utf-8") as f:
            json.dump(sorted(os.environ), f)
    if cfg.get("child_pid_file"):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(cfg["child_pid_file"], "w") as f:
            f.write(str(p.pid))
    print("CMD", cmd)
    print(cfg.get("print", ""))
    sys.stdout.flush()
    time.sleep(cfg.get("sleep", 0))
    sys.exit(cfg.get("exit", 0))
""")


def pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, errors="replace").stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class WorkerTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="워커_테스트_"))
        self.root = self.tmp / "작업"
        self.root.mkdir()
        self.db = self.tmp / "큐.sqlite3"
        wk.migrate_mod.migrate(self.db, backup=False)
        self.engine = self.tmp / "fake_engine.py"
        self.engine.write_text(FAKE_ENGINE, encoding="utf-8")
        self.c = wk.migrate_mod.connect(self.db)
        self.c.execute("INSERT INTO works(title) VALUES ('테스트 작품')")
        self.c.execute("INSERT INTO sources(work_id, file_path, origin) VALUES (1, 'a.mp4', 'local_file')")
        self.lock = wk.SingleInstanceLock(wk.lock_path_for(self.db)).acquire()

    def tearDown(self):
        self.lock.release()
        self.c.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_job(self, cmd="render", plan="컷표.json", kind="gpu", cfg=None, priority=0):
        job_id = self.c.execute(
            "INSERT INTO jobs(source_id, engine_cmd, plan_path, slot_kind, priority) VALUES (1, ?, ?, ?, ?)",
            (cmd, plan, kind, priority)).lastrowid
        if cfg is not None:
            d = self.root / f"j{job_id}"
            d.mkdir()
            (d / "컷표.json").write_text(json.dumps(cfg), encoding="utf-8")
            self.c.execute("UPDATE jobs SET plan_path=? WHERE id=?", (f"j{job_id}/컷표.json", job_id))
        return job_id

    def row(self, job_id, cols="queue_state, state, slot_id, hold_reason, attempts"):
        return self.c.execute(f"SELECT {cols} FROM jobs WHERE id=?", (job_id,)).fetchone()

    def events(self, job_id):
        return self.c.execute(
            "SELECT from_state, to_state, message, error FROM job_events WHERE job_id=? ORDER BY id",
            (job_id,)).fetchall()

    def worker(self, **kw):
        kw.setdefault("timeout", 30)
        w = wk.Worker(self.db, [self.root],
                      engine_script=self.engine, log_dir=self.tmp / "logs", **kw)
        w.conn = wk.migrate_mod.connect(self.db)
        w.lock = self.lock
        self.addCleanup(w.conn.close)
        return w


class ClaimTest(WorkerTestBase):
    def test_gpu_slot_one_and_event_same_tx(self):
        a = self.add_job(priority=1)
        b = self.add_job()
        job = wk.claim(self.c, "w1", [self.root])
        self.assertEqual((job["id"], job["slot_id"], job["attempts"]), (a, "gpu-1", 1))
        self.assertEqual(self.row(a)[:3], ("running", "queued", "gpu-1"))
        self.assertEqual([e[:2] for e in self.events(a)], [("waiting", "running")])
        self.assertIsNone(wk.claim(self.c, "w1", [self.root]))  # GPU 슬롯 1 고정
        self.assertEqual(self.row(b)[0], "waiting")
        self.assertFalse(self.c.in_transaction)

    def test_llm_slots_setting(self):
        ids = [self.add_job(cmd="check", kind="llm") for _ in range(3)]
        g = self.add_job()
        got = [wk.claim(self.c, "w1", [self.root], llm_slots=2) for _ in range(4)]
        self.assertEqual([j["slot_id"] for j in got if j], ["gpu-1", "llm-1", "llm-2"])
        self.assertEqual(self.row(g)[0], "running")
        self.assertEqual(self.row(ids[2])[0], "waiting")

    def test_exception_rolls_back(self):
        a = self.add_job()
        with mock.patch.object(wk, "_event", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                wk.claim(self.c, "w1", [self.root])
        self.assertFalse(self.c.in_transaction)
        self.assertEqual(self.row(a)[0], "waiting")
        self.assertEqual(self.row(a)[4], 0)
        self.assertEqual(self.events(a), [])

    def test_approve_rejected(self):
        self.assertNotIn("approve", wk.ALLOWED_COMMANDS)
        with self.assertRaises(Exception):  # 0002 CHECK
            self.add_job(cmd="approve")
        with self.assertRaises(wk.WorkerError):
            wk.run_engine("approve", self.root / "컷표.json", 5, self.engine)

    def test_disallowed_cmd_in_code_fails_job(self):
        a = self.add_job()
        with mock.patch.object(wk, "ALLOWED_COMMANDS", ("check",)):
            self.assertIsNone(wk.claim(self.c, "w1", [self.root]))
        self.assertEqual(self.row(a)[:2], ("finished", "failed"))
        self.assertIn("허용되지 않은", self.events(a)[0][3])

    def test_path_outside_root_fails_job(self):
        a = self.add_job(plan="../밖/컷표.json")
        b = self.add_job(plan=str(self.tmp / "절대" / "컷표.json"))
        self.assertIsNone(wk.claim(self.c, "w1", [self.root]))
        for j in (a, b):
            self.assertEqual(self.row(j)[:2], ("finished", "failed"))
            self.assertIn("경로 거부", self.events(j)[0][3])


class RunTest(WorkerTestBase):
    def run_job(self, cfg, **kw):
        job_id = self.add_job(cfg=cfg)
        job = self.worker(**kw).run_once()
        self.assertEqual(job["id"], job_id)
        return job_id, job

    def test_success(self):
        j, job = self.run_job({"exit": 0})
        self.assertEqual(self.row(j)[:3], ("finished", "queued", None))
        self.assertEqual(self.events(j)[-1][:2], ("running", "finished"))
        self.assertTrue((self.tmp / "logs" / f"job-{j}-1.log").exists())

    def test_p0_exit1_held_human(self):
        j, _ = self.run_job({"exit": 1, "print": "✗ [P0-2] 승인 뒤에 컷표.json 이 바뀌었다"})
        q, st, slot, hold, _ = self.row(j)
        self.assertEqual((q, st, slot), ("held_human", "queued", None))
        self.assertIn("[P0-2]", hold)

    def test_other_exit_failed(self):
        for cfg in ({"exit": 1, "print": "✗ [C2-1] 점검 위반"}, {"exit": 2, "print": "실행 오류"}):
            with self.subTest(cfg=cfg):
                j, _ = self.run_job(cfg)
                self.assertEqual(self.row(j)[:2], ("finished", "failed"))
                self.assertIn("종료 코드", self.events(j)[-1][3])

    def test_committed_before_subprocess(self):
        j = self.add_job(cfg={})
        plan = self.root / f"j{j}" / "컷표.json"
        plan.write_text(json.dumps({"probe_db": str(self.db), "job_id": j}), encoding="utf-8")
        self.worker().run_once()
        log = (self.tmp / "logs" / f"job-{j}-1.log").read_text(encoding="utf-8")
        self.assertIn("PROBE running", log)

    def test_timeout_kills_tree(self):
        pid_file = self.tmp / "child.pid"
        t0 = time.monotonic()
        j, job = self.run_job({"sleep": 60, "child_pid_file": str(pid_file)}, timeout=3)
        self.assertLess(time.monotonic() - t0, 30)
        self.assertIsNone(job["returncode"])
        self.assertEqual(self.row(j)[:2], ("finished", "failed"))
        child = int(pid_file.read_text())
        for _ in range(50):
            if not pid_alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(child), "손자 프로세스가 살아 있다")

    def test_env_allowlist_and_child_starts(self):
        env_out = self.tmp / "env.json"
        with mock.patch.dict(os.environ, {"FAKE_API_KEY": "sk-should-not-pass"}):
            j, job = self.run_job({"env_out": str(env_out)})
        self.assertEqual(job["returncode"], 0)  # 줄인 환경으로 python 이 기동된다
        keys = {k.upper() for k in json.loads(env_out.read_text(encoding="utf-8"))}
        self.assertNotIn("FAKE_API_KEY", keys)
        for k in ("PATH", "TEMP") + (("SYSTEMROOT",) if os.name == "nt" else ()):
            if k in {x.upper() for x in os.environ}:
                self.assertIn(k, keys)
        env = wk.build_env({"SystemRoot": "C:\\Windows", "PATH": "p", "TEMP": "t", "OPENAI_API_KEY": "x"})
        self.assertEqual({k for k in env if not k.startswith("PYTHON")}, {"SYSTEMROOT", "PATH", "TEMP"})

    def test_heartbeat_failure_keeps_running(self):
        plan = self.root / "hb" / "컷표.json"
        plan.parent.mkdir()
        plan.write_text(json.dumps({"sleep": 1}), encoding="utf-8")
        calls = []

        def bad_tick():
            calls.append(1)
            raise RuntimeError("database is locked")
        with self.assertLogs("worker", "WARNING"):
            rc, out = wk.run_engine("check", plan, 30, self.engine, on_tick=bad_tick, tick=0.2)
        self.assertEqual(rc, 0)
        self.assertTrue(calls)

    def test_interrupt_kills_tree(self):
        pid_file = self.tmp / "child.pid"
        plan = self.root / "kb" / "컷표.json"
        plan.parent.mkdir()
        plan.write_text(json.dumps({"sleep": 60, "child_pid_file": str(pid_file)}), encoding="utf-8")

        def interrupt():
            if pid_file.exists():
                raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            wk.run_engine("check", plan, 60, self.engine, on_tick=interrupt, tick=0.2)
        child = int(pid_file.read_text())
        for _ in range(50):
            if not pid_alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(child), "손자 프로세스가 살아 있다")

    def test_popen_failure_marks_failed(self):
        j = self.add_job(plan="없는폴더/컷표.json")  # 루트 안이지만 cwd 가 없다
        job = self.worker().run_once()
        self.assertEqual(job["id"], j)
        self.assertEqual(self.row(j)[:3], ("finished", "failed", None))
        self.assertIn("엔진 실행 실패", self.events(j)[-1][3])

    def test_run_forever_survives_operational_error(self):
        w = self.worker()
        with mock.patch.object(w, "run_once", side_effect=[sqlite3.OperationalError("database is locked"),
                                                           KeyboardInterrupt]) as ro,                 mock.patch.object(wk.time, "sleep"), self.assertLogs("worker", "WARNING"):
            with self.assertRaises(KeyboardInterrupt):
                w.run_forever()
        self.assertEqual(ro.call_count, 2)

    def test_p0_only_for_gated_commands(self):
        out = "✗ [P0-2] 승인 뒤에 컷표.json 이 바뀌었다"
        for cmd in ("render", "handoff", "delegate"):
            self.assertEqual(wk.classify(1, out, cmd)[0], "held_human")
        for cmd in ("check", "recalc", "tts-plan", "tts-measure"):
            self.assertEqual(wk.classify(1, out, cmd)[:2], ("finished", "failed"))

    def test_heartbeat_extends_lease(self):
        a = self.add_job()
        wk.claim(self.c, "w1", [self.root], lease_seconds=1)
        before = self.row(a, "lease_expires_at")[0]
        wk.heartbeat(self.c, a, "w1", 600)
        self.assertGreater(self.row(a, "lease_expires_at")[0], before)


class LockReclaimTest(WorkerTestBase):
    def test_single_instance(self):
        with self.assertRaises(wk.WorkerLockError):
            wk.SingleInstanceLock(wk.lock_path_for(self.db)).acquire()
        with self.assertRaises(wk.WorkerLockError):
            wk.Worker(self.db, [self.root]).start()
        # 같은 DB를 다른 표기(상대·..)로 가리켜도 같은 잠금
        alias = self.tmp / "작업" / ".." / self.db.name
        self.assertEqual(wk.lock_path_for(alias), wk.lock_path_for(self.db))
        with self.assertRaises(wk.WorkerLockError):
            wk.Worker(alias, [self.root]).start()

    def test_lock_path_not_overridable(self):
        with self.assertRaises(TypeError):
            wk.Worker(self.db, [self.root], lock_path=self.tmp / "other.lock")
        from worker.__main__ import main
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit) as cm:
            main(["--db", str(self.db), "--root", str(self.root), "--lock", str(self.tmp / "x.lock")])
        self.assertEqual(cm.exception.code, 2)

    def test_reclaim_requires_lock(self):
        with self.assertRaises(wk.WorkerLockError):
            wk.reclaim(self.c, wk.SingleInstanceLock(self.tmp / "other.lock"))

    def test_reclaim_expired_and_retry_cap(self):
        a = self.add_job()
        b = self.add_job(kind="llm", cmd="check")
        wk.claim(self.c, "dead", [self.root])
        wk.claim(self.c, "dead", [self.root])
        self.assertEqual(wk.reclaim(self.c, self.lock), [])  # 임대 아직 유효
        self.c.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00.000Z'")
        self.c.execute("UPDATE jobs SET attempts=3 WHERE id=?", (b,))
        self.assertEqual(sorted(wk.reclaim(self.c, self.lock)), [a, b])
        self.assertEqual(self.row(a)[:3], ("waiting", "queued", None))
        self.assertEqual(self.row(b)[:2], ("finished", "failed"))
        self.assertIn("재시도 상한", self.events(b)[-1][3])

    def test_start_reclaims_all_running(self):
        a = self.add_job()
        wk.claim(self.c, "dead", [self.root], lease_seconds=3600)
        self.lock.release()
        w = wk.Worker(self.db, [self.root]).start()
        try:
            self.assertEqual(self.row(a)[0], "waiting")
        finally:
            w.stop()
            self.lock.acquire()

    def test_start_refuses_checksum_mismatch(self):
        self.c.execute("UPDATE schema_migrations SET checksum='tampered' WHERE version='0001'")
        self.lock.release()
        w = wk.Worker(self.db, [self.root])
        try:
            with self.assertRaises(wk.migrate_mod.MigrationError):
                w.start()
            self.assertFalse(w.lock.held)
        finally:
            self.lock.acquire()

    def test_start_refuses_pending(self):
        self.c.execute("DELETE FROM schema_migrations WHERE version='0002'")
        with self.assertRaises(wk.migrate_mod.MigrationError):
            wk.verify_migrations(self.db)


if __name__ == "__main__":
    unittest.main()
