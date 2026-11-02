"""실행 중 취소: 엔진 프로세스 트리 종료, 상태 유지, outputs 없음."""
import os
import sys
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import subproc  # noqa: E402
from test_worker_queue import Base, migrate_mod  # noqa: E402
from worker import queue, runner  # noqa: E402

FAKE = '''
import subprocess, sys, time, pathlib
pid_file = pathlib.Path(sys.argv[2]).parent / "grandchild.pid"
g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
pid_file.write_text(str(g.pid))
time.sleep(120)
'''


def alive(pid):
    if os.name == "nt":
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class SubprocStopTest(unittest.TestCase):
    def test_should_stop_none_unchanged(self):
        r = subproc.run([sys.executable, "-c", "print('ok')"], timeout=20)
        self.assertEqual((r.returncode, r.stopped), (0, False))

    def test_should_stop_kills(self):
        t0 = time.monotonic()
        r = subproc.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=60,
                        should_stop=lambda: True)
        self.assertTrue(r.stopped)
        self.assertEqual(r.returncode, -2)
        self.assertLess(time.monotonic() - t0, 5)


class RunnerCancelTest(Base):
    def _run(self, new_state):
        self.write_plan()
        engine = self.tmp / "fake_engine.py"
        engine.write_text(FAKE, encoding="utf-8")
        j = self.add_job()
        self.assertEqual(queue.claim_next(self.conn), j)
        pid_file = self.tmp / "plans" / "grandchild.pid"

        def cancel():
            for _ in range(100):
                if pid_file.exists() and pid_file.read_text().strip():
                    break
                time.sleep(0.1)
            c = migrate_mod.connect(self.db)
            c.execute("UPDATE jobs SET state=?, queue_state=? WHERE id=?",
                      (new_state, "finished", j))
            c.close()

        th = threading.Thread(target=cancel)
        th.start()
        res = runner.run_job(self.conn, j, self.tmp, timeout=60, engine=engine)
        th.join()
        return j, res, int(pid_file.read_text())

    def _check(self, new_state):
        t0 = time.monotonic()
        j, res, pid = self._run(new_state)
        self.assertLess(time.monotonic() - t0, 12)
        time.sleep(0.5)
        self.assertFalse(alive(pid))
        self.assertEqual(self.job(j)[:2], (new_state, "finished"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outputs").fetchone()[0], 0)
        self.assertEqual(res, (new_state, "finished"))
        self.assertIn("실행 중 취소로 중지", [e[2] for e in self.events(j)])

    def test_canceled_during_run(self):
        self._check("canceled")

    def test_finished_during_run(self):
        self._check("failed")


class KillFailedTest(Base):
    def test_kill_failed_flag(self):
        with mock.patch.object(subproc, "kill_tree"), mock.patch.object(subproc, "_reap", return_value=False):
            r = subproc.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=60,
                            should_stop=lambda: True)
        self.assertTrue(r.kill_failed)
        # 정리: 실제로 죽인다
        subproc.run([sys.executable, "-c", "pass"], timeout=10)

    def test_worker_stops_on_kill_failed(self):
        from worker import __main__ as wmain
        self.write_plan()
        j = self.add_job()
        fake = subproc.RunResult(-1, "", "", timed_out=True, kill_failed=True)
        real = subproc.run

        def fake_run(args, *a, **kw):  # 승인 게이트 호출은 실제로, 엔진 호출만 가짜로
            return fake if runner.ENGINE.name in str(args[1]) else real(args, *a, **kw)

        with mock.patch.object(subproc, "run", side_effect=fake_run):
            rc = wmain.main(["--once"], db_path=self.db, lock_path=self.tmp / "var" / "w.lock", root=self.tmp)
        self.assertEqual(rc, 3)
        self.assertIn("엔진 종료 실패 — 워커 정지", [e[2] for e in self.events(j)])
        self.assertEqual(self.job(j)[:2], ("failed", "finished"))
        j2 = self.add_job("plans/b.json")  # 정지한 워커는 다음 작업을 집지 않는다
        self.assertEqual(self.job(j2)[1], "waiting")


if __name__ == "__main__":
    unittest.main()
