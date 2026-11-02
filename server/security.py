"""ADR-003 로컬 서버 보안 골격 — 웹 프레임워크와 무관한 표준 라이브러리 순수 함수.

정본: docs/16-기술설계ADR.md#ADR-003 체크리스트, docs/14-환경설정과대기큐.md §5.
FastAPI 미들웨어가 이 함수들을 감싼다. 거부는 모두 SecurityError로 올린다.

다루는 항목: 1 바인딩 주소, 2 Host 허용 목록, 3 POST+Origin(CORS 닫힘),
4 CSRF 서명·검증·쿠키 속성, 5 응답 키 패턴 탐지, 6 로그 마스킹, 7 작업 루트 경로 제한.
(6의 환경 변수 허용 목록·7의 서브프로세스 인자 목록은 워커 담당.)
"""
import hashlib
import hmac
import re
import secrets
import time
from pathlib import Path


class SecurityError(Exception):
    """보안 검사 거부."""


# ---------------------------------------------------------------- 1. 바인딩
BIND_HOST = "127.0.0.1"


def validate_bind_host(host):
    """바인딩 주소는 127.0.0.1 고정. 설정으로 0.0.0.0·외부 주소 불가."""
    if host != BIND_HOST:
        raise SecurityError(f"바인딩 주소는 {BIND_HOST}만 허용: {host!r}")
    return host


# ---------------------------------------------------------------- 2. Host
def allowed_hosts(port):
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def check_host(host_header, port):
    """Host 헤더가 허용 목록(127.0.0.1:<포트>, localhost:<포트>)인지. DNS 리바인딩 방지."""
    if not host_header or host_header.strip().lower() not in allowed_hosts(port):
        raise SecurityError(f"허용되지 않은 Host: {host_header!r}")
    return host_header


# ---------------------------------------------------------------- 3. 메서드·Origin
SAFE_METHODS = {"GET", "HEAD"}
STATE_CHANGE_METHOD = "POST"


def allowed_origins(port):
    return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


def check_request_method(method, origin, port):
    """GET/HEAD는 통과, 상태 변경은 POST만 + Origin 허용 목록. 그 밖의 메서드는 거부."""
    m = (method or "").upper()
    if m in SAFE_METHODS:
        return m
    if m != STATE_CHANGE_METHOD:
        raise SecurityError(f"허용되지 않은 메서드: {method!r}")
    if not origin or origin.strip().lower() not in allowed_origins(port):
        raise SecurityError(f"허용되지 않은 Origin: {origin!r}")
    return m


def check_no_cors(response_headers):
    """CORS를 열지 않는다: 응답에 Access-Control-Allow-* 헤더가 있으면 거부."""
    for name in response_headers:
        if name.lower().startswith("access-control-allow-"):
            raise SecurityError(f"CORS 헤더 금지: {name}")
    return True


# ---------------------------------------------------------------- 4. CSRF
CSRF_COOKIE = "csrf_token"


def new_server_secret():
    """서버 기동 시 1회 만드는 서명 비밀(메모리에만 둔다)."""
    return secrets.token_bytes(32)


CSRF_MAX_AGE = 12 * 60 * 60  # 초. 발급 후 12시간 지나면 거부.


def _sign(secret, session_id, nonce, issued):
    msg = f"{session_id}.{nonce}.{issued}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest().encode("ascii")


def _ascii(value, what):
    try:
        return value.encode("ascii")
    except (UnicodeEncodeError, AttributeError):
        raise SecurityError(f"CSRF {what} 형식 오류") from None


def issue_csrf_token(secret, session_id, now=None):
    """세션에 묶인 CSRF 토큰 `<nonce>.<발급 시각(초)>.<hmac>`."""
    if not session_id:
        raise SecurityError("세션 없음")
    nonce = secrets.token_hex(16)
    issued = str(int(time.time() if now is None else now))
    return f"{nonce}.{issued}.{_sign(secret, session_id, nonce, issued).decode('ascii')}"


def verify_csrf_token(secret, session_id, token, cookie_token=None,
                      max_age=CSRF_MAX_AGE, now=None):
    """토큰·쿠키 없음, 형식 오류, 비ASCII, 서명 불일치, 다른 세션, 만료, 쿠키 값 불일치 → 거부."""
    if not session_id or not token or not isinstance(token, str):
        raise SecurityError("CSRF 토큰 없음")
    if not cookie_token or not isinstance(cookie_token, str):
        raise SecurityError("CSRF 쿠키 없음")
    token_b = _ascii(token, "토큰")
    cookie_b = _ascii(cookie_token, "쿠키")
    parts = token.split(".")
    if len(parts) != 3 or not all(parts) or not parts[1].isdigit():
        raise SecurityError("CSRF 토큰 형식 오류")
    nonce, issued, sig = parts
    if not hmac.compare_digest(sig.encode("ascii"), _sign(secret, session_id, nonce, issued)):
        raise SecurityError("CSRF 토큰 불일치")
    age = (time.time() if now is None else now) - int(issued)
    if age < 0 or age > max_age:
        raise SecurityError("CSRF 토큰 만료")
    if not hmac.compare_digest(cookie_b, token_b):
        raise SecurityError("CSRF 쿠키와 토큰 불일치")
    return True


def csrf_cookie_header(token, name=CSRF_COOKIE):
    """Set-Cookie 값. SameSite=Strict 고정, 스크립트 접근 차단."""
    return f"{name}={token}; Path=/; HttpOnly; SameSite=Strict"


# ---------------------------------------------------------------- 5·6. 키 패턴
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
MASK = "***"


def find_secrets(text):
    """문자열에서 키 패턴 일치 목록."""
    if not text:
        return []
    found = []
    for pat in KEY_PATTERNS:
        found.extend(m.group(0) for m in pat.finditer(text))
    return found


def check_response_body(body):
    """5: 응답 본문(str/bytes)에 키 패턴이 있으면 거부."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    hits = find_secrets(body)
    if hits:
        raise SecurityError(f"응답에 키 패턴 {len(hits)}건")
    return body


def mask_secrets(text):
    """6: 로그·오류문에서 키 패턴을 가린다."""
    if not text:
        return text
    for pat in KEY_PATTERNS:
        text = pat.sub(MASK, text)
    return text


# ---------------------------------------------------------------- 7. 경로 제한
def resolve_within(user_path, roots):
    """입력 경로를 정규화해 허용 루트(작업 루트·`원본/`) 안이면 절대 경로를 돌려준다.

    상대 경로는 첫 루트 기준. `..`·절대 경로·심볼릭 링크로 밖에 나가면 거부.
    """
    if not roots:
        raise SecurityError("허용 루트 없음")
    raw = str(user_path or "")
    if not raw or "\x00" in raw:
        raise SecurityError("빈 경로 또는 NUL 포함")
    # resolve()가 파일 시스템에 닿기 전에 막는다: UNC 접근은 NTLM 해시 유출 경로.
    if raw[:2] in ("\\\\", "//", "\\/", "/\\"):
        raise SecurityError(f"UNC·장치 경로 금지: {user_path!r}")
    resolved_roots = [Path(r).resolve() for r in roots]
    p = Path(raw)
    root_drives = {r.drive.lower() for r in resolved_roots}
    if p.drive and p.drive.lower() not in root_drives:
        raise SecurityError(f"허용 루트와 다른 드라이브: {user_path!r}")
    if not p.is_absolute():
        p = resolved_roots[0] / p
    p = p.resolve()
    for root in resolved_roots:
        if p == root or p.is_relative_to(root):
            return p
    raise SecurityError(f"허용 루트 밖 경로: {user_path!r}")
