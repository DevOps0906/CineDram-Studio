"""번호 붙인 SQL 마이그레이션 적용기 (docs/16 ADR-004).

사용: python db/migrate.py <db경로>
- db/migrations/NNNN_*.sql 을 번호순으로 적용하고 schema_migrations 에 기록한다.
- 이미 적용된 파일의 체크섬이 바뀌면 중단한다(적용된 파일 수정 금지).
- 적용할 파일이 있고 DB 파일이 이미 있으면 적용 전에 복사본을 남긴다.
표준 라이브러리만 쓴다.
"""
import hashlib
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
FILE_RE = re.compile(r"^(\d{4})_[^\s]+\.sql$")
BUSY_TIMEOUT_MS = 5000


class MigrationError(Exception):
    pass


def connect(db_path):
    """외래 키·WAL·busy timeout 을 건 연결. isolation_level=None(트랜잭션은 직접 연다)."""
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def checksum(path):
    """줄바꿈(CRLF/LF) 차이는 무시한 sha256."""
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def list_migrations(migrations_dir=MIGRATIONS_DIR):
    found = {}
    for p in sorted(Path(migrations_dir).glob("*.sql")):
        m = FILE_RE.match(p.name)
        if not m:
            raise MigrationError(f"파일 이름 규칙(NNNN_요약.sql) 위반: {p.name}")
        version = m.group(1)
        if version in found:
            raise MigrationError(f"번호 중복: {found[version].name}, {p.name}")
        found[version] = p
    return [(v, found[v]) for v in sorted(found)]


def _ensure_table(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY,"
        " checksum TEXT NOT NULL,"
        " applied_at TEXT NOT NULL)"
    )


def migrate(db_path, migrations_dir=MIGRATIONS_DIR, backup=True):
    """적용한 버전 목록을 돌려준다. 체크섬 불일치·파일 누락이면 MigrationError."""
    db_path = Path(db_path)
    migrations = list_migrations(migrations_dir)
    existed = db_path.exists() and db_path.stat().st_size > 0
    conn = connect(db_path)
    try:
        _ensure_table(conn)
        applied = dict(conn.execute("SELECT version, checksum FROM schema_migrations"))
        files = dict(migrations)
        for version, saved in applied.items():
            if version not in files:
                raise MigrationError(f"적용된 마이그레이션 {version} 파일이 없습니다")
            if checksum(files[version]) != saved:
                raise MigrationError(
                    f"적용된 파일이 수정됨: {files[version].name} — 고치지 말고 새 번호 파일을 만드세요")
        pending = [(v, p) for v, p in migrations if v not in applied]
        if pending and existed and backup:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            shutil.copy2(db_path, db_path.with_name(f"{db_path.name}.pre-{pending[0][0]}-{stamp}.bak"))
        done = []
        for version, path in pending:
            sql = path.read_text(encoding="utf-8")
            digest = checksum(path)
            # version 은 숫자 4자리, digest 는 hex 라 문자열 삽입이 안전하다.
            script = (
                "BEGIN IMMEDIATE;\n" + sql + "\n;\n"
                "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES "
                f"('{version}', '{digest}', strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
                "COMMIT;\n"
            )
            try:
                conn.executescript(script)
            except sqlite3.Error as e:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise MigrationError(f"{path.name} 적용 실패: {e}") from e
            done.append(version)
        return done
    finally:
        conn.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) != 1:
        print("사용: python db/migrate.py <db경로>", file=sys.stderr)
        return 2
    try:
        done = migrate(argv[0])
    except MigrationError as e:
        print(f"중단: {e}", file=sys.stderr)
        return 1
    print(f"적용: {', '.join(done)}" if done else "적용할 마이그레이션 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
