from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core import paths
from server.deps import STATIC_DIR
from server.routes import register_routes
from server.security import DEFAULT_PORT, SecurityMiddleware, install_log_masking

DEFAULT_DB_PATH = paths.DEFAULT_DB_PATH  # CINEDRAM_DB 미설정 시 기본값


class Health(BaseModel):
    status: str


def create_app(port=DEFAULT_PORT, db_path=None, root=None):
    """db_path 기본값은 CINEDRAM_DB(없으면 DEFAULT_DB_PATH), root 기본값은 paths.default_root()."""
    install_log_masking()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityMiddleware, port=port)
    app.state.db_path = Path(db_path) if db_path is not None else paths.db_path_from_env(DEFAULT_DB_PATH)
    app.state.root = Path(root) if root is not None else paths.default_root()
    # html=False: 디렉터리 목록·index 서빙 없음. 경로 탈출은 StaticFiles 가 거부한다.
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=False), name="static")

    @app.get("/healthz", response_model=Health)
    def healthz():
        return Health(status="ok")

    register_routes(app)
    return app
