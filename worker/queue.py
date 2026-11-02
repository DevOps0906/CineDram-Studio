"""큐 집기·상태 전이 (docs/01 §2, docs/14 §4). 전이와 job_events 는 한 트랜잭션.

연결은 db/migrate.py connect() 가 만든 isolation_level=None(자동 커밋) 연결을 가정한다.
"""
from contextlib import contextmanager

from core import paths

from . import GPU_SLOT, REQUIRED_SCHEMA

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"


class SchemaTooOld(RuntimeError):
    pass


def check_schema(conn, required=REQUIRED_SCHEMA):
    """D4: 서버가 적용한 최신 버전이 필요 버전 미만이면 SchemaTooOld."""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    except Exception as e:
        raise SchemaTooOld(f"스키마가 없다(서버를 먼저 기동해 마이그레이션 필요): {e}") from e
    latest = row[0] if row else None
    if latest is None or latest < required:
        raise SchemaTooOld(f"스키마 {latest} < 필요 {required} — 서버를 먼저 기동해 마이그레이션하세요")
    return latest


@contextmanager
def tx(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def set_plan_path(conn, root, job_id, rel):
    """컷표 상대 경로를 검사한 뒤 저장한다(절대·탈출 경로는 PathRejected)."""
    paths.resolve_in(root, rel)
    cur = conn.execute(f"UPDATE jobs SET plan_path=?, updated_at={NOW} WHERE id=?", (rel, job_id))
    if cur.rowcount != 1:
        raise KeyError(f"작업 {job_id} 없음")


def transition(conn, job_id, to_state, queue_state, message=None, error=None, hold_reason=None):
    """running 인 작업의 상태·큐 상태 전이 + job_events 기록(한 트랜잭션). 슬롯은 반납한다.

    실행 중 다른 곳(취소 등)이 상태를 바꿨으면(queue_state != running) 덮어쓰지 않고 이벤트만 남기고 False.
    """
    with tx(conn):
        row = conn.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        finished = ", finished_at=" + NOW if queue_state == "finished" else ""
        cur = conn.execute(
            f"UPDATE jobs SET state=?, queue_state=?, hold_reason=?, slot_id=NULL, "
            f"updated_at={NOW}{finished} WHERE id=? AND queue_state='running'",
            (to_state, queue_state, hold_reason, job_id))
        if cur.rowcount != 1:
            conn.execute(
                "INSERT INTO job_events(job_id, from_state, to_state, message, error) VALUES (?,?,?,?,?)",
                (job_id, row[0], row[0], f"전이 무시(실행 중 상태가 바뀜): {to_state} 시도", error))
            return False
        conn.execute(
            "INSERT INTO job_events(job_id, from_state, to_state, message, error) VALUES (?,?,?,?,?)",
            (job_id, row[0], to_state, message, error))
        return True


def claim_next(conn, slot=GPU_SLOT):
    """대기 중인 building 작업 1건을 집는다. 슬롯이 차 있거나 대기가 없으면 None.

    BEGIN IMMEDIATE 로 쓰기 잠금을 먼저 잡고, 조건부 UPDATE 로 waiting→running 을 원자적으로 바꾼다.
    """
    with tx(conn):
        cur = conn.execute(
            f"UPDATE jobs SET queue_state='running', slot_id=?, hold_reason=NULL, started_at={NOW}, updated_at={NOW} "
            "WHERE id=(SELECT id FROM jobs WHERE queue_state='waiting' AND state='building' "
            "          AND plan_path IS NOT NULL ORDER BY priority DESC, id LIMIT 1) "
            "AND queue_state='waiting' "
            "AND NOT EXISTS (SELECT 1 FROM jobs WHERE queue_state='running' AND slot_id=?)",
            (slot, slot))
        if cur.rowcount != 1:
            return None
        return conn.execute("SELECT id FROM jobs WHERE queue_state='running' AND slot_id=?",
                            (slot,)).fetchone()[0]


def recover_running(conn):
    """기동 시 남은 running 을 waiting 으로 되돌리고 job_events 에 남긴다. 복구한 id 목록."""
    with tx(conn):
        rows = conn.execute("SELECT id, state FROM jobs WHERE queue_state='running'").fetchall()
        for job_id, state in rows:
            conn.execute(
                f"UPDATE jobs SET queue_state='waiting', slot_id=NULL, updated_at={NOW} WHERE id=?",
                (job_id,))
            conn.execute(
                "INSERT INTO job_events(job_id, from_state, to_state, message) VALUES (?,?,?,?)",
                (job_id, state, state, "워커 재기동: running → waiting 복구"))
    return [r[0] for r in rows]
