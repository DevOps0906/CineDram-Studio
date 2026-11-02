"""대기 큐 화면 S-QU1(docs/14 §2·§4): GET /queue + 사람 조작 POST /queue/{id}/{op}.

조회는 connect_ro, 조작은 connect_rw 로 worker.queue_ops 를 부른다(워커와는 SQLite 로만 통신).
모든 POST 는 require_csrf. 성공은 303 → /queue, 거부(InvalidTransition)는 409, 없는 작업은 404 로
같은 화면에 사람이 읽을 안내를 띄운다. 업로드·approve 조작은 여기 없다(사람만, 큐는 업로드하지 않는다).
템플릿·아이콘·공용 표기는 server/views.py 것을 그대로 쓴다.
"""
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from server import db, views
from server.csrf import require_csrf
from worker import queue_ops
from worker.worker import GPU_SLOTS

logger = logging.getLogger("server.views_queue")

router = APIRouter()

# 이 화면이 더 쓰는 아이콘(design/wireframes/_app.js 스프라이트, 상수). 이미 있는 이름은 건드리지 않는다.
for _name, _svg in {
    "up": '<path d="M18 15l-6-6-6 6"/>',
    "down": '<path d="M6 9l6 6 6-6"/>',
    "top": '<path d="M17 11l-5-5-5 5M17 18l-5-5-5 5"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "gauge": '<path d="M12 14l4-4"/><path d="M3.3 19a10 10 0 1 1 17.4 0z"/>',
    "loader": '<path d="M21 12a9 9 0 1 1-6.2-8.6"/>',
}.items():
    views.ICONS.setdefault(_name, _svg)

# 화면 묶음(docs/14 §2 list: 실행 중·대기·사람 대기·자원 대기) + 재시도용 실패
GROUPS = (
    ("run", "실행 중"),
    ("wait", "대기"),
    ("human", "사람 대기"),
    ("resource", "자원 대기"),
    ("fail", "실패"),
)
GROUP_OF = {"running": "run", "waiting": "wait", "paused": "wait", "held_human": "human",
            "held_resource": "resource"}
QUEUE_LABEL = {"waiting": "대기", "running": "실행 중", "held_human": "사람 대기", "held_resource": "자원 대기",
               "paused": "일시정지", "finished": "큐에서 빠짐"}
# 큐 상태 배지: (문구, 클래스, 아이콘)
QUEUE_BADGE = {
    "running": ("실행 중", "b-review", "loader"),
    "waiting": ("대기", "b-nosrc", "clock"),
    "paused": ("일시정지", "b-comp", "pause"),
    "held_human": ("사람 대기", "b-review", "scaneye"),
    "held_resource": ("자원 대기", "b-disk", "gauge"),
    "failed": ("실패", "b-rights", "x"),
}
EXEC_LABEL = {"auto_render": "자동제작", "manual": "캡컷으로 편집", "ai_assistant": "Aside 사용하기"}
SLOT_LABEL = {"gpu": "렌더 슬롯", "llm": "AI 단계 슬롯"}
MOVES = queue_ops.MOVE_DIRECTIONS
OPS = ("reorder", "pause", "resume", "cancel", "retry")

# 조작 거부 안내(사람이 읽는 고정 문구). 내부 예외 문구는 로그에만 남긴다.
REJECTED = {
    "reorder": "끝난 작업이거나 상태가 바뀌어 순서를 바꾸지 못했습니다.",
    "pause": "대기 중인 작업만 일시정지할 수 있습니다. 상태가 바뀌었을 수 있습니다.",
    "resume": "일시정지된 작업만 재개할 수 있습니다. 상태가 바뀌었을 수 있습니다.",
    "cancel": "취소하지 못했습니다. 이미 끝난 작업이거나, 업로드 중·예약됨 작업은 원격 삭제 확인이 필요해 여기서 취소할 수 없습니다.",
    "retry": "실패한 작업만 재시도할 수 있습니다. 상태가 바뀌었을 수 있습니다.",
}
NOT_FOUND = "작업을 찾지 못했습니다. 이미 지워졌을 수 있습니다."
BAD_MOVE = "순서 변경 방향이 올바르지 않습니다."
UNKNOWN_OP = "알 수 없는 조작입니다."
DB_DOWN = "큐 상태를 불러오지 못했습니다. DB 연결을 확인한 뒤 다시 시도하세요."

QUEUE_SELECT = """
SELECT j.id, j.state, j.queue_state, j.priority, j.slot_kind, j.slot_id, j.hold_reason, j.exec_mode,
       j.topic_mode, j.rule_set, j.channel_id, j.created_at, j.started_at, j.finished_at,
       s.episode, s.rights_status, w.title AS work_title,
       (SELECT e.error FROM job_events e WHERE e.job_id = j.id AND e.error IS NOT NULL
         ORDER BY e.id DESC LIMIT 1) AS last_error
FROM jobs j
LEFT JOIN sources s ON s.id = j.source_id
LEFT JOIN works w ON w.id = s.work_id
WHERE j.queue_state != 'finished' OR j.state = 'failed'
ORDER BY j.priority DESC, j.id
"""


def _first_line(text):
    return (text or "").strip().splitlines()[0] if (text or "").strip() else None


