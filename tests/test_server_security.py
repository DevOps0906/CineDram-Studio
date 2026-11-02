import io
import logging
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import logmask, paths, subproc  # noqa: E402
from server import __main__ as entry  # noqa: E402
from server.app import create_app  # noqa: E402
from server import security  # noqa: E402
from server.security import CSRF_COOKIE, DEFAULT_PORT  # noqa: E402

BASE = "http://127.0.0.1:8765"
ORIGIN = {"origin": BASE}


def make():
    app = create_app()

    @app.post("/act")
    def act():
        return {"ok": True}

    @app.put("/act")
    def put():
        return {"ok": True}

    @app.delete("/act")
    def delete():
        return {"ok": True}

    @app.post("/form")
    async def form(request: Request):
        return {"body": (await request.body()).decode()}

    @app.post("/stream")
    async def stream(request: Request):
        n = 0
        async for chunk in request.stream():
            n += len(chunk)
        return {"n": n}

    @app.get("/probe")
    def probe(request: Request):
        return {"has_state": bool(request.state.csrf_token)}

    return app


class Base(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(make(), base_url=BASE)

    def token(self):
        self.client.get("/healthz")
        return self.client.cookies.get(CSRF_COOKIE)


class BindingTest(unittest.TestCase):  # 1
    def test_fixed_loopback(self):
        self.assertEqual((entry.HOST, entry.PORT), ("127.0.0.1", 8765))
        self.assertEqual(DEFAULT_PORT, entry.PORT)
        src = (ROOT / "server" / "__main__.py").read_text(encoding="utf-8")
        self.assertNotIn("0.0.0.0", src)


class HostTest(Base):  # 2 (uvicorn --host 0.0.0.0 로 떠도 앱 안에서 막는다)
    def test_allowed(self):
        for h in ("127.0.0.1:8765", "localhost:8765", "LOCALHOST:8765"):
            with self.subTest(h=h):
                self.assertEqual(self.client.get("/healthz", headers={"host": h}).status_code, 200)

    def test_rejected(self):
        for h in ("evil.com", "evil.com:8765", "127.0.0.1", "localhost", "127.0.0.1:8766",
                  "127.0.0.1:8765.evil.com", "192.168.0.5:8765", "0.0.0.0:8765", "testserver"):
            with self.subTest(h=h):
                self.assertEqual(self.client.get("/healthz", headers={"host": h}).status_code, 400)

    def test_rejected_on_post_too(self):
        r = self.client.post("/act", headers={"host": "evil.com", **ORIGIN})
        self.assertEqual(r.status_code, 400)


class OriginMethodTest(Base):  # 3
    def post(self, origin=None):
        h = {"x-csrf-token": self.token()}
        if origin is not None:
            h["origin"] = origin
        return self.client.post("/act", headers=h)

    def test_post_with_exact_origin_passes(self):
        for o in (BASE, "http://localhost:8765"):
            with self.subTest(o=o):
                self.assertEqual(self.post(o).status_code, 200)

    def test_origin_rejected(self):
        for o in (None, "null", "http://evil.com", "https://127.0.0.1:8765", "http://127.0.0.1",
                  "http://127.0.0.1:8766", "http://127.0.0.1:8765.evil.com", "http://127.0.0.1:8765/"):
            with self.subTest(o=o):
                self.assertEqual(self.post(o).status_code, 403)

    def test_only_get_head_post(self):
        for m in ("put", "delete", "patch", "options"):
            with self.subTest(m=m):
                r = getattr(self.client, m)("/act", headers=ORIGIN)
                self.assertEqual(r.status_code, 405)

    def test_no_cors_headers(self):
        r = self.client.get("/healthz", headers=ORIGIN)
        self.assertNotIn("access-control-allow-origin", r.headers)
        r = self.client.options("/healthz", headers={**ORIGIN, "access-control-request-method": "POST"})
        self.assertEqual(r.status_code, 405)
        self.assertNotIn("access-control-allow-origin", r.headers)


class CsrfTest(Base):  # 4
    def test_cookie_flags(self):
        sc = self.client.get("/healthz").headers["set-cookie"]
        self.assertIn("HttpOnly", sc)
        self.assertIn("SameSite=Strict", sc)

    def test_header_ok(self):
        r = self.client.post("/act", headers={**ORIGIN, "x-csrf-token": self.token()})
        self.assertEqual(r.status_code, 200)

    def test_form_field_ok_and_body_replayed(self):
        t = self.token()
        r = self.client.post("/form", headers=ORIGIN, data={"csrf_token": t, "a": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("a=1", r.json()["body"])

    def test_missing_token(self):
        self.token()
        self.assertEqual(self.client.post("/act", headers=ORIGIN).status_code, 403)

    def test_cookie_only_not_enough(self):
        self.token()  # 쿠키는 자동 전송되지만 헤더·필드 없음
        self.assertEqual(self.client.post("/form", headers=ORIGIN, data={"a": "1"}).status_code, 403)

    def test_no_cookie(self):
        t = self.token()
        fresh = TestClient(make(), base_url=BASE)  # 쿠키 없는 클라이언트
        self.assertEqual(fresh.post("/act", headers={**ORIGIN, "x-csrf-token": t}).status_code, 403)

    def test_mismatch_and_forged(self):
        other = TestClient(make(), base_url=BASE)
        other.get("/healthz")
        t_other = other.cookies.get(CSRF_COOKIE)
        t = self.token()
        for bad in (t + "x", "abc.def", "garbage", t_other):
            with self.subTest(bad=bad):
                r = self.client.post("/act", headers={**ORIGIN, "x-csrf-token": bad})
                self.assertEqual(r.status_code, 403)

    def test_state_token_available(self):
        self.assertTrue(self.client.get("/probe").json()["has_state"])


class BodyLimitTest(Base):
    def test_content_length_over_limit(self):
        t = self.token()
        big = b"a" * (security.MAX_BODY_BYTES + 1)
        r = self.client.post("/act", headers={**ORIGIN, "x-csrf-token": t}, content=big)
        self.assertEqual(r.status_code, 413)
        r = self.client.post("/form", headers=ORIGIN, data={"csrf_token": t, "x": "a" * security.MAX_BODY_BYTES})
        self.assertEqual(r.status_code, 413)

    def test_at_limit_ok(self):
        t = self.token()
        r = self.client.post("/stream", headers={**ORIGIN, "x-csrf-token": t},
                             content=b"a" * security.MAX_BODY_BYTES)
        self.assertEqual(r.status_code, 200)

    def test_streaming_over_limit_without_content_length(self):
        t = self.token()

        def gen():
            for _ in range(11):
                yield b"a" * 100_000

        r = self.client.post("/stream", headers={**ORIGIN, "x-csrf-token": t}, content=gen())
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.headers["x-frame-options"], "DENY")

    def test_streaming_form_over_limit(self):
        t = self.token()

        def gen():
            yield f"csrf_token={t}&x=".encode()
            for _ in range(11):
                yield b"a" * 100_000

        r = self.client.post("/form", headers={**ORIGIN, "content-type": "application/x-www-form-urlencoded"},
                             content=gen())
        self.assertEqual(r.status_code, 413)


class TtlTest(Base):
    def test_expired_rejected_then_reissued(self):
        t = self.token()
        future = security.time.time() + security.CSRF_TTL_SECONDS + 10
        with mock.patch.object(security.time, "time", return_value=future):
            r = self.client.post("/act", headers={**ORIGIN, "x-csrf-token": t})
            self.assertEqual(r.status_code, 403)
            r = self.client.get("/healthz")
            self.assertIn("set-cookie", r.headers)
            t2 = self.client.cookies.get(CSRF_COOKIE)
            self.assertNotEqual(t, t2)
            r = self.client.post("/act", headers={**ORIGIN, "x-csrf-token": t2})
            self.assertEqual(r.status_code, 200)

    def test_within_ttl_ok(self):
        t = self.token()
        soon = security.time.time() + security.CSRF_TTL_SECONDS - 60
        with mock.patch.object(security.time, "time", return_value=soon):
            self.assertEqual(self.client.post("/act", headers={**ORIGIN, "x-csrf-token": t}).status_code, 200)

    def test_tampered_timestamp_rejected(self):
        t = self.token()
        nonce, ts, sig = t.split(".")
        forged = f"{nonce}.{int(ts) + 3600}.{sig}"
        self.client.cookies.set(CSRF_COOKIE, forged)
        self.assertEqual(self.client.post("/act", headers={**ORIGIN, "x-csrf-token": forged}).status_code, 403)


class HeadersTest(Base):
    def check(self, r):
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")

    def test_on_all_response_kinds(self):
        t = self.token()
        self.check(self.client.get("/healthz"))
        self.check(self.client.get("/nope"))  # 404
        self.check(self.client.get("/healthz", headers={"host": "evil.com"}))  # 400
        self.check(self.client.post("/act", headers={"x-csrf-token": t}))  # 403
        self.check(self.client.put("/act", headers=ORIGIN))  # 405
        self.check(self.client.post("/act", headers={**ORIGIN, "x-csrf-token": t}))  # 200

    def test_no_duplicates(self):
        r = self.client.get("/healthz")
        self.assertEqual(len(r.headers.get_list("x-frame-options")), 1)


class NoSecretEchoTest(Base):  # 5
    def test_no_key_pattern_in_any_route_response(self):
        t = self.token()
        checked = 0
        for route in self.client.app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", None) or set()
            if "{" in path:
                continue
            if "GET" in methods:
                r = self.client.get(path)
            elif "POST" in methods:
                r = self.client.post(path, headers={**ORIGIN, "x-csrf-token": t})
            else:
                continue
            checked += 1
            self.assertEqual(logmask.mask(r.text), r.text, path)
            for k, v in r.headers.items():
                if k.lower() != "set-cookie":  # CSRF 쿠키는 HttpOnly 로만 내려간다
                    self.assertEqual(logmask.mask(v), v, f"{path} {k}")
        self.assertGreater(checked, 2)

    def test_error_responses_do_not_leak(self):
        r = self.client.get("/healthz", headers={"host": "sk-abcdefgh12345678.evil"})
        self.assertNotIn("sk-abcdefgh12345678", r.text)
        self.assertNotIn("sk-abcdefgh12345678", str(r.headers))


class LogMaskTest(unittest.TestCase):  # 6
    def test_uvicorn_loggers_masked(self):
        create_app()
        for name in ("", "uvicorn.access", "uvicorn.error"):
            lg = logging.getLogger(name)
            buf = io.StringIO()
            h = logging.StreamHandler(buf)
            h.setFormatter(logging.Formatter("%(message)s"))
            old_level, old_prop = lg.level, lg.propagate
            lg.addHandler(h)
            lg.setLevel(logging.INFO)
            lg.propagate = False
            try:
                lg.info("GET /x?api_key=SECRETVALUE123 Authorization: Bearer abcdef123456")
                lg.info("key %s", "sk-abcdefgh12345678")
            finally:
                lg.removeHandler(h)
                lg.setLevel(old_level)
                lg.propagate = old_prop
            out = buf.getvalue()
            with self.subTest(logger=name or "root"):
                self.assertNotIn("SECRETVALUE123", out)
                self.assertNotIn("abcdef123456", out)
                self.assertNotIn("sk-abcdefgh12345678", out)
                self.assertIn("***", out)

    def test_child_env_allowlist(self):
        os.environ["MY_API_KEY"] = "sk-abcdefgh12345678"
        try:
            self.assertNotIn("MY_API_KEY", subproc.build_env())
        finally:
            del os.environ["MY_API_KEY"]


class PathAndShellTest(unittest.TestCase):  # 7
    def test_path_escape_rejected(self):
        root = Path(tempfile.mkdtemp())
        (root / "원본").mkdir()
        for rel in ("../x", "..\\x", "/etc/passwd", "C:\\Windows", "a/../../x", "\\\\srv\\share", "", "a\x00b"):
            with self.subTest(rel=rel):
                with self.assertRaises(paths.PathRejected):
                    paths.resolve_in(root, rel)
                with self.assertRaises(paths.PathRejected):
                    paths.resolve_source(root, rel)

    def test_subprocess_rejects_shell_string(self):
        with self.assertRaises(TypeError):
            subproc.run("echo hi && whoami", timeout=5)


if __name__ == "__main__":
    unittest.main()
