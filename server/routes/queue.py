"""대기 큐 (plan-dev5 §1). R16 `/queue`·`/queue/fragment` 읽기, R17 일시정지·R18 재개·R19 우선순위·R20 재시도 쓰기.
전이는 모두 expect_queue_state 조건부 UPDATE 로만 확정한다(C13). 취소(R14)는 approval_service.cancel(running 포함)."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from core.approval import ApprovalError, check_source_binding
from core.logmask import mask
from server import approval_service as svc
from server import jobs_repo
from server.deps import get_db, read_form, templates
from server.routes.decisions import _error_page as _page
from server.routes.status import STATE_VIEW, _parse_dt, fmt_elapsed

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]

PAGE_SIZE = 20
GPU_SLOTS = 1  # worker.GPU_SLOT 하나. LLM 단계는 아직 없다(Q3).
# (필터 id, 라벨, queue_state). 정렬 순서이기도 하다. fail 은 state=failed + queue_state=finished.
GROUPS = (("run", "실행 중", "running"), ("wait", "대기", "waiting"), ("human", "사람 대기", "held_human"),
          ("resource", "자원 대기", "held_resource"), ("pause", "일시정지", "paused"), ("fail", "실패", "finished"))
FILTER_IDS = tuple(g[0] for g in GROUPS)
EXEC_LABEL = {"auto_render": "자동 제작", "manual": "직접 편집", "ai_assistant": "Aside 사용하기"}
CANCEL_QS = ("waiting", "running", "held_human", "held_resource", "paused")
MOVABLE = ("waiting", "paused")
_RANK = "CASE j.queue_state WHEN 'running' THEN 0 WHEN 'waiting' THEN 1 WHEN 'held_human' THEN 2 " \
        "WHEN 'held_resource' THEN 3 WHEN 'paused' THEN 4 ELSE 5 END"
_VISIBLE = "(j.queue_state IN ('running','waiting','held_human','held_resource','paused') " \
           "OR (j.state = 'failed' AND j.queue_state = 'finished'))"
_GROUP_OF = {"running": "run", "waiting": "wait", "held_human": "human", "held_resource": "resource",
             "paused": "pause", "finished": "fail"}


def _like(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _where(flt, q):
    sql, args = [_VISIBLE], []
    if flt != "all":
        qs = next(g[2] for g in GROUPS if g[0] == flt)
        sql.append("j.queue_state = ?")
        args.append(qs)
    if q:
        sql.append("w.title LIKE ? ESCAPE '\\'")
        args.append(_like(q))
    return " AND ".join(sql), args


def _title(r):
    t = r["work_title"] or "작품 미정"
    return t + (f" · E{int(r['episode']):02d}" if r["episode"] is not None else "")


def _reason(raw):
    lines = [ln for ln in mask(raw or "").strip().splitlines() if ln.strip()]
    return lines[-1].strip()[-200:] if lines else "사유 기록 없음"


def build_queue(conn, flt="all", q="", page=1, now=None):
    now = now or datetime.now(timezone.utc)
    base = "FROM jobs j LEFT JOIN sources s ON s.id = j.source_id LEFT JOIN works w ON w.id = s.work_id"
    # 묶음 개수: 검색어는 적용하고 상태 필터는 적용하지 않는다(필터 칩의 숫자).
    wh, args = _where("all", q)
    counts = {g[0]: 0 for g in GROUPS}
    for r in conn.execute(f"SELECT j.queue_state AS qs, COUNT(*) AS n {base} WHERE {wh} GROUP BY j.queue_state", args):
        counts[_GROUP_OF[r["qs"]]] = r["n"]
    gpu_used = counts["run"]
    wh, args = _where(flt, q)
    limit = page * PAGE_SIZE
    rows = conn.execute(
        f"SELECT j.*, w.title AS work_title, s.episode AS episode, "
        "(SELECT COALESCE(e.error, e.message) FROM job_events e WHERE e.job_id = j.id AND e.to_state = 'failed' "
        " ORDER BY e.id DESC LIMIT 1) AS fail_reason "
        f"{base} WHERE {wh} ORDER BY {_RANK}, "
        "CASE WHEN j.queue_state = 'finished' THEN 0 ELSE j.priority END DESC, j.finished_at DESC, j.id LIMIT ?",
        (*args, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    # 대기 순번은 같은 묶음 안 표시 순서(우선순위 높은 순). 더 보기로 잘린 뒤쪽은 표시 안 됨.
    out = {g[0]: [] for g in GROUPS}
    for r in rows:
        job = dict(r)
        gid = _GROUP_OF[job["queue_state"]]
        start = _parse_dt(job["started_at"] if job["queue_state"] == "running" else job["created_at"])
        note = STATE_VIEW.get(job["state"], (0, ""))[1]
        try:
            rule_set = [x for x in json.loads(job["rule_set"] or "[]") if x != "common"]
        except ValueError:
            rule_set = []
        qs = job["queue_state"]
        out[gid].append({
            "id": job["id"], "title": _title(r), "comp": " · ".join(map(str, rule_set)) or None, "group": gid,
            "mode": EXEC_LABEL.get(job["exec_mode"], job["exec_mode"]), "note": note,
            "reason": job["hold_reason"] if qs != "finished" else _reason(job["fail_reason"]),
            "elapsed": fmt_elapsed((now - start).total_seconds()) if start else None,
            "order": len(out[gid]) + 1, "priority": job["priority"],
            "can_pause": qs == "waiting", "can_resume": qs == "paused", "can_move": qs in MOVABLE,
            "can_cancel": qs in CANCEL_QS and job["state"] in svc.CANCELABLE, "can_retry": qs == "finished",
            "running": qs == "running", "human_link": ("/inbox" if job["state"] == "review" else "/status")
            if qs == "held_human" else None,
        })
    sections = [{"id": g[0], "label": g[1], "count": counts[g[0]], "jobs": out[g[0]]} for g in GROUPS if out[g[0]]]
    return {"sections": sections, "counts": counts, "total": sum(counts.values()), "more": more,
            "slots": {"gpu_used": gpu_used, "gpu_total": GPU_SLOTS}}


def _ctx(request, conn, flt, q, page):
    data = build_queue(conn, flt, q, page)
    qs = {k: v for k, v in (("state", flt if flt != "all" else ""), ("q", q), ("page", page if page > 1 else "")) if v}
    data.update(csrf=request.state.csrf_token, flt=flt, q=q, pg=page, filters=[("all", "전체")] + [(g[0], g[1]) for g in GROUPS],
                fragment="/queue/fragment" + ("?" + urlencode(qs) if qs else ""),
                more_url="/queue?" + urlencode({**{k: v for k, v in qs.items() if k != "page"}, "page": page + 1}))
    return data


FILTER_Q = Query("all", pattern="^(all|" + "|".join(FILTER_IDS) + ")$")
SEARCH_Q = Query("", max_length=50)
PAGE_Q = Query(1, ge=1, le=50)


@router.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request, state: str = FILTER_Q, q: str = SEARCH_Q, page: int = PAGE_Q, conn=Depends(get_db)):
    q = q.strip()
    ctx = _ctx(request, conn, state, q, page)
    return templates.TemplateResponse(request, "queue.html", {**ctx, "page": "queue", "title": "대기 큐", "nav_counts": {}})


@router.get("/queue/fragment", response_class=HTMLResponse)
def queue_fragment(request: Request, state: str = FILTER_Q, q: str = SEARCH_Q, page: int = PAGE_Q, conn=Depends(get_db)):
    return templates.TemplateResponse(request, "_queue_list.html", _ctx(request, conn, state, q.strip(), page))


# ───────── 쓰기 (R17~R20) ─────────

def _need_confirm(form):
    svc._need_yes(form, "confirm")


def _load(conn, job_id):
    return svc._load(conn, job_id)


def pause(conn, job_id, form):
    _need_confirm(form)
    job = _load(conn, job_id)
    if not jobs_repo.transition_from(conn, job_id, job["state"], job["state"], queue_state="paused",
                                     expect_queue_state="waiting", message="일시정지"):
        raise svc.Decision(409, "대기 중인 작업만 일시정지할 수 있습니다.")


def resume(conn, job_id, form):
    _need_confirm(form)
    job = _load(conn, job_id)
    if not jobs_repo.transition_from(conn, job_id, job["state"], job["state"], queue_state="waiting",
                                     expect_queue_state="paused", message="재개"):
        raise svc.Decision(409, "일시정지된 작업만 재개할 수 있습니다.")


def priority(conn, job_id, form):
    """±1 은 `priority = priority + ?` 한 문장이라 동시 요청이 겹쳐도 합이 정확하다(읽고 쓰기 금지)."""
    _need_confirm(form)
    d = form.get("dir", "").strip()
    if d not in ("up", "down"):
        raise svc.Decision(422, "순서 방향 값이 올바르지 않습니다.")
    job = _load(conn, job_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "UPDATE jobs SET priority = priority + ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id = ? AND queue_state IN ('waiting','paused')", (1 if d == "up" else -1, job_id))
        if cur.rowcount != 1:
            conn.execute("ROLLBACK")
            raise svc.Decision(409, "대기·일시정지 작업만 순서를 바꿀 수 있습니다.")
        jobs_repo.add_event(conn, job_id, job["state"], job["state"], "순서 앞당김" if d == "up" else "순서 미룸")
        conn.execute("COMMIT")
    except svc.Decision:
        raise
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def retry(conn, root, job_id, form):
    """failed/finished → 승인 해시가 있으면 building/waiting, 없으면 topic/held_human(재승인). 승인 우회 없음 — 워커가 집을 때 다시 본다."""
    _need_confirm(form)
    job = _load(conn, job_id)
    if job["exec_mode"] == "ai_assistant":  # S2: 원본·자막이 외부 AI 로 나가므로 권리 미확인은 재시도 불가
        plan_file = svc._plan_file(Path(root), job)
        try:
            check_source_binding(conn, job_id, plan_file, Path(root), need_rights=True)
        except ApprovalError as e:
            raise svc.Decision(409, str(e)) from None
    if job["plan_hash"]:
        to_state, to_qs, msg, hold = "building", "waiting", "재시도", None
    else:
        to_state, to_qs, msg, hold = ("topic", jobs_repo.TOPIC_QUEUE_STATE,
                                      "재시도 — 승인 기록이 없어 기획 재승인 필요", "재시도 — 기획 승인 대기")
    # jobs_repo.transition_from 은 started_at·finished_at·slot_id 를 못 비우므로 이 경로에서 직접 조건부 UPDATE.
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "UPDATE jobs SET state=?, queue_state=?, hold_reason=?, slot_id=NULL, started_at=NULL, finished_at=NULL, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND state='failed' AND queue_state='finished'",
            (to_state, to_qs, hold, job_id))
        if cur.rowcount != 1:
            conn.execute("ROLLBACK")
            raise svc.Decision(409, "실패한 작업만 다시 시도할 수 있습니다.")
        jobs_repo.add_event(conn, job_id, "failed", to_state, msg)
        conn.execute("COMMIT")
    except svc.Decision:
        raise
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        raise svc.Decision(409, "같은 컷표로 진행 중인 다른 작업이 있어 다시 시작할 수 없습니다.") from None
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def _error_page(status, message):
    return _page(status, message, back=("/queue", "대기 큐"))


async def _run(request, conn, fn, job_id, with_root=False):
    form = await read_form(request)
    try:
        args = (conn, Path(getattr(request.app.state, "root", ROOT))) if with_root else (conn,)
        await run_in_threadpool(fn, *args, job_id, form)
    except svc.Decision as d:
        return _error_page(d.status, d.message)
    return RedirectResponse("/queue", status_code=303)


@router.post("/jobs/{job_id}/pause")
async def post_pause(job_id: int, request: Request, conn=Depends(get_db)):
    return await _run(request, conn, pause, job_id)


@router.post("/jobs/{job_id}/resume")
async def post_resume(job_id: int, request: Request, conn=Depends(get_db)):
    return await _run(request, conn, resume, job_id)


@router.post("/jobs/{job_id}/priority")
async def post_priority(job_id: int, request: Request, conn=Depends(get_db)):
    return await _run(request, conn, priority, job_id)


@router.post("/jobs/{job_id}/retry")
async def post_retry(job_id: int, request: Request, conn=Depends(get_db)):
    return await _run(request, conn, retry, job_id, with_root=True)
