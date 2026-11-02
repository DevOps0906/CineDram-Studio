"""로컬 서버 기동: python -m server.run → http://127.0.0.1:8765

바인딩 주소는 security.validate_bind_host로 127.0.0.1만 허용(설정으로 바꿀 수 없다).
프록시 헤더를 믿지 않는다. 로그 마스킹 필터는 로거가 아니라 **핸들러**에 걸어
하위 로거에서 전파된 레코드도 가린다(루트·uvicorn·uvicorn.access 핸들러 전부).
"""
import copy

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from server.app import PORT
from server.security import BIND_HOST, validate_bind_host


def build_log_config():
    """uvicorn 기본 로그 설정 + 모든 핸들러에 MaskSecretsFilter + 루트 로거 핸들러."""
    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["mask_secrets"] = {"()": "server.middleware.MaskSecretsFilter"}
    for handler in config["handlers"].values():
        handler.setdefault("filters", []).append("mask_secrets")
    config["root"] = {"handlers": ["default"], "level": "INFO"}
    return config


def main():
    host = validate_bind_host(BIND_HOST)
    uvicorn.run(
        "server.app:app",
        host=host,
        port=PORT,
        proxy_headers=False,
        server_header=False,
        log_config=build_log_config(),
        log_level="info",
    )


if __name__ == "__main__":
    main()
