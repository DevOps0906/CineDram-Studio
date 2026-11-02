"""ADR-003 보안 골격 1~3·5·6을 거는 순수 ASGI 미들웨어.

검사 본체는 server/security.py 순수 함수이고, 여기서는 요청마다 순서대로 부르기만 한다.
1 scope server/client 루프백·포트 → 2 Host → 3 비GET은 POST+Origin
→ 5 응답 본문 키 검사(스트림·바이너리 명시 목록만 제외) + CORS 헤더 금지.
거부는 403 고정 문구, 처리 중 예외는 500 고정 문구. 로그는 mask_secrets를 거친다.
CSRF(4)는 개발 2단계에서 붙인다.
"""
import logging
import traceback
from collections.abc import Mapping

from server.security import (
    SecurityError,
    check_host,
    check_no_cors,
    check_request_method,
    check_response_body,
    mask_secrets,
    validate_bind_host,
)

logger = logging.getLogger("server.security")

LOOPBACK_CLIENTS = {"127.0.0.1"}
# 응답 키 검사는 기본. 아래 형식(스트림·바이너리)만 명시적으로 제외한다.
UNCHECKED_CONTENT_TYPES = (
    "text/event-stream",
    "application/octet-stream",
    "video/",
    "audio/",
    "image/",
)
FORBIDDEN_BODY = "요청이 거부되었습니다.".encode("utf-8")
ERROR_BODY = "서버 오류가 발생했습니다.".encode("utf-8")


def _header(scope, name):
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def _check_scope(scope, port):
    server = scope.get("server")
    if not server or server[1] != port:
        raise SecurityError(f"허용되지 않은 서버 주소: {server!r}")
    validate_bind_host(server[0])
    client = scope.get("client")
    if not client or client[0] not in LOOPBACK_CLIENTS:
        raise SecurityError(f"루프백이 아닌 클라이언트: {client!r}")
    check_host(_header(scope, b"host"), port)
    check_request_method(scope.get("method"), _header(scope, b"origin"), port)


def _is_checked(headers):
    for key, value in headers:
        if key.lower() == b"content-type":
            ctype = value.decode("latin-1").strip().lower()
            return not ctype.startswith(UNCHECKED_CONTENT_TYPES)
    return True


async def _send_plain(send, status, body):
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class MaskSecretsFilter(logging.Filter):
    """로그 레코드 본문·예외 추적에서 키 패턴을 가린다(ADR-003 6)."""

    def filter(self, record):
        # args 형태는 유지한다(uvicorn 접근 로그 포매터가 args 튜플을 직접 읽는다).
        if isinstance(record.msg, str):
            record.msg = mask_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(mask_secrets(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, Mapping):
            record.args = {k: mask_secrets(v) if isinstance(v, str) else v for k, v in record.args.items()}
        if record.exc_info:
            record.exc_text = mask_secrets("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


class LocalSecurityMiddleware:
    def __init__(self, app, port):
        self.app = app
        self.port = port

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # 웹소켓 등은 쓰지 않는다: 연결 수락 없이 닫는다.
            await receive()
            await send({"type": "websocket.close", "code": 1008})
            return

        try:
            _check_scope(scope, self.port)
        except SecurityError as exc:
            logger.warning("요청 거부: %s", mask_secrets(str(exc)))
            await _send_plain(send, 403, FORBIDDEN_BODY)
            return

        start = None
        chunks = []
        checked = False
        started = False

        async def guarded_send(message):
            nonlocal start, checked, started
            if message["type"] == "http.response.start":
                check_no_cors(name.decode("latin-1") for name, _ in message.get("headers", []))
                checked = _is_checked(message.get("headers", []))
                if checked:
                    start = message
                    return
                started = True
                await send(message)
                return
            if message["type"] == "http.response.body" and checked:
                chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                body = b"".join(chunks)
                check_response_body(body)
                started = True
                await send(start)
                await send({"type": "http.response.body", "body": body})
                return
            await send(message)

        try:
            await self.app(scope, receive, guarded_send)
        except SecurityError as exc:
            logger.warning("응답 거부: %s", mask_secrets(str(exc)))
            if not started:
                await _send_plain(send, 403, FORBIDDEN_BODY)
        except Exception:
            logger.error("처리 중 예외:\n%s", mask_secrets(traceback.format_exc()))
            if not started:
                await _send_plain(send, 500, ERROR_BODY)
