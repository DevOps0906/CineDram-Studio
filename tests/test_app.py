"""server/app.py + middleware.py — ADR-003 1~3·5·6 미들웨어 거부 테스트.

venv(requirements-dev.txt)에서 실행. fastapi·httpx가 없으면 skip.
"""
import logging
import sys
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
        from fastapi.testclient import TestClient

    from server.app import PORT, app, create_app
except ImportError:  # 표준 라이브러리만 있는 환경
    TestClient = None

BASE = f"http://127.0.0.1:{PORT}"
LOOPBACK = ("127.0.0.1", 50000)
FAKE_KEY = "sk-ant-api03-" + "A" * 40
FORBIDDEN = "요청이 거부되었습니다."


def client_for(application, base_url=BASE, client=LOOPBACK):
    return TestClient(application, base_url=base_url, client=client)


def app_with_routes():
    a = create_app()

    @a.get("/leak-json")
    async def leak_json():
        return JSONResponse({"api_key": FAKE_KEY})

    @a.get("/leak-html")
    async def leak_html():
        return HTMLResponse(f"<p>{FAKE_KEY}</p>")

    @a.get("/leak-text")
    async def leak_text():
        return StreamingResponse(iter([FAKE_KEY.encode()]), media_type="text/event-stream")

    @a.get("/leak/{kind}")
    async def leak_kind(kind):
        media = {
            "plain": "text/plain",
            "problem": "application/problem+json",
            "none": None,
            "octet": "application/octet-stream",
            "image": "image/png",
            "video": "video/mp4",
            "audio": "audio/mpeg",
        }[kind]
        return Response(FAKE_KEY.encode(), media_type=media)

    @a.get("/cors")
    async def cors():
        return JSONResponse({"ok": True}, headers={"Access-Control-Allow-Origin": "*"})

    @a.get("/boom")
    async def boom():
        raise RuntimeError(f"실패 {FAKE_KEY}")

    @a.post("/change")
    async def change():
        return {"ok": True}

    return a


@unittest.skipIf(TestClient is None, "fastapi/httpx 미설치(venv에서 실행)")
class AppTest(unittest.TestCase):
    def setUp(self):
        self.c = client_for(app)

    def test_health_ok(self):
        r = self.c.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_docs_disabled(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.c.get(path).status_code, 404, path)

    def test_debug_off(self):
        self.assertFalse(app.debug)


@unittest.skipIf(TestClient is None, "fastapi/httpx 미설치(venv에서 실행)")
class ScopeTest(unittest.TestCase):  # 1
    def assertForbidden(self, r):
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.text, FORBIDDEN)

    def test_bind_all_interfaces_rejected(self):
        self.assertForbidden(client_for(app, base_url=f"http://0.0.0.0:{PORT}").get("/health"))

    def test_other_port_rejected(self):
        c = client_for(app, base_url="http://127.0.0.1:9999")
        self.assertForbidden(c.get("/health", headers={"Host": f"127.0.0.1:{PORT}"}))

    def test_non_loopback_client_rejected(self):
        self.assertForbidden(client_for(app, client=("192.168.0.10", 50000)).get("/health"))
        self.assertForbidden(client_for(app, client=("testclient", 50000)).get("/health"))


@unittest.skipIf(TestClient is None, "fastapi/httpx 미설치(venv에서 실행)")
class HostOriginTest(unittest.TestCase):  # 2·3
    def setUp(self):
        self.c = client_for(app_with_routes())

    def test_localhost_host_allowed(self):
        self.assertEqual(self.c.get("/health", headers={"Host": f"localhost:{PORT}"}).status_code, 200)

    def test_other_host_rejected(self):
        for host in ("evil.example", f"evil.example:{PORT}", "127.0.0.1:9999", "127.0.0.1"):
            r = self.c.get("/health", headers={"Host": host})
            self.assertEqual(r.status_code, 403, host)

    def test_post_with_allowed_origin(self):
        r = self.c.post("/change", headers={"Origin": f"http://localhost:{PORT}"})
        self.assertEqual(r.status_code, 200)

    def test_post_other_or_missing_origin_rejected(self):
        for origin in ("http://evil.example", f"http://127.0.0.1:9999", "null", None):
            headers = {"Origin": origin} if origin else {}
            self.assertEqual(self.c.post("/change", headers=headers).status_code, 403, origin)

    def test_other_methods_rejected(self):
        h = {"Origin": BASE}
        self.assertEqual(self.c.put("/change", headers=h).status_code, 403)
        self.assertEqual(self.c.delete("/change", headers=h).status_code, 403)
        self.assertEqual(self.c.options("/change", headers=h).status_code, 403)