def _view_row(job, now):
    qs, state = job["queue_state"], job["state"]
    failed = qs == "finished" and state == "failed"
    start = views._parse_ts(job["started_at"]) or views._parse_ts(job["created_at"])
    end = views._parse_ts(job["finished_at"]) or now
    return {
        "id": job["id"],
        "group": "fail" if failed else GROUP_OF.get(qs, "wait"),
        "queue_state": qs,
        "queue_label": QUEUE_LABEL.get(qs, qs),
        "state": state,
        "state_label": views.STATE_LABEL.get(state, state),
        "name": views._job_name(job),
        "comps": views._comp_labels(job["rule_set"]),
        "mode": EXEC_LABEL.get(job["exec_mode"], job["exec_mode"]),
        "topic_mode": job["topic_mode"],
        "channel": views._channel_hint(job["channel_id"]),
        "priority": job["priority"],
        "slot": SLOT_LABEL.get(job["slot_kind"], job["slot_kind"]),
        "slot_id": job["slot_id"],
        "hold_reason": job["hold_reason"],
        "fail_reason": _first_line(job["last_error"]) if failed else None,
        "elapsed": views.fmt_elapsed((end - start).total_seconds()) if start else "—",
        "unverified": views._unverified(job),
        "badge": QUEUE_BADGE["failed" if failed else qs] if (failed or qs in QUEUE_BADGE) else None,
        "can_cancel": not failed and state not in queue_ops.CANCEL_BLOCKED_STATES,
    }


def _view_events(events):
    return [
        {
            "label": QUEUE_LABEL.get(e["to_state"], views.STATE_LABEL.get(e["to_state"], e["to_state"])),
            "message": e["message"] or _first_line(e["error"]),
            "failed": bool(e["error"]),
            "at": views.fmt_local(e["at"]),
        }
        for e in events
    ]


def _slots(conn):
    used = {r[0]: r[1] for r in conn.execute(
        "SELECT slot_kind, COUNT(*) FROM jobs WHERE queue_state='running' GROUP BY slot_kind")}
    gpu_wait = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE queue_state='waiting' AND slot_kind='gpu'").fetchone()[0]
    gpu_used = used.get("gpu", 0)
    return {
        # LLM 슬롯 전체 수는 워커 기동 설정(--llm-slots)이라 웹 서버가 알 수 없다(docs/14 §4 기본값 미확인).
        "llm": {"used": used.get("llm", 0), "total": None},
        "gpu": {"used": gpu_used, "total": GPU_SLOTS, "wait": gpu_wait,
                "bars": [i < gpu_used for i in range(GPU_SLOTS)]},
    }


def _render_queue(request, db_path, job=None, notice=None, status_code=200):
    now = datetime.now(timezone.utc)
    try:
        conn = db.connect_ro(db_path)
        try:
            counts = db.state_counts(conn)
            rows = [_view_row(dict(r), now) for r in conn.execute(QUEUE_SELECT)]
            slots = _slots(conn)
            selected = views._pick(rows, job)
            events = _view_events(db.job_events(conn, selected["id"])) if selected else []
        finally:
            conn.close()
    except (db.DBUnavailable, sqlite3.Error) as exc:
        logger.warning("대기 큐 조회 실패: %s", exc)
        return views._render(request, "queue.html", {
            "page": "queue", "error": True, "notice": notice, "groups": [], "total": 0, "slots": None,
            "selected": None, "events": []}, status_code=503 if status_code < 400 else status_code)

    wait_ids = [r["id"] for r in rows if r["group"] == "wait"]
    for r in rows:
        if r["group"] == "wait":
            i = wait_ids.index(r["id"])
            r["wait_no"] = i + 1
            r["first"], r["last"] = i == 0, i == len(wait_ids) - 1
    groups = [{"key": k, "label": label, "rows": [r for r in rows if r["group"] == k]} for k, label in GROUPS]
    return views._render(request, "queue.html", {
        "page": "queue", "nav_counts": views._nav_counts(counts), "notice": notice, "groups": groups,
        "total": len(rows), "slots": slots, "selected": selected, "events": events, "picked": job is not None,
        "wait_total": len(wait_ids),
    }, status_code=status_code)


@router.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request, job: Optional[int] = None, db_path=Depends(views.get_db_path)):
    return _render_queue(request, db_path, job)


def _apply(conn, job_id, op, move):
    if op == "reorder":
        queue_ops.move(conn, job_id, move)
        return
    getattr(queue_ops, op)(conn, job_id)


@router.post("/queue/{job_id}/{op}", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
def queue_action(request: Request, job_id: int, op: str, move: str = Form(default=""),
                 db_path=Depends(views.get_db_path)):
    if op not in OPS:
        return _render_queue(request, db_path, notice=UNKNOWN_OP, status_code=404)
    if op == "reorder" and move not in MOVES:
        return _render_queue(request, db_path, job_id, notice=BAD_MOVE, status_code=400)
    try:
        conn = db.connect_rw(db_path)
    except db.DBUnavailable as exc:
        logger.warning("대기 큐 조작 실패(DB): %s", exc)
        return _render_queue(request, db_path, notice=DB_DOWN, status_code=503)
    try:
        _apply(conn, job_id, op, move)
    except queue_ops.JobNotFound:
        return _render_queue(request, db_path, notice=NOT_FOUND, status_code=404)
    except queue_ops.QueueOpError as exc:  # InvalidTransition 포함
        logger.info("대기 큐 조작 거부 %s #%s: %s", op, job_id, exc)
        return _render_queue(request, db_path, job_id, notice=REJECTED[op], status_code=409)
    except sqlite3.Error as exc:
        logger.warning("대기 큐 조작 실패 %s #%s: %s", op, job_id, exc)
        return _render_queue(request, db_path, job_id, notice=DB_DOWN, status_code=503)
    finally:
        conn.close()
    return RedirectResponse("/queue", status_code=303)
