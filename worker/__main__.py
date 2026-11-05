"""워커 기동: python -m worker [--once]. 포트를 열지 않는다. 마이그레이션은 서버만 적용한다(D4)."""
import argparse
import importlib.util
import sys
import time
from pathlib import Path

from core import llm, paths

from . import queue, runner, steps
from .lock import AlreadyRunning, SingleInstance

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = paths.DEFAULT_DB_PATH  # CINEDRAM_DB 미설정 시 기본값
LOCK_PATH = ROOT / "var" / "worker.lock"
POLL_SEC = 2.0


def _connect(db_path):
    spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.connect(db_path)


def run_once(conn, root=None, timeout=runner.ENGINE_TIMEOUT):
    """대기 작업 1건 처리. 처리했으면 job id, 없으면 None. root 기본값은 paths.default_root()."""
    root = root if root is not None else paths.default_root()
    job_id = queue.claim_next(conn)
    if job_id is not None:
        runner.run_job(conn, job_id, root, timeout)
        return job_id
    job_id = steps.claim_next_llm(conn)  # 렌더할 작업이 없을 때 AI 단계(컷표 없는 작업)
    if job_id is None:
        return None
    steps.run_step(conn, job_id, root)
    return job_id


def main(argv=None, db_path=None, lock_path=LOCK_PATH, root=None):
    ap = argparse.ArgumentParser(prog="worker")
    ap.add_argument("--once", action="store_true", help="대기 작업을 모두 처리하고 종료")
    a = ap.parse_args(argv)
    try:
        db_path = db_path if db_path is not None else paths.db_path_from_env(DB_PATH)
        root = root if root is not None else paths.default_root()
    except paths.PathRejected as e:
        print(f"기동 거부: {e}", file=sys.stderr)
        return 1
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
        stale = llm.recover_stale(conn)  # 죽은 워커가 남긴 started AI 호출 행 정리
        if stale:
            print(f"복구: AI 호출 {stale}건 started -> error")
        while True:
            if run_once(conn, root) is None:
                if a.once:
                    return 0
                time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        return 0
    except runner.EngineKillFailed as e:
        print(f"정지: 작업 {e} 엔진 종료 실패 - 수동 확인 필요", file=sys.stderr)
        return 3
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
