"""ADR-003 4 CSRF — 미들웨어 쿠키 발급·재발급, require_csrf 거부, 실물 앱 비GET 라우트 보호.

venv(requirements.txt + requirements-dev.txt)에서 실행. fastapi·httpx·python-multipart가 없으면 skip.
거부 테스트는 허용 Origin·Host를 붙여 1~3을 통과시킨 뒤 CSRF만으로 막히는지 본다.
"""
import sys
import time
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import python_multipart  # noqa: F401  폼 파싱 의존성
        from fastapi import APIRouter, Depends, FastAPI, File, Form, Request, UploadFile
        from fastapi.routing import APIRoute, iter_route_contexts
        from fastapi.testclient import TestClient
        from starlette.endpoints import HTTPEndpoint
        from starlette.routing import Mount, Route

    from server.app import PORT, app, create_app
    from server.csrf import (
        COOKIE_CONFLICT,
        CSRF_COOKIE,
        CSRF_FAILED,
        MAX_BODY_BYTES,
        MAX_FIELDS,
        MAX_FILES,
        SID_COOKIE,
        TOO_LARGE,
        require_csrf,
    )
    from server.security import CSRF_MAX_AGE, issue_csrf_token
except ImportError:  # 표준 라이브러리만 있는 환경
    TestClient = None

BASE = f"http://127.0.0.1:{PORT}"
LOOPBACK = ("127.0.0.1", 50000)
SAFE = {"GET", "HEAD"}
FORBIDDEN = "요청이 거부되었습니다."
MB = 1024 * 1024


def csrf_app():
    a = create_app()

    @a.get("/token")
    async def token(request: Request):
        return {"t": request.state.csrf_token}

    @a.post("/submit", dependencies=[Depends(require_csrf)])
    async def submit():
        return {"ok": True}

    @a.post("/echo", dependencies=[Depends(require_csrf)])
    async def echo(request: Request):
        return {"n": len(await request.body())}

    @a.post("/upload", dependencies=[Depends(require_csrf)])
    async def upload(f: list[UploadFile] = File(default=[]), note: str = Form(default="")):
        return {"n": len(f)}

    return a


def fresh_client(application):
    return TestClient(application, base_url=BASE, client=LOOPBACK, headers={"Origin": BASE})


def set_cookies(response):
    return response.headers.get_list("set-cookie")


def has_require_csrf(dependant):
    if dependant.call is require_csrf:
        return True
    return any(has_require_csrf(d) for d in dependant.dependencies)


def unguarded_routes(routes, prefix="", seen=None):
    """비GET인데 require_csrf가 없는 경로 목록. 알 수 없는 Mount·일반 Route도 실패로 센다.
    seen(집합)을 주면 훑은 경로를 모은다. Route.methods None(클래스형 엔드포인트)은 비GET으로 본다."""
    bad = []
    # FastAPI 0.162는 include_router를 지연 객체(_IncludedRouter)로 둔다 → 공개 iter_route_contexts로 펼친다.
    # 컨텍스트의 dependant는 include_router(dependencies=…)까지 합친 실효 값이다.
    for ctx in iter_route_contexts(routes):
        r = ctx.original_route
        path = ctx.path or getattr(r, "path", "")
        if seen is not None:
            seen.add(prefix + path)
        if isinstance(r, APIRoute):
            if set(ctx.methods) - SAFE and not has_require_csrf(ctx.dependant):
                bad.append(prefix + path)
        elif isinstance(r, Mount):
            sub = getattr(r, "routes", None)
            if sub:
                bad.extend(unguarded_routes(sub, prefix + r.path, seen))
            else:
                bad.append(f"{prefix}{r.path} (내부 라우트를 알 수 없는 Mount)")
        elif isinstance(r, Route):
            if r.methods is None or set(r.methods) - SAFE:
                bad.append(prefix + r.path)
        else:
            bad.append(f"{prefix}{getattr(r, 'path', r)!r} (알 수 없는 라우트 형식)")
    return bad


