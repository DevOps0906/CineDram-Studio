"""python -m worker --db <db경로> --root <작업 루트> [--root <원본 루트>] [--once]"""
import argparse
import logging
import sys

from worker.worker import Worker, WorkerError, migrate_mod


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="worker", description="포트 없는 큐 워커")
    ap.add_argument("--db", required=True, help="SQLite DB 경로")
    ap.add_argument("--root", action="append", required=True, help="컷표 허용 루트(여러 번 가능, 첫 값이 상대 경로 기준)")
    ap.add_argument("--timeout", type=float, default=3600, help="엔진 실행 시간 제한(초)")
    ap.add_argument("--llm-slots", type=int, default=1)
    ap.add_argument("--once", action="store_true", help="한 건만 처리하고 끝낸다")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    w = Worker(a.db, a.root, timeout=a.timeout, llm_slots=a.llm_slots)
    try:
        w.start()
    except (WorkerError, migrate_mod.MigrationError) as e:
        print(f"기동 거부: {e}", file=sys.stderr)
        return 1
    try:
        if a.once:
            job = w.run_once()
            print(f"처리: job {job['id']} 종료 코드 {job['returncode']}" if job else "대기 작업 없음")
        else:
            w.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        w.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
