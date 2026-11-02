"""로컬 운영 서버 앱(빈 앱 + 보안 미들웨어 + /health). 상태 변경 라우트는 csrf.require_csrf 필수.

API 문서(/docs·/redoc·/openapi.json)는 노출 면을 줄이려고 끈다. debug=False 고정.
"""
from fastapi import FastAPI
from pydantic import BaseModel

from server import views, views_create, views_oneclick, views_queue
from server.middleware import LocalSecurityMiddleware
from server.security import new_server_secret

PORT = 8765  # 읽기 서버(docs/01 §1). 바인딩 주소는 security.BIND_HOST 고정.


class Health(BaseModel):
    status: str


def create_app(port=PORT):
    app = FastAPI(debug=False, docs_url=None, redoc_url=None, openapi_url=None)
    # CSRF 서명 비밀: 기동마다 새로 만들고 메모리에만 둔다(재기동하면 기존 토큰 무효).
    app.state.csrf_secret = new_server_secret()
    app.add_middleware(LocalSecurityMiddleware, port=port, csrf_secret=app.state.csrf_secret)

    @app.get("/health", response_model=Health)
    async def health():
        return Health(status="ok")

    app.include_router(views.router)  # DB 경로는 views.get_db_path(CINEDRAM_DB)
    app.include_router(views_create.router)
    app.include_router(views_queue.router)
    app.include_router(views_oneclick.router)

    return app


app = create_app()
