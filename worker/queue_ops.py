"""대기 큐 사람 조작: 순서 변경·일시정지·재개·취소·재시도 (docs/14 §2 S-QU1·§4, docs/16 ADR-005).

표준 라이브러리만 쓴다. 웹 서버(사람 조작)가 부르는 함수이며 워커와는 SQLite 로만 통신한다.
- 각 조작은 BEGIN IMMEDIATE 안에서 조건부 UPDATE + job_events 기록을 한 트랜잭션으로 한다.
  바뀐 행이 1개가 아니면 되돌리고 InvalidTransition 을 올린다(동시 조작은 하나만 성공).
- 순서 변경은 priority 만 바꾼다. 일시정지 waiting→paused, 재개 paused→waiting(queue-flow.mmd).
- 취소: 대기 중(waiting·paused·held_*)은 즉시 finished(state=canceled).
  실행 중(running)도 같은 트랜잭션에서 canceled 로 바꾸고 임대를 지운다. 워커는 다음 임대 확인
  (heartbeat)에서 임대를 잃은 것을 보고 프로세스 트리를 종료한다(worker.run_engine).
  state=scheduled 는 플랫폼 예약 영상 원격 삭제가 필요해 사람 확인 흐름이 따로 필요하므로 여기서 거부한다.
- 재시도는 failed → queued(queue_state finished → waiting).
- approve·업로드는 여기 없다(사람만, 큐는 업로드하지 않는다).
"""
from worker.worker import NOW_SQL, _event, _Tx


class QueueOpError(Exception):
    pass


class JobNotFound(QueueOpError):
    pass


class InvalidTransition(QueueOpError):
    pass


# 큐가 다루지 않는 작업 상태: 원격 예약 삭제·업로드 진행은 사람 확인 흐름(docs/14 §4)
CANCEL_BLOCKED_STATES = ("scheduled", "uploading")
WAITING_LIKE = ("waiting", "paused", "held_human", "held_resource")


def _current(conn, job_id):
    row = conn.execute("SELECT queue_state, state, priority FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise JobNotFound(f"작업이 없습니다: {job_id}")
    return row


def _update(conn, job_id, sets, params, queue_state, state):
    """읽은 상태 그대로일 때만 바꾼다. 1행이 아니면 InvalidTransition(트랜잭션은 _Tx 가 되돌림)."""
    cur = conn.execute(
        f"UPDATE jobs SET {', '.join(sets)}, updated_at={NOW_SQL} "
        "WHERE id=? AND queue_state=? AND state=?", list(params) + [job_id, queue_state, state])
    if cur.rowcount != 1:
        raise InvalidTransition(f"작업 {job_id} 상태가 바뀌어 조작하지 못했습니다")


def reorder(conn, job_id, priority):
    """순서 변경: priority 만 바꾼다(클수록 먼저). 끝난 작업은 거부."""
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise QueueOpError(f"priority 는 정수여야 합니다: {priority!r}")
    with _Tx(conn):
        qs, st, old = _current(conn, job_id)
        if qs == "finished":
            raise InvalidTransition(f"끝난 작업은 순서를 바꿀 수 없습니다: {job_id}")
        _update(conn, job_id, ["priority=?"], [priority], qs, st)
        _event(conn, job_id, qs, qs, f"사람 조작: 순서 변경 priority {old}→{priority}")
    return qs


def pause(conn, job_id):
    """일시정지: waiting → paused(슬롯 비점유, 작업 상태 유지)."""
    with _Tx(conn):
        qs, st, _ = _current(conn, job_id)
        if qs != "waiting":
            raise InvalidTransition(f"대기(waiting) 작업만 일시정지할 수 있습니다: {job_id} ({qs})")
        _update(conn, job_id, ["queue_state='paused'"], [], qs, st)
        _event(conn, job_id, qs, "paused", "사람 조작: 일시정지")
    return "paused"


def resume(conn, job_id):
    """재개: paused → waiting."""
    with _Tx(conn):
        qs, st, _ = _current(conn, job_id)
        if qs != "paused":
            raise InvalidTransition(f"일시정지(paused) 작업만 재개할 수 있습니다: {job_id} ({qs})")
        _update(conn, job_id, ["queue_state='waiting'"], [], qs, st)
        _event(conn, job_id, qs, "waiting", "사람 조작: 재개")
    return "waiting"


def cancel(conn, job_id):
    """취소: 대기 중은 즉시, 실행 중은 임대를 지워 워커가 다음 임대 확인 때 프로세스 트리를 끝낸다."""
    with _Tx(conn):
        qs, st, _ = _current(conn, job_id)
        if st in CANCEL_BLOCKED_STATES:
            raise InvalidTransition(
                f"{st} 작업 취소는 원격 삭제가 필요해 사람 확인 후 따로 처리합니다: {job_id}")
        if qs not in WAITING_LIKE + ("running",):
            raise InvalidTransition(f"끝난 작업은 취소할 수 없습니다: {job_id} ({qs})")
        _update(conn, job_id,
                ["queue_state='finished'", "state='canceled'", "slot_id=NULL", "hold_reason=NULL",
                 "lease_owner=NULL", "lease_expires_at=NULL", "heartbeat_at=NULL", f"finished_at={NOW_SQL}"],
                [], qs, st)
        if qs == "running":
            msg = "사람 조작: 취소(실행 중), state→canceled — 워커가 임대 확인 때 프로세스 트리 종료"
        else:
            msg = "사람 조작: 취소, state→canceled"
        _event(conn, job_id, qs, "finished", msg)
    return "finished"


def retry(conn, job_id):
    """재시도: failed → queued(queue_state finished → waiting). 자동 재시도 횟수(attempts)는 0부터 다시."""
    with _Tx(conn):
        qs, st, _ = _current(conn, job_id)
        if qs != "finished" or st != "failed":
            raise InvalidTransition(f"실패(failed) 작업만 재시도할 수 있습니다: {job_id} ({qs}/{st})")
        _update(conn, job_id,
                ["queue_state='waiting'", "state='queued'", "attempts=0", "finished_at=NULL",
                 "hold_reason=NULL"], [], qs, st)
        _event(conn, job_id, qs, "waiting", "사람 조작: 재시도, state failed→queued")
    return "waiting"
