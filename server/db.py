"""운영 화면용 SQLite 연결과 조회(ADR-004). 조회 함수는 읽기만 한다.

화면 조회는 connect_ro(URI `mode=ro` + `PRAGMA query_only` 두 겹으로 쓰기를 막는다).
connect_rw 는 다음 라운드 사람 조작(queue_ops)용 쓰기 연결이며 이번 화면에서는 쓰지 않는다.
두 연결 모두 db/migrate.py connect() 와 같은 busy_timeout·외래 키 설정을 건다(없는 DB는 만들지 않는다).
DB 경로는 환경 변수 CINEDRAM_DB(없으면 화면이 오류 상태를 보인다).
"""
import os
import sqlite3
from pathlib import Path

from db.migrate import BUSY_TIMEOUT_MS

DB_ENV = "CINEDRAM_DB"

# 제작 현황 묶음(docs/03 제작 현황 패널). done·canceled 는 보이지 않는다.
REVIEW_STATES = ("review",)
ACTIVE_STATES = (
    "queued", "topic", "matching", "need_source", "building", "blocked_disk", "failed",
    "approved", "scheduled", "uploading", "blocked_auth",
)
APPROVED_STATES = ("approved", "scheduled")


class DBUnavailable(Exception):
    pass


def db_path_from_env():
    return os.environ.get(DB_ENV) or None


def _open(db_path, mode):
    if not db_path:
        raise DBUnavailable("DB 경로가 설정되지 않았습니다")
    path = Path(db_path)
    if not path.is_file():
        raise DBUnavailable(f"DB 파일이 없습니다: {path}")
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + f"?mode={mode}", uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error as exc:
        raise DBUnavailable(f"DB를 열 수 없습니다: {exc}") from exc
    return conn


def connect_ro(db_path):
    conn = _open(db_path, "ro")
    conn.execute("PRAGMA query_only = ON")
    return conn


def connect_rw(db_path):
    """사람 조작(쓰기)용. isolation_level=None 이라 트랜잭션은 호출자가 직접 연다(migrate.connect 와 같음)."""
    conn = _open(db_path, "rw")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


JOB_SELECT = """
SELECT j.id, j.state, j.queue_state, j.hold_reason, j.rule_set, j.channel_id, j.created_at, j.started_at,
       j.finished_at,
       s.episode, s.rights_status, w.title AS work_title,
       (SELECT MAX(e.render_count) FROM job_events e WHERE e.job_id = j.id) AS render_count,
       (SELECT e.from_state FROM job_events e WHERE e.job_id = j.id ORDER BY e.id DESC LIMIT 1)
           AS last_from_state,
       (SELECT t.title FROM topics t WHERE t.job_id = j.id
         ORDER BY t.used DESC, t.score DESC, t.id LIMIT 1) AS topic_title
FROM jobs j
LEFT JOIN sources s ON s.id = j.source_id
LEFT JOIN works w ON w.id = s.work_id
"""


def list_jobs(conn, states):
    marks = ", ".join("?" for _ in states)
    sql = JOB_SELECT + f" WHERE j.state IN ({marks}) ORDER BY j.created_at, j.id"
    return [dict(r) for r in conn.execute(sql, tuple(states))]


def state_counts(conn):
    return {r["state"]: r["n"] for r in conn.execute("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")}


def job_events(conn, job_id, limit=50):
    rows = conn.execute(
        "SELECT at, from_state, to_state, message, render_count, error FROM job_events"
        " WHERE job_id = ? ORDER BY id LIMIT ?",
        (job_id, limit),
    )
    return [dict(r) for r in rows]


def latest_output(conn, job_id):
    row = conn.execute(
        "SELECT render_ver, file_path FROM outputs WHERE job_id = ? ORDER BY render_ver DESC, id DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    return dict(row) if row else None