@unittest.skipIf(TestClient is None, "fastapi/httpx 미설치(venv에서 실행)")
class ResponseTest(unittest.TestCase):  # 5·6
    def setUp(self):
        self.c = client_for(app_with_routes())

    def test_key_in_json_or_html_rejected(self):
        for path in ("/leak-json", "/leak-html"):
            r = self.c.get(path)
            self.assertEqual(r.status_code, 403, path)
            self.assertNotIn(FAKE_KEY, r.text)

    def test_checked_by_default(self):
        for kind in ("plain", "problem", "none"):
            r = self.c.get(f"/leak/{kind}")
            self.assertEqual(r.status_code, 403, kind)
            self.assertNotIn(FAKE_KEY, r.text)

    def test_stream_and_binary_excluded(self):
        self.assertEqual(self.c.get("/leak-text").status_code, 200)
        for kind in ("octet", "image", "video", "audio"):
            self.assertEqual(self.c.get(f"/leak/{kind}").status_code, 200, kind)

    def test_cors_header_rejected(self):
        self.assertEqual(self.c.get("/cors").status_code, 403)

    def test_exception_masked(self):
        with self.assertLogs("server.security", level=logging.ERROR) as logs:
            r = self.c.get("/boom")
        self.assertEqual(r.status_code, 500)
        self.assertNotIn(FAKE_KEY, r.text)
        out = "\n".join(logs.output)
        self.assertIn("RuntimeError", out)
        self.assertNotIn(FAKE_KEY, out)


@unittest.skipIf(TestClient is None, "fastapi/httpx 미설치(venv에서 실행)")
class MaskFilterTest(unittest.TestCase):  # 6 (uvicorn 로그에 거는 필터)
    def test_args_masked_and_kept_as_tuple(self):
        from server.middleware import MaskSecretsFilter

        rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                                '%s - "%s %s"', ("127.0.0.1:1", "GET", f"/x?token={FAKE_KEY}"), None)
        MaskSecretsFilter().filter(rec)
        self.assertIsInstance(rec.args, tuple)
        self.assertEqual(len(rec.args), 3)
        self.assertNotIn(FAKE_KEY, rec.getMessage())

    def test_dict_args_masked(self):
        from server.middleware import MaskSecretsFilter

        rec = logging.LogRecord("x", logging.INFO, __file__, 1, "%(k)s", ({"k": FAKE_KEY},), None)
        MaskSecretsFilter().filter(rec)
        self.assertNotIn(FAKE_KEY, rec.getMessage())

    def test_handler_filter_masks_propagated_records(self):
        import io
        import logging.config

        from server.run import build_log_config

        root = logging.getLogger()
        saved = (root.handlers[:], root.level)
        buf = io.StringIO()
        old_err, old_out = sys.stderr, sys.stdout
        sys.stderr = sys.stdout = buf  # 핸들러 스트림(ext://sys.stderr·stdout)을 설정 시점에 잡게 한다
        try:
            logging.config.dictConfig(build_log_config())
            logging.getLogger("server.child.deep").error("키 %s", FAKE_KEY)
            try:
                raise RuntimeError(FAKE_KEY)
            except RuntimeError:
                logging.getLogger("uvicorn.error").exception("예외")
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", f"/x?token={FAKE_KEY}", "1.1", 200)
        finally:
            sys.stderr, sys.stdout = old_err, old_out
            for h in root.handlers[:]:
                root.removeHandler(h)
            root.handlers[:] = saved[0]
            root.setLevel(saved[1])
        out = buf.getvalue()
        self.assertIn("키 ***", out)
        self.assertIn("RuntimeError", out)
        self.assertIn("token=***", out)
        self.assertNotIn(FAKE_KEY, out)


if __name__ == "__main__":
    unittest.main()
