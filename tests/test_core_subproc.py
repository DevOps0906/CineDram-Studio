import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import subproc  # noqa: E402


def alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class SubprocTest(unittest.TestCase):
    def test_runs_and_captures(self):
        r = subproc.run([sys.executable, "-c", "print('안녕'); import sys; sys.exit(3)"], timeout=20)
        self.assertEqual(r.returncode, 3)
        self.assertIn("안녕", r.stdout)
        self.assertFalse(r.timed_out)

    def test_no_shell_string(self):
        with self.assertRaises(TypeError):
            subproc.run("echo hi", timeout=5)

    def test_shell_metachar_is_literal(self):
        r = subproc.run([sys.executable, "-c", "import sys; print(sys.argv[1])", "a & b | c"], timeout=20)
        self.assertIn("a & b | c", r.stdout)

    def test_env_allowlist(self):
        os.environ["SF_SECRET_TEST"] = "leak"
        try:
            r = subproc.run([sys.executable, "-c",
                             "import os; print('SF_SECRET_TEST' in os.environ, 'PATH' in os.environ or 'Path' in os.environ)"],
                            timeout=20)
        finally:
            del os.environ["SF_SECRET_TEST"]
        self.assertTrue(r.stdout.startswith("False True"), r.stdout + r.stderr)

    def test_timeout_kills_grandchild(self):
        pidfile = Path(tempfile.mkdtemp()) / "gc.pid"
        child = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
            "time.sleep(60)\n"
        )
        t0 = time.time()
        r = subproc.run([sys.executable, "-c", child], timeout=3)
        self.assertTrue(r.timed_out)
        self.assertLess(time.time() - t0, 30)
        gpid = int(pidfile.read_text())
        for _ in range(30):
            if not alive(gpid):
                break
            time.sleep(0.2)
        self.assertFalse(alive(gpid), "손자 프로세스가 살아 있다")

    def test_timeout_returncode_and_partial_output(self):
        code = "import sys, time; print('partial', flush=True); sys.stderr.write('err'); sys.stderr.flush(); time.sleep(60)"
        r = subproc.run([sys.executable, "-c", code], timeout=3)
        self.assertTrue(r.timed_out)
        self.assertEqual(r.returncode, -1)
        self.assertIn("partial", r.output)
        self.assertIn("err", r.stderr)

    def test_output_cap(self):
        code = f"print('x' * {subproc.MAX_OUTPUT_BYTES + 5000})"
        r = subproc.run([sys.executable, "-c", code], timeout=20)
        self.assertTrue(r.truncated)
        self.assertLessEqual(len(r.stdout.encode("utf-8")), subproc.MAX_OUTPUT_BYTES)

    def test_unknown_executable_rejected(self):
        with self.assertRaises(subproc.SubprocRejected):
            subproc.run(["definitely-not-a-real-binary-xyz"], timeout=5)

    def test_default_cwd_is_work_root(self):
        r = subproc.run([sys.executable, "-c", "import os; print(os.getcwd())"], timeout=20)
        self.assertEqual(Path(r.stdout.strip()).resolve(), subproc.WORK_ROOT.resolve())

    def test_env_protected_and_allowed(self):
        for k in ("PATH", "path", "SYSTEMROOT"):
            with self.assertRaises(subproc.SubprocRejected):
                subproc.build_env({k: "x"})
        env = subproc.build_env({"FOO": "1"})
        self.assertEqual(env["FOO"], "1")
        self.assertNotIn("PYTHONPATH", env)
        for k in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "PATHEXT", "COMSPEC"):
            self.assertIn(k, subproc.ENV_ALLOW)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg 없음")
    def test_engine_check_on_sample(self):
        root = subproc.WORK_ROOT
        with tempfile.TemporaryDirectory(prefix="샘플 ") as d:
            r = subproc.run([sys.executable, str(root / "engine" / "examples" / "make_sample.py"), d], timeout=180)
            self.assertEqual(r.returncode, 0, r.output)
            r = subproc.run([sys.executable, str(root / "engine" / "shortsflow.py"), "check",
                             str(Path(d) / "컷표.json")], timeout=120)
            self.assertEqual(r.returncode, 0, r.output)


if __name__ == "__main__":
    unittest.main()
