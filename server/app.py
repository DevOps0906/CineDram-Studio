"""로컬 운영 서버 앱(개발 0단계: 빈 앱 + 보안 미들웨어 + /health).

API 문서(/docs·/redoc·/openapi.json)는 노출 면을 줄이려고 끈다. debug=False 고정.
"""
from fastapi import FastAPI
from pydantic import BaseModel

from server.middleware import LocalSecurityMiddleware

PORT = 8765  # 읽기 서버(docs/01 §1). 바인딩 주소는 security.BIND_HOST 고정.


class Health(BaseModel):
    status: str


def create_app(port=PORT):
    app = FastAPI(debug=False, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalSecurityMiddleware, port=port)

    @app.get("/health", response_model=Health)
    async def health():
        return Health(status="ok")

    return app


app = create_app()
