"""로컬 서버 보안 골격 (docs/16 ADR-003 1~7). 순수 ASGI 미들웨어 — CORS 없음, 로그인 없음.

앱 안에서 클라이언트 루프백·Host·Origin·메서드·CSRF(쿠키 중복 포함)·응답 본문 키 패턴을 모두 검사하므로 `--host 0.0.0.0` 으로 띄워도 허용 목록 밖은 거부된다.
"""
import hashlib
import hmac
import ipaddress
import logging
import re
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
COOKIE_CONFLICT = "다른 로컬 앱 쿠키와 충돌했습니다. 브라우저에서 127.0.0.1·localhost 쿠키를 삭제한 뒤 새로고침해 주세요."
# 응답 키 검사는 기본. 아래 형식(스트림·바이너리)만 명시적으로 제외한다.
UNCHECKED_CONTENT_TYPES = ("text/event-stream", "application/octet-stream", "video/", "audio/", "image/")

_B = r"(?<![A-Za-z0-9])"  # 앞 경계: "task-…", "disk-…" 같은 단어 중간 일치 방지
KEY_PATTERNS = [
    re.compile(_B + r"sk-ant-[A-Za-z0-9_\-]{16,}"),         # Anthropic
    re.compile(_B + r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),   # OpenAI 계열
    re.compile(_B + r"AIza[0-9A-Za-z_\-]{35}"),             # Google API 키
    re.compile(_B + r"GOCSPX-[A-Za-z0-9_\-]{20,}"),         # Google OAuth 클라이언트 비밀
    re.compile(_B + r"ya29\.[0-9A-Za-z_\-]{20,}"),          # Google OAuth 액세스 토큰
    re.compile(_B + r"1//[0-9A-Za-z_\-]{30,}"),             # Google OAuth 리프레시 토큰
    re.compile(_B + r"gh[pousr]_[A-Za-z0-9]{30,}"),         # GitHub
    re.compile(_B + r"bearer\s+[A-Za-z0-9._\-]{16,}", re.I),
    # 일반 `키=값`. 우리 CSRF 토큰(csrf_token·X-CSRF-Token)은 비밀이 아니므로 제외.
    re.compile(
        r"(?<!csrf[_-])(?:api[_-]?key|secret|token|password)[\"']?\s*[:=]\s*[\"']?"
        r"[A-Za-z0-9._\-/+]{12,}",
        re.I,
    ),
]

logger = logging.getLogger("server.security")


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


class CookieConflict(Exception):
    """같은 이름의 CSRF 쿠키가 2개 이상(다른 경로·다른 로컬 앱). 재발급으로 풀리지 않아 거부한다."""


class SecretInResponse(Exception):
    pass


def is_loopback_client(client):
    """ASGI scope client 가 루프백 IP 인지. 테스트도 예외 없이 루프백 client 를 넘긴다."""
    if not client:
        return False
    try:
        return ipaddress.ip_address(client[0]).is_loopback
    except ValueError:
        return False


def find_secrets(text):
    if not text:
        return []
    return [m.group(0) for pat in KEY_PATTERNS for m in pat.finditer(text)]


def check_response_body(body):
    """응답 본문(str/bytes)에 키 패턴이 있으면 SecretInResponse."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    hits = find_secrets(body)
    if hits:
        raise SecretInResponse(f"응답에 키 패턴 {len(hits)}건")


def _is_checked(headers):
    for k, v in headers:
        if k.lower() == b"content-type":
            return not v.decode("latin-1").strip().lower().startswith(UNCHECKED_CONTENT_TYPES)
    return True


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
        if not is_loopback_client(scope.get("client")):
            return await _reject(send, 403, "루프백이 아닌 클라이언트")
        host = _header(scope, b"host")
        if host is None or host.lower() not in self.hosts:
            return await _reject(send, 400, "허용되지 않은 Host")
        method = scope["method"]
        try:
            cookie = self._cookie_token(scope)
        except CookieConflict:
            return await _reject(send, 403, COOKIE_CONFLICT)
        started = False
        held = None  # 키 검사 대상 응답: 본문을 다 모아 검사한 뒤 보낸다
        chunks = []

        async def send_wrapped(message):
            nonlocal started, held
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in _SECURITY_NAMES]
                headers.extend(SECURITY_HEADERS)
                if set_cookie:
                    headers.append((b"set-cookie", set_cookie))
                message = {**message, "headers": headers}
                if _is_checked(headers):
                    held = message
                    return
            elif message["type"] == "http.response.body" and held is not None:
                chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                body = b"".join(chunks)
                check_response_body(body)
                started = True
                await send(held)
                await send({"type": "http.response.body", "body": body})
                return
            started = True
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
        except SecretInResponse as exc:
            logger.warning("응답 차단: %s", exc)
            if not started:
                await _reject(send, 403, "응답에 키 형태 값이 있어 차단")

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
        """CSRF 쿠키 값. 같은 이름이 2개 이상이면(여러 Cookie 헤더 포함) CookieConflict."""
        raws = [v.decode("latin-1") for k, v in scope["headers"] if k == b"cookie"]
        names = [p.strip().partition("=")[0].strip() for raw in raws for p in raw.split(";")]
        if names.count(CSRF_COOKIE) > 1:
            raise CookieConflict()
        if len(raws) != 1:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raws[0])
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
