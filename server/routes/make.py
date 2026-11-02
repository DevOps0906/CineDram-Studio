"""R1~R4: 만들기·원클릭 폼과 작업 접수 (plan-dev3 §2). AI 단계는 범위 밖 — 사람·스킬이 만든 컷표 경로를 입력으로 받는다."""
import json
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from core import paths
from server import jobs_repo
from server.deps import get_db, read_form, templates

router = APIRouter()

# 허용 목록: 규칙 파일이 있는 방식만(구성 추가 = rules/ 파일 추가). (값=rules/<값>.md, 라벨, 선택 가능)
RULE_SETS = (
    ("drama", "한드짜", True), ("drama-first-person", "1인칭 한드짜", True),
    ("touching-reversal", "감동 반전", True), ("first-person", "1인칭 나레이션", True),
    ("variety", "예능", True), ("own-content", "자체 보유", True),
    ("person-fantube", "인물·팬튜브", False),  # 초안 규칙 — 준비 중
)
RULE_OK = {v for v, _, ok in RULE_SETS if ok}
EXEC_MODES = (
    ("auto_render", "자동제작", "엔진이 완성본까지 만듭니다. 사람은 완성본만 확인합니다."),
    ("manual", "캡컷으로 편집", "캡컷 편집 묶음을 만들고 편집은 사람이 합니다."),
    ("ai_assistant", "Aside 사용하기", "외부 AI 어시스턴트에 맡깁니다. 사람이 전부 검수하며 원본·자막이 외부로 나갑니다."),
)
TOPIC_MODES = (("new", "새 소재"), ("same", "같은 소재"), ("similar", "같은 결 다른 소재"), ("different", "다른 결"))
SPEEDS = (("review", "관문 멈춤", "관문 ①·②에서 사람이 고른 뒤 진행"), ("fast", "끝까지 자동", "1순위 후보로 진행, 승인함에서만 확인"))
CHANNEL_RE = re.compile(r"^[\w.@-]{0,64}$")
MAX_PLAN_PATH = 260


def _root(request):
    return Path(getattr(request.app.state, "root", None) or paths.default_root())


def _sources(conn, root):
    """작품·원본 목록. 파일이 없으면 has_file=False(원본 없음 → need_source로 접수)."""
    out = []
    rows = conn.execute(
        "SELECT s.id, s.episode, s.file_path, s.rights_status, w.title FROM sources s "
        "JOIN works w ON w.id = s.work_id ORDER BY w.title, s.episode, s.id").fetchall()
    for r in rows:
        out.append({"id": r["id"], "title": r["title"], "episode": r["episode"], "rights": r["rights_status"],
                    "has_file": _file_exists(root, r["file_path"])})
    return out


def _file_exists(root, rel):
    try:
        return paths.resolve_in(root, rel).is_file()
    except (paths.PathRejected, OSError):
        return False


def _options(sources):
    opts = []
    for s in sources:
        ep = f" · E{s['episode']:02d}" if s["episode"] is not None else ""
        tag = "" if s["has_file"] else " · 원본 없음"
        if s["rights"] not in ("own", "licensed"):
            tag += " · 권리 미확인"
        opts.append((s["id"], f"{s['title']}{ep}{tag}"))
    return opts


def _ctx(request, conn, values=None, errors=None, page="make", form_error=None):
    sources = _sources(conn, _root(request))
    v = {"source_id": "", "rule_set": "", "plan_path": "", "exec_mode": "auto_render", "topic_mode": "new",
         "speed": "review", "channel_id": ""}
    v.update(values or {})
    return {"page": page, "title": "원클릭 제작" if page == "oneclick" else "만들기", "values": v,
            "errors": errors or {}, "form_error": form_error, "sources": sources, "source_options": _options(sources),
            "rule_sets": RULE_SETS, "exec_modes": EXEC_MODES, "topic_modes": TOPIC_MODES, "speeds": SPEEDS,
            "src_rights": {s["id"]: s["rights"] for s in sources}}


def _page(request, conn, tpl, page, **kw):
    return templates.TemplateResponse(request, tpl, _ctx(request, conn, page=page, **kw))


@router.get("/make")
def make_form(request: Request, conn=Depends(get_db)):
    return _page(request, conn, "make.html", "make")


@router.get("/make/oneclick")
def oneclick_form(request: Request, conn=Depends(get_db)):
    return _page(request, conn, "oneclick.html", "oneclick")


