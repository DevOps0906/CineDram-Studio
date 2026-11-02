"""로컬 서버 기동: 127.0.0.1:8765 고정(설정으로 변경 불가). 기동 전 마이그레이션 적용(D4)."""
import importlib.util
import socket
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT = 8765
DB_PATH = ROOT / "var" / "studio.db"


_migrate = None


def _load_migrate():
    global _migrate
    if _migrate is not None:
        return _migrate
    spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _migrate = mod
    return mod


def prepare_db(db_path=DB_PATH):
    """마이그레이션 적용. 체크섬 불일치 등은 MigrationError 로 올려 기동을 거부한다."""
    mod = _load_migrate()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return mod.migrate(db_path)


def ensure_port_free(host=HOST, port=PORT):
    """이미 사용 중이면 OSError — 다른 프로세스가 8765를 선점한 채 뜨지 않게 한다."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
    finally:
        s.close()


def main():
    mod = _load_migrate()
    try:
        prepare_db()
    except mod.MigrationError as e:
        print(f"기동 거부: {e}", file=sys.stderr)
        return 1
    try:
        ensure_port_free()
    except OSError as e:
        print(f"기동 거부: {HOST}:{PORT} 사용 중 ({e})", file=sys.stderr)
        return 1
    from core import logmask
    from server.app import create_app
    logmask.install()
    uvicorn.run(create_app(), host=HOST, port=PORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
