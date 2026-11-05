"""관문 ①②: AI 소재 후보·제목 후보를 사람이 고르는 화면 (tasks/plan-dev6-2.md §4, worker/steps.py).

선택은 사람만 한다. 선택해도 기획 승인은 별도 단계(R9)다.
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from core import paths
from server.deps import get_db, read_form, templates
from server.routes.decisions import _error_page
from worker import steps

router = APIRouter()


def _root(request):
    return Path(getattr(request.app.state, "root", None) or paths.default_root())


def _job(conn, job_id):
    return conn.execute(
        "SELECT j.id, j.state, j.queue_state, j.hold_reason, j.plan_path, w.title FROM jobs j "
        "LEFT JOIN sources s ON s.id = j.source_id LEFT JOIN works w ON w.id = s.work_id WHERE j.id = ?",
        (job_id,)).fetchone()


def _flow(root, job_id):
    try:
        return json.loads((steps.job_dir(root, job_id) / steps.FLOW_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, paths.PathRejected):
        return None


@router.get("/gate/{job_id}")
def gate_page(job_id: int, request: Request, conn=Depends(get_db)):
    job = _job(conn, job_id)
    gate = None
    topics, flow = [], None
    if job is not None and job["state"] == "topic" and job["queue_state"] == "held_human" \
            and job["plan_path"] is None:
        if job["hold_reason"] == steps.GATE1_REASON:
            gate = 1
            topics = conn.execute("SELECT id, source_start, source_end, hook_line, tone_primary FROM topics "
                                  "WHERE job_id = ? ORDER BY id", (job_id,)).fetchall()
        elif job["hold_reason"] == steps.GATE2_REASON:
            gate = 2
            flow = _flow(_root(request), job_id)
    return templates.TemplateResponse(request, "gate.html", {
        "page": "status", "title": "관문 선택", "job": job, "gate": gate, "topics": topics, "flow": flow})


@router.post("/gate/{job_id}/topic")
async def choose_topic(job_id: int, request: Request, conn=Depends(get_db)):
    form = await read_form(request)
    raw = form.get("topic_id", "")
    ok = raw.isdigit() and len(raw) <= 12 and await run_in_threadpool(steps.choose_topic, conn, job_id, int(raw))
    if not ok:
        return _error_page(409, "지금은 소재를 고를 수 없는 상태입니다. 제작 현황에서 상태를 확인하세요.",
                           (f"/gate/{job_id}", "선택 화면"))
    return RedirectResponse("/status", status_code=303)


@router.post("/gate/{job_id}/flow")
async def choose_flow(job_id: int, request: Request, conn=Depends(get_db)):
    form = await read_form(request)
    raw = form.get("title_index", "")
    ok = raw.isdigit() and len(raw) <= 3 and await run_in_threadpool(
        steps.choose_flow, conn, job_id, _root(request), int(raw))
    if not ok:
        return _error_page(409, "지금은 제목을 고를 수 없는 상태입니다. 제작 현황에서 상태를 확인하세요.",
                           (f"/gate/{job_id}", "선택 화면"))
    return RedirectResponse("/status", status_code=303)