def _validate(form, conn, root):
    """(값 dict, 오류 dict, 원본 행). 오류 메시지는 사람 말이고 입력값·경로를 되풀이하지 않는다."""
    errors = {}
    vals = {k: form.get(k, "").strip() for k in
            ("source_id", "rule_set", "plan_path", "exec_mode", "topic_mode", "speed", "channel_id")}
    vals["exec_mode"] = vals["exec_mode"] or "auto_render"
    src = None
    if not vals["source_id"].isdigit() or len(vals["source_id"]) > 12:
        errors["source_id"] = "원본을 골라 주세요."
    else:
        src = conn.execute("SELECT id, file_path, rights_status FROM sources WHERE id = ?",
                           (int(vals["source_id"]),)).fetchone()
        if src is None:
            errors["source_id"] = "등록되지 않은 원본입니다."
    if vals["rule_set"] not in RULE_OK:
        errors["rule_set"] = "고를 수 있는 방식이 아닙니다."
    if vals["exec_mode"] not in {m for m, _, _ in EXEC_MODES}:
        errors["exec_mode"] = "제작 모드를 다시 골라 주세요."
    if vals["topic_mode"] and vals["topic_mode"] not in {m for m, _ in TOPIC_MODES}:
        errors["topic_mode"] = "소재 모드를 다시 골라 주세요."
    if vals["speed"] and vals["speed"] not in {m for m, _, _ in SPEEDS}:
        errors["speed"] = "속도를 다시 골라 주세요."
    if not CHANNEL_RE.match(vals["channel_id"]):
        errors["channel_id"] = "채널 값은 영문·숫자·._@- 64자까지입니다."
    pp = vals["plan_path"]
    if not pp or len(pp) > MAX_PLAN_PATH:
        errors["plan_path"] = "컷표 경로를 작업 루트 기준 상대 경로로 적어 주세요."
    elif not pp.lower().endswith(".json"):
        errors["plan_path"] = "컷표(.json) 파일 경로여야 합니다."
    else:
        try:
            if not paths.resolve_in(root, pp).is_file():
                errors["plan_path"] = "컷표 파일을 찾을 수 없습니다."
        except paths.PathRejected:
            errors["plan_path"] = "작업 루트 안의 상대 경로만 쓸 수 있습니다."
    return vals, errors, src


async def _submit(request, conn, tpl, page):
    form = await read_form(request)
    root = _root(request)
    vals, errors, src = _validate(form, conn, root)
    if errors:
        return _rerender(request, conn, tpl, page, vals, errors, 422)
    try:
        job_id = _create_job(conn, vals, src, root)
    except sqlite3.IntegrityError:
        msg = "이미 진행 중인 작업이 이 컷표를 씁니다"
        errors["plan_path"] = msg
        return _rerender(request, conn, tpl, page, vals, errors, 422, form_error=msg)
    return RedirectResponse(f"/status#job-{job_id}", status_code=303)


def _rerender(request, conn, tpl, page, vals, errors, status, form_error=None):
    return templates.TemplateResponse(request, tpl, _ctx(request, conn, vals, errors, page, form_error),
                                      status_code=status)


def _create_job(conn, vals, src, root):
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "INSERT INTO jobs (source_id, rule_set, topic_mode, channel_id, speed, exec_mode, plan_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (src["id"], json.dumps(["common", vals["rule_set"]]), vals["topic_mode"] or None,
             vals["channel_id"] or None, vals["speed"] or None, vals["exec_mode"], vals["plan_path"]))
        job_id = cur.lastrowid
        jobs_repo.add_event(conn, job_id, None, "queued", "만들기 접수")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    jobs_repo.transition_from(conn, job_id, "queued", "matching", message="원본·자막 확인 중")
    if _file_exists(root, src["file_path"]):
        jobs_repo.transition_from(conn, job_id, "matching", "topic", queue_state="held_human",
                                  message="원본 확인 완료 — 기획 승인 대기", hold_reason="기획 승인 대기")
    else:
        jobs_repo.transition_from(conn, job_id, "matching", "need_source", queue_state="held_human",
                                  message="원본 파일 없음 — 원본이 들어오면 이어서 제작", hold_reason="원본 없음")
    return job_id


@router.post("/make")
async def make_submit(request: Request, conn=Depends(get_db)):
    return await _submit(request, conn, "make.html", "make")


@router.post("/make/oneclick")
async def oneclick_submit(request: Request, conn=Depends(get_db)):
    return await _submit(request, conn, "oneclick.html", "oneclick")