@unittest.skipIf(TestClient is None, "fastapi/httpx/python-multipart 미설치(venv에서 실행)")
class IssueTest(unittest.TestCase):
    def setUp(self):
        self.a = csrf_app()
        self.c = fresh_client(self.a)

    def test_first_request_sets_both_cookies(self):
        r = self.c.get("/token")
        self.assertEqual(r.status_code, 200)
        cookies = set_cookies(r)
        self.assertEqual(len(cookies), 2)
        for line in cookies:
            self.assertIn("HttpOnly", line)
            self.assertIn("SameSite=Strict", line)
            self.assertIn("Path=/", line)
        self.assertTrue(any(c.startswith(f"{SID_COOKIE}=") for c in cookies))
        self.assertIn(f"{CSRF_COOKIE}={r.json()['t']};", "".join(cookies))

    def test_valid_cookie_not_reissued(self):
        t = self.c.get("/token").json()["t"]
        r = self.c.get("/token")
        self.assertEqual(set_cookies(r), [])
        self.assertEqual(r.json()["t"], t)

    def test_invalid_cookie_reissued(self):
        r = self.c.get("/token", headers={"Cookie": f"{SID_COOKIE}={'s' * 40}; {CSRF_COOKIE}={'0' * 32}.1.abc"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(set_cookies(r)), 2)

    def test_near_expiry_not_reissued(self):
        # 회귀: 만료 전 갱신을 하면 열린 폼의 토큰이 쿠키와 어긋나 제출이 깨진다.
        sid = "s" * 40
        old = issue_csrf_token(self.a.state.csrf_secret, sid, now=time.time() - CSRF_MAX_AGE + 60)
        cookie = {"Cookie": f"{SID_COOKIE}={sid}; {CSRF_COOKIE}={old}"}
        r = self.c.get("/token", headers=cookie)
        self.assertEqual(set_cookies(r), [])
        self.assertEqual(r.json()["t"], old)
        self.assertEqual(self.c.post("/submit", data={"csrf_token": old}, headers=cookie).status_code, 200)

    def test_expired_reissued(self):
        sid = "s" * 40
        old = issue_csrf_token(self.a.state.csrf_secret, sid, now=time.time() - CSRF_MAX_AGE - 10)
        r = self.c.get("/token", headers={"Cookie": f"{SID_COOKIE}={sid}; {CSRF_COOKIE}={old}"})
        self.assertEqual(len(set_cookies(r)), 2)
        self.assertNotEqual(r.json()["t"], old)
        self.assertIn(f"{SID_COOKIE}={sid};", "".join(set_cookies(r)))  # 형식 맞는 sid는 유지

    def test_duplicate_cookie_conflict(self):
        sid = "s" * 40
        t = issue_csrf_token(self.a.state.csrf_secret, sid)
        for cookie in (f"{SID_COOKIE}={sid}; {CSRF_COOKIE}={t}; {CSRF_COOKIE}={t}",
                       f"{SID_COOKIE}={sid}; {SID_COOKIE}=other; {CSRF_COOKIE}={t}"):
            with self.assertLogs("server.security", level="WARNING") as logs:
                r = self.c.get("/token", headers={"Cookie": cookie})
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.text, COOKIE_CONFLICT)
            self.assertEqual(set_cookies(r), [])
            self.assertIn("쿠키 충돌", "\n".join(logs.output))

    def test_secret_per_app_in_memory(self):
        self.assertIsInstance(self.a.state.csrf_secret, bytes)
        self.assertNotEqual(self.a.state.csrf_secret, create_app().state.csrf_secret)


