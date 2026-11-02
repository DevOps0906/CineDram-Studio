"""라우트 자동 등록: 이 패키지의 각 모듈이 `router`(APIRouter)를 가지면 앱에 붙인다."""
import importlib
import pkgutil


def register_routes(app):
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        mod = importlib.import_module(f"{__name__}.{info.name}")
        router = getattr(mod, "router", None)
        if router is not None:
            app.include_router(router)
