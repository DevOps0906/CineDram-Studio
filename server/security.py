"""로컬 서버 보안 골격 (docs/16 ADR-003 1~7). 순수 ASGI 미들웨어 — CORS 없음, 로그인 없음.

앱 안에서 Host·Origin·메서드·CSRF를 모두 검사하므로 `--host 0.0.0.0` 으로 띄워도 허용 목록 밖은 거부된다.
"""
import hashlib
import hmac
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

from core import logmask

DEFAULT_PORT = 8765
LOOPBACK_NAMES = ("127.0.0.1", "localhost")
CSRF_COOKIE = "csrf"
CSRF_FIELD = "csrf_token"
CSRF_TTL_SECONDS = 12 * 3600
SAFE_METHODS = ("GET", "HEAD")
MAX_BODY_BYTES = 1_000_000  # POST 본문 상한(전 경로). 라우트별 상향은 개발 3에서.
SECURITY_HEADERS = (
    (b"cache-control", b"no-store"),
    (b"x-frame-options", b"DENY"),
    (b"x-content-type-options", b"nosniff"),
    (b"content-security-policy",
     b"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"),
)
_SECURITY_NAMES = {k for k, _ in SECURITY_HEADERS}


def install_log_masking():
    """LogRecord 팩토리 감싸기 — root·uvicorn.access·uvicorn.error(propagate 꺼짐 포함) 모두 적용된다."""
    logmask.install()


class CsrfSigner:
    """기동 시 만든 비밀로 서명한 토큰. 형식: `<난수>.<발급시각>.<hmac>`, TTL 이 지나면 무효."""

    def __init__(self, secret=None, ttl=CSRF_TTL_SECONDS):
        self._secret = secret or secrets.token_bytes(32)
        self._ttl = ttl

    def _sig(self, payload):
        return hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).hexdigest()

    def issue(self):
        payload = f"{secrets.token_urlsafe(24)}.{int(time.time())}"
        return f"{payload}.{self._sig(payload)}"

    def valid(self, token):
        if not isinstance(token, str) or token.count(".") != 2 or not token.isascii():
            return False
        nonce, ts, sig = token.split(".")
        if not hmac.compare_digest(sig, self._sig(f"{nonce}.{ts}")):
            return False
        try:
            age = time.time() - int(ts)
        except ValueError:
            return False
        return -60 <= age <= self._ttl


class BodyTooLarge(Exception):
    pass


def _header(scope, name):
    vals = [v for k, v in scope["headers"] if k == name]
    if len(vals) != 1:
        return None
    return vals[0].decode("latin-1")


async def _reject(send, status, text):
    body = text.encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                            (b"content-length", str(len(body)).encode()), *SECURITY_HEADERS]})
    await send({"type": "http.response.body", "body": body})


class SecurityMiddleware:
    def __init__(self, app, port=DEFAULT_PORT, signer=None, max_body=MAX_BODY_BYTES):
        self.app = app
        self.hosts = {f"{n}:{port}" for n in LOOPBACK_NAMES}
        self.origins = {f"http://{h}" for h in self.hosts}
        self.signer = signer or CsrfSigner()
        self.max_body = max_body

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.app(scope, receive, send)
        if scope["type"] != "http":  # websocket 등은 열지 않는다
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        host = _header(scope, b"host")
        if host is None or host.lower() not in self.hosts:
            return await _reject(send, 400, "허용되지 않은 Host")
        method = scope["method"]
        cookie = self._cookie_token(scope)
        started = False

        async def send_wrapped(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in _SECURITY_NAMES]
                headers.extend(SECURITY_HEADERS)
                if set_cookie:
                    headers.append((b"set-cookie", set_cookie))
                message = {**message, "headers": headers}
            await send(message)

        set_cookie = None
        try:
            if method not in SAFE_METHODS:
                if method != "POST":
                    return await _reject(send, 405, "상태 변경은 POST만 허용")
                if _header(scope, b"origin") not in self.origins:  # 없음·null·불일치 모두 거부
                    return await _reject(send, 403, "허용되지 않은 Origin")
                if not (cookie and self.signer.valid(cookie)):
                    return await _reject(send, 403, "CSRF 토큰 없음 또는 만료")
                declared = _header(scope, b"content-length")
                if declared is not None:
                    if not declared.isdigit():
                        return await _reject(send, 400, "Content-Length 오류")
                    if int(declared) > self.max_body:
                        return await _reject(send, 413, "본문이 너무 큼")
                receive = self._limited(receive)
                sent, receive = await self._submitted_token(scope, receive)
                if not (sent and self.signer.valid(sent) and hmac.compare_digest(sent, cookie)):
                    return await _reject(send, 403, "CSRF 토큰 불일치")
            token = cookie if cookie and self.signer.valid(cookie) else self.signer.issue()
            scope.setdefault("state", {})["csrf_token"] = token
            if token != cookie:
                set_cookie = f"{CSRF_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict".encode("latin-1")
            await self.app(scope, receive, send_wrapped)
        except BodyTooLarge:
            if started:
                raise
            await _reject(send, 413, "본문이 너무 큼")

    def _limited(self, receive):
        total = 0

        async def limited():
            nonlocal total
            msg = await receive()
            if msg["type"] == "http.request":
                total += len(msg.get("body", b""))
                if total > self.max_body:
                    raise BodyTooLarge()
            return msg

        return limited

    @staticmethod
    def _cookie_token(scope):
        raw = _header(scope, b"cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return None
        m = jar.get(CSRF_COOKIE)
        return m.value if m else None

    @staticmethod
    async def _submitted_token(scope, receive):
        """헤더 우선, 없으면 urlencoded 폼 필드. 본문을 읽었다면 되감아 앱에 넘긴다."""
        h = _header(scope, b"x-csrf-token")
        if h:
            return h, receive
        ctype = (_header(scope, b"content-type") or "").split(";")[0].strip().lower()
        if ctype != "application/x-www-form-urlencoded":
            return None, receive
        chunks = []
        while True:
            msg = await receive()  # 상한 초과 시 BodyTooLarge
            if msg["type"] != "http.request":
                break
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body"):
                break
        body = b"".join(chunks)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        vals = parse_qs(body.decode("utf-8", "replace")).get(CSRF_FIELD)
        return (vals[0] if vals else None), replay
