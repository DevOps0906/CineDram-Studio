"""R9~R15: 사람 승인·수정 요청·보관·취소·되돌리기 (plan-dev3 §2·§4). 로직은 server/approval_service.py."""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markupsafe import escape
from starlette.concurrency import run_in_threadpool

from server import approval_service as svc
from server.deps import get_db, read_form

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]


def _root(request):
    return Path(getattr(request.app.state, "root", ROOT))


def _error_page(status, message):
    """사람 말 오류 화면. 인라인 스타일·스크립트 없음(CSP), 입력값·경로를 되풀이하지 않는다."""
    body = "<br>".join(str(escape(line)) for line in message.splitlines())
    html = ("<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\"><title>처리하지 못했습니다 — CineDram Studio</title>"
            "<link rel=\"stylesheet\" href=\"/static/_tokens.css\"><link rel=\"stylesheet\" href=\"/static/_app.css\"></head>"
            f"<body><main class=\"main\" id=\"main\"><h1>처리하지 못했습니다</h1><p role=\"alert\">{body}</p>"
            "<p><a href=\"/status\">제작 현황</a> · <a href=\"/inbox\">승인함</a></p></main></body></html>")
    return HTMLResponse(html, status_code=status)


async def _run(request, conn, to, fn, *args, with_root=False, with_form=True):
    form = await read_form(request)
    call = [conn] + ([_root(request)] if with_root else []) + list(args) + ([form] if with_form else [])
    try:
        await run_in_threadpool(fn, *call)
    except svc.Decision as d:
        return _error_page(d.status, d.message)
    return RedirectResponse(to, status_code=303)


@router.post("/jobs/{job_id}/approve-plan")
async def approve_plan(job_id: int, request: Request, conn=Depends(get_db)):
    return await _run(request, conn, "/status", svc.approve_plan, job_id, with_root=True)


@router.post("/jobs/{job_id}/approve-video")
async def approve_video(job_id: int, request: Request, conn=Depends(get_db)):
    return await _run(request, conn, "/inbox", svc.approve_video, job_id, with_root=True)


@router.post("/jobs/{job_id}/request-change")
async def request_change(job_id: int, request: Request, conn=Depends(get_db)):
    return await _run(request, conn, "/inbox", svc.request_change, job_id)


@router.post("/jobs/{job_id}/rebuild")
async def rebuild(job_id: int, request: Request, conn=Depends(get_db)):
    return await _run(request, conn, "/status", svc.rebuild, job_id, with_root=True)


@router.post("/jobs/{job_id}/hold")
async def hold(job_id: int, request: Request, conn=Depends(get_db)):
    return await _run(request, conn, "/inbox", svc.hold, job_id)


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: int, request: Request, conn=Depends(get_db)):
    return await _run(request, conn, "/status", svc.cancel, job_id)


@router.post("/jobs/{job_id}/revert-plan")
async def revert_plan(job_id: int, request: Request, conn=Depends(get_db)):
    return await _run(request, conn, "/status", svc.revert_plan, job_id)
