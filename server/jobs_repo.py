"""jobs 조회와 상태 전이. 전이는 transition_from 한 함수로만(조건부 UPDATE + job_events, 한 트랜잭션)."""
from core.logmask import mask

# 5단계 진행바(소재·원본·제작·검수·업로드) 위치. 값 = 진행 중 칸 인덱스, 5 = 전부 완료. U2가 표를 확정한다.
STEPS = ("소재", "원본", "제작", "검수", "업로드")
STATE_STEP = {
    "queued": 0, "topic": 0, "matching": 1, "need_source": 1, "building": 2, "blocked_disk": 2,
    "review": 3, "approved": 4, "scheduled": 4, "uploading": 4, "blocked_auth": 4, "done": 5,
}
# transition_from 이 추가로 바꿀 수 있는 열(허용 목록)
_EXTRA_COLUMNS = ("hold_reason", "plan_hash", "tts_hash")  # slot_id 제외: 선점 슬롯을 쓰지 않는다(C11)
# C12 불변식: topic 의 queue_state 는 항상 held_human. R3(접수)·R11(review→topic)·R15(building→topic)는 이 값으로 넣는다.
TOPIC_QUEUE_STATE = "held_human"


def get_job(conn, job_id):
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(conn, states=None):
    sql = "SELECT * FROM jobs"
    args = ()
    if states:
        sql += f" WHERE state IN ({','.join('?' * len(states))})"
        args = tuple(states)
    return [dict(r) for r in conn.execute(sql + " ORDER BY created_at DESC, id DESC", args)]


def add_event(conn, job_id, from_state, to_state, message=None):
    conn.execute("INSERT INTO job_events (job_id, from_state, to_state, message) VALUES (?, ?, ?, ?)",
                 (job_id, from_state, to_state, mask(message) if message else None))


def transition_from(conn, job_id, expect_state, to_state, queue_state=None, message=None, *,
                    expect_queue_state=None, **extra):
    """`WHERE id=? AND state=?` 조건부 전이. 성공 True, 상태 불일치(이중 클릭 등) False.
    queue_state='finished' 면 finished_at 도 기록. extra 는 허용 열만(값 None = NULL).
    expect_queue_state(문자열 또는 튜플)가 주어지면 현재 queue_state 도 맞아야 한다(C13, 예: R14 는 running 제외).
    topic 으로 보낼 때는 queue_state=TOPIC_QUEUE_STATE 를 쓴다(C12).
    conn 은 isolation_level=None(자동 커밋)이어야 한다."""
    bad = set(extra) - set(_EXTRA_COLUMNS)
    if bad:
        raise ValueError(f"허용되지 않은 열: {sorted(bad)}")
    sets = ["state = ?", "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"]
    args = [to_state]
    if queue_state is not None:
        sets.append("queue_state = ?")
        args.append(queue_state)
        if queue_state == "finished":
            sets.append("finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')")
    for col, val in extra.items():
        sets.append(f"{col} = ?")
        args.append(val)
    where, wargs = "id = ? AND state = ?", [job_id, expect_state]
    if expect_queue_state is not None:
        eq = (expect_queue_state,) if isinstance(expect_queue_state, str) else tuple(expect_queue_state)
        where += f" AND queue_state IN ({','.join('?' * len(eq))})"
        wargs.extend(eq)
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE {where}", (*args, *wargs))
        if cur.rowcount != 1:
            conn.execute("ROLLBACK")
            return False
        add_event(conn, job_id, expect_state, to_state, message)
        conn.execute("COMMIT")
        return True
    except Exception:
        conn.execute("ROLLBACK")
        raise
