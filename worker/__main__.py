"""워커 기동: python -m worker [--once]. 포트를 열지 않는다. 마이그레이션은 서버만 적용한다(D4)."""
import argparse
import importlib.util
import sys
import time
from pathlib import Path

from . import queue, runner
from .lock import AlreadyRunning, SingleInstance

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "var" / "studio.db"
LOCK_PATH = ROOT / "var" / "worker.lock"
POLL_SEC = 2.0


def _connect(db_path):
    spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.connect(db_path)


def run_once(conn, root=ROOT, timeout=runner.ENGINE_TIMEOUT):
    """대기 작업 1건 처리. 처리했으면 job id, 없으면 None."""
    job_id = queue.claim_next(conn)
    if job_id is None:
        return None
    runner.run_job(conn, job_id, root, timeout)
    return job_id


def main(argv=None, db_path=DB_PATH, lock_path=LOCK_PATH, root=ROOT):
    ap = argparse.ArgumentParser(prog="worker")
    ap.add_argument("--once", action="store_true", help="대기 작업을 모두 처리하고 종료")
    a = ap.parse_args(argv)
    if not Path(db_path).exists():
        print("기동 거부: DB가 없다 - 서버를 먼저 기동해 마이그레이션하세요", file=sys.stderr)
        return 1
    try:
        lock = SingleInstance(lock_path).acquire()
    except AlreadyRunning as e:
        print(f"기동 거부: {e}", file=sys.stderr)
        return 1
    try:
        conn = _connect(db_path)
        try:
            queue.check_schema(conn)
        except queue.SchemaTooOld as e:
            print(f"기동 거부: {e}", file=sys.stderr)
            return 1
        for job_id in queue.recover_running(conn):
            print(f"복구: 작업 {job_id} running -> waiting")
        while True:
            if run_once(conn, root) is None:
                if a.once:
                    return 0
                time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
