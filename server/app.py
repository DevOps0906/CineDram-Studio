from fastapi import FastAPI
from pydantic import BaseModel

from server.security import DEFAULT_PORT, SecurityMiddleware, install_log_masking


class Health(BaseModel):
    status: str


def create_app(port=DEFAULT_PORT):
    install_log_masking()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityMiddleware, port=port)

    @app.get("/healthz", response_model=Health)
    def healthz():
        return Health(status="ok")

    return app
