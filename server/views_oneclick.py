"""원클릭 제작 화면 GET·POST /make/oneclick (S-OC1, docs/13 §5·§9·§9-1·§16) — 방식·소재·권리·제작 모드 → 작업 생성.

입력 검증과 works·sources·jobs 삽입은 만들기 화면(server.views_create)의 함수를 그대로 쓴다.
내부 6단계 중 ①받아쓰기 ②대본·제목 ③장면 맞추기는 AI 단계가 없어 만들지 않고,
작업을 queue_state='held_human'(사람·스킬 입력 대기)으로 둔다. 관문 ①②·기획 승인·승인함·업로드 승인은
자동 통과가 없다(§9-1 안 A). 이 화면은 approve 를 부르지 않는다.
업로드 방식은 환경설정의 채널별 값(docs/14)이라 폼에서 받지 않고 jobs.upload_mode 기본값(검수 후)을 그대로 둔다.
"""
import logging
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from server import db, views, views_create
from server.csrf import require_csrf

logger = logging.getLogger("server.views_oneclick")

# 제작 모드 3종(docs/13 §16, 2026-11-02 사용자 확정): (jobs.exec_mode, 이름, 설명, 마지막 단계 명령)
MODES = (
    ("auto_render", "자동제작", "엔진이 완성본까지 만듭니다. 사람은 완성본만 확인합니다.", "render"),
    ("manual", "캡컷으로 편집", "캡컷 편집 묶음을 만들고 편집은 사람이 합니다. 처음 몇 편의 기준을 잡을 때 권장.", "handoff"),
    ("ai_assistant", "Aside 사용하기",
     "외부 AI 어시스턴트에 맡깁니다. 결과가 매번 달라 사람이 전부 검수합니다. 원본·자막이 외부 서비스로 나갑니다.",
     "delegate"),
)
MODE_VALUES = tuple(m[0] for m in MODES)
MODE_LABELS = {m[0]: m[1] for m in MODES}
DEFAULT_MODE = "auto_render"  # §16-1 기본값 = 자동제작
RECENT_LIMIT = 5
PATH = "/make/oneclick"  # docs/13 §5 S-OC1

# 버튼 뒤 내부 처리(§9). 지금 자동으로 도는 단계는 없다 — 사람·스킬 입력 대기로 보인다.
STAGES = (
    ("① 받아쓰기", "사람·스킬 입력 대기", "SRT를 직접 지정합니다(자동 받아쓰기 없음)."),
    ("② 대본·제목", "사람·스킬 입력 대기", "소재 3개 → 관문 ①, 흐름·제목 → 관문 ②. 자동 통과 없음."),
    ("③ 장면 맞추기", "사람·스킬 입력 대기", "컷표.json 작성(자동 생성 없음)."),
    ("④ 목소리·자막", "컷표 뒤 엔진 명령", "tts-plan → TTS → tts-measure → recalc. 자동 호출 없음."),
    ("★ 기획 승인", "사람만", "approve는 사람이 합니다."),
    ("⑤ 자동 검수", "엔진 check", "렌더 전 점검."),
    ("⑥ 렌더", "기획 승인 뒤", "제작 모드에 따라 render · handoff · delegate."),
)

router = APIRouter()


def _recent(db_path):
    """진행 중 작업 최근 순 몇 개(사이드 패널). list_jobs 순서에 기대지 않고 created_at·id 내림차순으로 고른다. 오류면 빈 목록."""
    try:
        conn = db.connect_ro(db_path)
        try:
            jobs = db.list_jobs(conn, db.ACTIVE_STATES + db.REVIEW_STATES)
        finally:
            conn.close()
    except (db.DBUnavailable, sqlite3.Error) as exc:
        logger.warning("원클릭 최근 기록 조회 실패: %s", exc)
        return []
    now = datetime.now(timezone.utc)
    jobs.sort(key=lambda j: (j["created_at"], j["id"]), reverse=True)
    return [views._view_job(j, now) for j in jobs[:RECENT_LIMIT]]


def _render(request, db_path, roots, form, errors, status_code=200, conflict=None):
    data = views_create._load_page_data(db_path)
    return views._render(request, "oneclick.html", {
        "page": "oneclick", "action": PATH, "error": data is None, "works": data["works"] if data else [],
        "nav_counts": data["nav_counts"] if data else None, "comps": views_create.compositions(),
        "rights": views_create.RIGHTS, "modes": MODES, "stages": STAGES,
        "recent": _recent(db_path) if data else [], "roots_ready": bool(roots), "form": form, "errors": errors,
        "conflict": conflict,
    }, status_code=503 if data is None and status_code == 200 else status_code)


@router.get(PATH, response_class=HTMLResponse)
def oneclick_page(request: Request, db_path=Depends(views.get_db_path), roots=Depends(views_create.get_roots)):
    form = {"work_title": "", "episode": "", "video_path": "", "srt_path": "", "rights_status": "", "comp": "",
            "exec_mode": DEFAULT_MODE}
    return _render(request, db_path, roots, form, {})


@router.post(PATH, response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
def oneclick_submit(
    request: Request,
    comp: str = Form(default=""),
    work_title: str = Form(default=""),
    episode: str = Form(default=""),
    video_path: str = Form(default=""),
    srt_path: str = Form(default=""),
    rights_status: str = Form(default=""),
    exec_mode: str = Form(default=""),
    db_path=Depends(views.get_db_path),
    roots=Depends(views_create.get_roots),
):
    form = {"work_title": work_title.strip(), "episode": episode.strip(), "video_path": video_path.strip(),
            "srt_path": srt_path.strip(), "rights_status": rights_status, "comp": comp, "exec_mode": exec_mode}
    clean, errors = views_create.validate_form(form, roots)
    if exec_mode not in MODE_VALUES:
        errors["exec_mode"] = "제작 모드를 고르세요."
    if errors:
        return _render(request, db_path, roots, form, errors, status_code=422)
    try:
        conn = db.connect_rw(db_path)
        try:
            job_id = views_create.insert_job(
                conn, clean, exec_mode=exec_mode,
                event_message=f"원클릭 제작 · 모드 {exec_mode} · {views_create.HOLD_REASON}")
            # 새 작업이면 요청 모드로 저장됐다. 다르면 중복 제출로 돌려받은 기존 작업이다.
            current = conn.execute("SELECT exec_mode FROM jobs WHERE id = ?", (job_id,)).fetchone()["exec_mode"]
        finally:
            conn.close()
    except (db.DBUnavailable, sqlite3.Error) as exc:
        logger.warning("원클릭 저장 실패: %s", exc)
        return _render(request, db_path, roots, form, {"_save": "저장하지 못했습니다. DB 연결을 확인하세요."},
                       status_code=503)
    if current != exec_mode:
        conflict = {"job_id": job_id, "mode": MODE_LABELS.get(current, current)}
        return _render(request, db_path, roots, form, {}, status_code=409, conflict=conflict)
    return RedirectResponse(f"/status?job={job_id}", status_code=303)
