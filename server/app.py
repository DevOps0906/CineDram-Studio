from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server.deps import STATIC_DIR
from server.routes import register_routes
from server.security import DEFAULT_PORT, SecurityMiddleware, install_log_masking

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "var" / "studio.db"


class Health(BaseModel):
    status: str


def create_app(port=DEFAULT_PORT, db_path=DEFAULT_DB_PATH):
    install_log_masking()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityMiddleware, port=port)
    app.state.db_path = Path(db_path)
    # html=False: 디렉터리 목록·index 서빙 없음. 경로 탈출은 StaticFiles 가 거부한다.
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=False), name="static")

    @app.get("/healthz", response_model=Health)
    def healthz():
        return Health(status="ok")

    register_routes(app)
    return app
