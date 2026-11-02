import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402

from core import subproc  # noqa: E402
from server import __main__ as entry  # noqa: E402
from server.app import create_app  # noqa: E402


class AppTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(), base_url="http://127.0.0.1:8765")

    def test_healthz(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_docs_hidden(self):
        for p in ("/docs", "/redoc", "/openapi.json"):
            with self.subTest(p=p):
                self.assertEqual(self.client.get(p).status_code, 404)

    def test_binding_fixed(self):
        self.assertEqual((entry.HOST, entry.PORT), ("127.0.0.1", 8765))
        src = (ROOT / "server" / "__main__.py").read_text(encoding="utf-8")
        self.assertNotIn("0.0.0.0", src)
        self.assertNotIn("os.environ", src)

    def test_prepare_db_applies_and_rejects_tampered(self):
        d = Path(tempfile.mkdtemp())
        db = d / "x" / "t.db"
        self.assertTrue(entry.prepare_db(db))
        self.assertEqual(entry.prepare_db(db), [])
        c = sqlite3.connect(db)
        c.execute("UPDATE schema_migrations SET checksum='bad' WHERE version='0001'")
        c.commit()
        c.close()
        with self.assertRaises(entry._load_migrate().MigrationError):
            entry.prepare_db(db)


class PortGuardTest(unittest.TestCase):
    def test_refuses_when_port_in_use(self):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            with self.assertRaises(OSError):
                entry.ensure_port_free("127.0.0.1", port)
        finally:
            s.close()
        entry.ensure_port_free("127.0.0.1", port)  # 닫은 뒤에는 통과


def _port_free():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", entry.PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


class LiveTest(unittest.TestCase):
    @unittest.skipUnless(_port_free(), "8765 사용 중")
    def test_boot_and_loopback_only(self):
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        proc = subprocess.Popen([sys.executable, "-m", "server"], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=flags)
        try:
            body = None
            for _ in range(50):
                try:
                    body = urllib.request.urlopen("http://127.0.0.1:8765/healthz", timeout=1).read()
                    break
                except OSError:
                    time.sleep(0.2)
            self.assertEqual(body, b'{"status":"ok"}')
            out = subprocess.run(["netstat", "-an"], capture_output=True, text=True).stdout
            lines = [ln for ln in out.splitlines() if ":8765" in ln and "LISTEN" in ln.upper()]
            self.assertTrue(lines)
            for ln in lines:
                self.assertIn("127.0.0.1:8765", ln)
        finally:
            subproc.kill_tree(proc)
            proc.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