@unittest.skipIf(TestClient is None, "fastapi/httpx/python-multipart 미설치(venv에서 실행)")
class RequireCsrfTest(unittest.TestCase):
    def setUp(self):
        self.a = csrf_app()
        self.c = fresh_client(self.a)
        self.t = self.c.get("/token").json()["t"]

    def assertCsrfRejected(self, r):
        self.assertEqual(r.status_code, 403)
        self.assertIn(CSRF_FAILED, r.text)

    def assertTooLarge(self, r):
        self.assertEqual(r.status_code, 413)
        self.assertNotEqual(r.text, FORBIDDEN)
        self.assertIn(TOO_LARGE, r.text)

    @staticmethod
    def _chunks():
        for _ in range(MAX_BODY_BYTES // MB + 1):
            yield b"a" * MB

    def test_form_field_ok(self):
        self.assertEqual(self.c.post("/submit", data={"csrf_token": self.t}).status_code, 200)

    def test_header_ok(self):
        self.assertEqual(self.c.post("/submit", headers={"X-CSRF-Token": self.t}).status_code, 200)

    def test_localhost_origin_host_ok(self):
        r = self.c.post("/submit", data={"csrf_token": self.t},
                        headers={"Host": f"localhost:{PORT}", "Origin": f"http://localhost:{PORT}"})
        self.assertEqual(r.status_code, 200)

    def test_multipart_ok(self):
        r = self.c.post("/submit", data={"csrf_token": self.t}, files={"f": ("a.srt", b"1")})
        self.assertEqual(r.status_code, 200)

    def test_missing_token_rejected(self):
        self.assertCsrfRejected(self.c.post("/submit", data={"x": "1"}))
        self.assertCsrfRejected(self.c.post("/submit"))
        self.assertCsrfRejected(self.c.post("/submit", json={"csrf_token": self.t}))

    def test_wrong_token_rejected(self):
        other = fresh_client(self.a).get("/token").json()["t"]  # 다른 세션 토큰
        self.assertCsrfRejected(self.c.post("/submit", data={"csrf_token": other}))
        self.assertCsrfRejected(self.c.post("/submit", data={"csrf_token": self.t + "0"}))
        self.assertCsrfRejected(self.c.post("/submit", headers={"X-CSRF-Token": "x"}))

    def test_duplicate_form_token_rejected(self):
        r = self.c.post("/submit", content=f"csrf_token={self.t}&csrf_token={self.t}",
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertCsrfRejected(r)

    def test_missing_cookie_rejected(self):
        self.c.cookies.clear()
        self.assertCsrfRejected(self.c.post("/submit", data={"csrf_token": self.t}))

    def test_duplicate_cookie_rejected(self):
        sid = self.c.cookies.get(SID_COOKIE)
        self.c.cookies.clear()
        with self.assertLogs("server.security", level="WARNING"):
            r = self.c.post("/submit", data={"csrf_token": self.t}, headers={
                "Cookie": f"{SID_COOKIE}={sid}; {CSRF_COOKIE}={self.t}; {CSRF_COOKIE}={self.t}"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.text, COOKIE_CONFLICT)

    def test_other_app_secret_rejected(self):
        c2 = fresh_client(csrf_app())  # 재기동 = 새 비밀
        c2.cookies.update(self.c.cookies)
        self.assertCsrfRejected(c2.post("/submit", data={"csrf_token": self.t}))

    def test_too_many_files_rejected(self):
        files = [("f", (f"{i}.srt", b"1")) for i in range(MAX_FILES + 1)]
        r = self.c.post("/submit", data={"csrf_token": self.t}, files=files)
        self.assertIn(r.status_code, (400, 413))  # Starlette 파서 상한(400) 또는 재검사(413)

    def test_file_route_ok(self):
        r = self.c.post("/upload", data={"csrf_token": self.t}, files=[("f", ("a.srt", b"1"))])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"n": 1})

    def test_file_route_recount_files(self):
        # File 파라미터가 있으면 FastAPI가 기본 상한(1000)으로 먼저 파싱한다 → require_csrf 재검사가 막는다.
        files = [("f", (f"{i}.srt", b"1")) for i in range(MAX_FILES + 1)]
        r = self.c.post("/upload", data={"csrf_token": self.t}, files=files)
        self.assertEqual(r.status_code, 413)

    def test_file_route_recount_fields(self):
        data = {f"x{i}": "1" for i in range(MAX_FIELDS)}
        data["csrf_token"] = self.t
        r = self.c.post("/upload", data=data, files=[("f", ("a.srt", b"1"))])
        self.assertEqual(r.status_code, 413)

    def test_urlencoded_too_many_fields(self):
        data = {f"x{i}": "1" for i in range(MAX_FIELDS)}
        data["csrf_token"] = self.t
        # File 없는 라우트는 require_csrf가 상한을 걸고 처음 파싱한다 → Starlette 파서 상한(400).
        self.assertIn(self.c.post("/submit", data=data).status_code, (400, 413))

    def test_body_too_large_content_length(self):
        self.assertTooLarge(self.c.post("/echo", content=b"a" * (MAX_BODY_BYTES + 1),
                                        headers={"X-CSRF-Token": self.t}))

    def test_chunked_body_too_large(self):
        # 유효 토큰 + 본문을 읽는 라우트: _limit_body가 없으면 200이 된다.
        self.assertTooLarge(self.c.post("/echo", content=self._chunks(), headers={"X-CSRF-Token": self.t}))

    def test_chunked_body_too_large_fastapi_form_parse(self):
        r = self.c.post("/upload", content=self._chunks(), headers={
            "X-CSRF-Token": self.t, "Content-Type": "application/x-www-form-urlencoded"})
        self.assertTooLarge(r)

    def test_chunked_small_body_ok(self):
        def small():
            yield b"abc"
        r = self.c.post("/echo", content=small(), headers={"X-CSRF-Token": self.t})
        self.assertEqual(r.json(), {"n": 3})


@unittest.skipIf(TestClient is None, "fastapi/httpx/python-multipart 미설치(venv에서 실행)")
class RouteGuardTest(unittest.TestCase):
    def test_real_app_non_get_routes_require_csrf(self):
        seen = set()
        self.assertEqual(unguarded_routes(app.routes, seen=seen), [])
        # 감시가 실제 화면 라우트를 훑었는지(빈 앱에서 통과하는 것 방지)
        for path in ("/health", "/status", "/inbox", "/static/{name}"):
            self.assertIn(path, seen)

    def test_class_endpoint_without_methods_is_non_get(self):
        class Thing(HTTPEndpoint):
            async def get(self, request):
                return None

        a = FastAPI()
        a.router.routes.append(Route("/thing", Thing))
        self.assertIsNone(a.router.routes[-1].methods)
        self.assertEqual(unguarded_routes(a.routes), ["/thing"])

    def test_guard_detects_missing(self):
        a = FastAPI()

        @a.post("/open")
        async def open_():
            return {}

        @a.post("/ok", dependencies=[Depends(require_csrf)])
        async def ok():
            return {}

        async def nested(_=Depends(require_csrf)):
            return None

        @a.post("/nested")
        async def nested_ok(_=Depends(nested)):
            return {}

        sub = FastAPI()

        @sub.post("/inner")
        async def inner():
            return {}

        a.mount("/sub", sub)

        guarded, bare = APIRouter(), APIRouter()

        @guarded.post("/g")
        async def g():
            return {}

        @bare.post("/b")
        async def b():
            return {}

        a.include_router(guarded, prefix="/r", dependencies=[Depends(require_csrf)])
        a.include_router(bare, prefix="/r")
        self.assertEqual(unguarded_routes(a.routes), ["/open", "/sub/inner", "/r/b"])


if __name__ == "__main__":
    unittest.main()
