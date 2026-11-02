"""ADR-003 4: 세션 CSRF — 쿠키 읽기·재발급 판단·FastAPI 의존성 require_csrf.

서명·검증 본체는 server/security.py(issue/verify_csrf_token). 비밀은 create_app이 만든
app.state.csrf_secret(메모리에만). 미들웨어가 요청마다 쿠키를 검사해 없음·무효·만료일 때만
재발급하고(열린 폼의 토큰을 지키려고 만료 전 갱신은 하지 않는다) scope["state"]["csrf_token"]에
유효 토큰을 둔다(화면이 hidden 필드에 넣는다). 같은 이름 쿠키가 2개 이상이면 CookieConflict.
상태 변경 라우트는 require_csrf로 제출 토큰(hidden csrf_token 또는 X-CSRF-Token)과
요청 쿠키를 대조한다. 실패는 403 "새로고침 후 다시 제출".
"""
import secrets
import time

from fastapi import HTTPException, Request
from starlette.datastructures import UploadFile

from server.security import (
    SecurityError,
    csrf_cookie_header,
    issue_csrf_token,
    verify_csrf_token,
)

SID_COOKIE = "cinedram_sid"
CSRF_COOKIE = "cinedram_csrf"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SID_MAX_LEN = 128

# 폼 상한. 전체 본문 크기는 미들웨어가 막는다(FastAPI가 의존성보다 먼저 폼을 파싱할 수 있어서).
MAX_BODY_BYTES = 10 * 1024 * 1024
MAX_FILES = 5
MAX_FIELDS = 200
MAX_PART_SIZE = 1024 * 1024

CSRF_FAILED = "새로고침 후 다시 제출해 주세요."
COOKIE_CONFLICT = "다른 로컬 앱 쿠키와 충돌했습니다. 브라우저에서 127.0.0.1·localhost 쿠키를 삭제한 뒤 새로고침해 주세요."
TOO_LARGE = "요청 본문이 너무 큽니다."
TOO_MANY_FILES = "파일이 너무 많습니다."
TOO_MANY_FIELDS = "폼 항목이 너무 많습니다."


class BodyTooLarge(HTTPException):
    """본문 상한 초과. HTTPException이라 FastAPI 본문 파싱·의존성 어디서 나도 413으로 나간다."""

    def __init__(self):
        super().__init__(status_code=413, detail=TOO_LARGE)


class CookieConflict(SecurityError):
    """같은 이름의 앱 쿠키가 2개 이상(다른 포트의 로컬 앱 등). 재발급으로 풀리지 않아 거부한다."""


def read_cookies(headers):
    """ASGI headers에서 쿠키 이름 → 값 목록. 같은 이름 2개 이상을 가려내려고 직접 나눈다."""
    found = {}
    for key, value in headers:
        if key != b"cookie":
            continue
        for part in value.decode("latin-1").split(";"):
            name, sep, val = part.strip().partition("=")
            if sep:
                found.setdefault(name.strip(), []).append(val.strip())
    return found


def _single(cookies, name):
    values = cookies.get(name, [])
    if len(values) > 1:
        raise CookieConflict(f"같은 이름 쿠키 {len(values)}개: {name}")
    return values[0] if values else None


def _valid_sid(sid):
    return bool(sid) and len(sid) <= SID_MAX_LEN and sid.isascii() and sid.replace("-", "").replace("_", "").isalnum()


def cookie_pair(headers):
    """요청 쿠키의 (sid, csrf). 없으면 None, 같은 이름이 2개 이상이면 CookieConflict."""
    cookies = read_cookies(headers)
    return _single(cookies, SID_COOKIE), _single(cookies, CSRF_COOKIE)


def ensure_token(secret, headers, now=None):
    """요청 쿠키가 유효하면 (토큰, []), 없음·무효·만료면 새 토큰과 Set-Cookie 헤더 목록.
    sid는 형식이 맞으면 유지한다. 쿠키 중복은 CookieConflict를 그대로 올린다."""
    now = time.time() if now is None else now
    sid, token = cookie_pair(headers)
    if _valid_sid(sid) and token:
        try:
            verify_csrf_token(secret, sid, token, token, now=now)
            return token, []
        except SecurityError:
            pass
    if not _valid_sid(sid):
        sid = secrets.token_urlsafe(32)
    token = issue_csrf_token(secret, sid, now=now)
    return token, [
        (b"set-cookie", csrf_cookie_header(sid, name=SID_COOKIE).encode("ascii")),
        (b"set-cookie", csrf_cookie_header(token, name=CSRF_COOKIE).encode("ascii")),
    ]


def _reject():
    raise HTTPException(status_code=403, detail=CSRF_FAILED)


async def require_csrf(request: Request):
    """상태 변경 라우트 의존성: 제출 토큰 + 요청 쿠키(sid·csrf 각 1개)를 서명 비밀로 검증."""
    try:
        sid, cookie_token = cookie_pair(request.scope.get("headers", []))
    except CookieConflict:
        _reject()
    submitted = request.headers.get(CSRF_HEADER)
    if submitted is None:
        ctype = request.headers.get("content-type", "").lower()
        if not ctype.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            _reject()
        form = await request.form(max_files=MAX_FILES, max_fields=MAX_FIELDS, max_part_size=MAX_PART_SIZE)
        # FastAPI가 기본 상한(1000)으로 먼저 파싱했을 수 있어 개수를 다시 센다.
        items = form.multi_items()
        if sum(isinstance(v, UploadFile) for _, v in items) > MAX_FILES:
            raise HTTPException(status_code=413, detail=TOO_MANY_FILES)
        if len(items) > MAX_FIELDS:
            raise HTTPException(status_code=413, detail=TOO_MANY_FIELDS)
        values = form.getlist(CSRF_FIELD)
        submitted = values[0] if len(values) == 1 and isinstance(values[0], str) else None
    try:
        verify_csrf_token(request.app.state.csrf_secret, sid, submitted, cookie_token)
    except SecurityError:
        _reject()
