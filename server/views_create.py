"""만들기 화면 GET·POST /create — 작품·회차·원본 경로·권리·구성을 받아 works·sources·jobs 를 만든다.

AI 단계(받아쓰기·소재·컷표 작성)는 아직 없어 사람·스킬 입력으로 대신한다(docs/13 §9, 개발 3 범위).
그래서 새 작업은 엔진 첫 명령 check·gpu 슬롯으로 만들되 컷표(plan_path)가 없으므로
queue_state='held_human'(컷표 작성 대기)으로 두어 워커가 집지 않게 한다.
경로 입력은 server.security.resolve_within 으로 작업 루트 안만 받는다(ADR-003 7).
작업 루트는 환경 변수 CINEDRAM_ROOTS(os.pathsep 구분, 첫 값이 상대 경로 기준). 없으면 제출을 막는다.
"""
import json
import logging
import os
import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from server import db, views
from server.csrf import require_csrf
from server.security import SecurityError, resolve_within

logger = logging.getLogger("server.views_create")

ROOTS_ENV = "CINEDRAM_ROOTS"
# sources.rights_status CHECK 값(0001). 기본값 없이 사람이 고른다.
RIGHTS = (
    ("own", "자체 보유", "내 채널·직접 촬영 원본"),
    ("licensed", "라이선스", "사용 허락을 받은 원본"),
    ("unverified", "미확인", "제작까지만 가능, 업로드 경로 닫힘 (R9-1)"),
)
RIGHTS_VALUES = tuple(r[0] for r in RIGHTS)
BASE_RULE = "common"  # 모든 구성이 얹히는 공통 규칙(구성 목록에는 안 보인다)
LAYER_PREFIX = "channel-"  # 구성 위에 얹는 채널 스타일 옵션 층(구성이 아니라 목록에서 뺀다)
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
MAX_EPISODE = 99999
DRAFT_MARKS = ("초안", "미확정", "정의 필요")  # rules 파일 머리 2줄에 있으면 고를 수 없다
HOLD_REASON = "컷표 작성 대기(사람·스킬)"
MAX_TEXT = 500

router = APIRouter()


def get_roots():
    raw = os.environ.get(ROOTS_ENV) or ""
    return [p for p in raw.split(os.pathsep) if p.strip()]


def compositions():
    """rules/*.md → [{id, name, disabled}]. 공통 규칙은 빼고, 초안·미확정 파일은 고를 수 없게 둔다."""
    items = []
    for rule_id, name in views.rule_labels().items():
        if rule_id == BASE_RULE or rule_id.startswith(LAYER_PREFIX):
            continue
        try:
            head = "\n".join((views.RULES_DIR / f"{rule_id}.md").read_text(encoding="utf-8").splitlines()[:2])
        except (OSError, UnicodeDecodeError):
            continue
        items.append({"id": rule_id, "name": name, "disabled": any(m in head for m in DRAFT_MARKS)})
    return items


def _load_page_data(db_path):
    """작품 목록(datalist)과 사이드바 숫자. DB 오류면 None."""
    try:
        conn = db.connect_ro(db_path)
        try:
            works = [r["title"] for r in conn.execute("SELECT title FROM works ORDER BY title")]
            counts = db.state_counts(conn)
        finally:
            conn.close()
    except (db.DBUnavailable, sqlite3.Error) as exc:
        logger.warning("만들기 화면 조회 실패: %s", exc)
        return None
    return {"works": works, "nav_counts": views._nav_counts(counts)}


def _render(request, db_path, roots, form, errors, status_code=200):
    data = _load_page_data(db_path)
    return views._render(request, "create.html", {
        "page": "create", "error": data is None, "works": data["works"] if data else [],
        "nav_counts": data["nav_counts"] if data else None, "comps": compositions(), "rights": RIGHTS,
        "roots_ready": bool(roots), "form": form, "errors": errors,
    }, status_code=503 if data is None and status_code == 200 else status_code)


def _check_path(raw, roots, suffixes):
    """(절대 경로 문자열, 오류 문구). 작업 루트 밖·없는 파일·확장자 불일치는 거부."""
    if not raw:
        return None, "경로를 입력하세요."
    if not roots:
        return None, "작업 루트가 설정되지 않아 경로를 확인할 수 없습니다."
    try:
        path = resolve_within(raw, roots)
    except SecurityError:
        return None, "작업 루트 안의 경로만 쓸 수 있습니다."
    if path.suffix.lower() not in suffixes:
        return None, f"{' '.join(suffixes)} 파일만 쓸 수 있습니다."
    if not path.is_file():
        return None, "파일이 없습니다."
    return str(path), None


def validate_form(form, roots):
    errors, clean = {}, {}
    title = form["work_title"]
    if not title:
        errors["work_title"] = "작품명을 입력하세요."
    elif len(title) > MAX_TEXT:
        errors["work_title"] = f"작품명은 {MAX_TEXT}자 이하로 입력하세요."
    clean["work_title"] = title

    episode = form["episode"]
    clean["episode"] = None
    if episode:
        if not episode.isascii() or not episode.isdigit() or len(episode) > 5 or not 1 <= int(episode) <= MAX_EPISODE:
            errors["episode"] = f"회차는 1~{MAX_EPISODE} 숫자로 입력하세요."
        else:
            clean["episode"] = int(episode)

    clean["video_path"], err = _check_path(form["video_path"], roots, VIDEO_SUFFIXES)
    if err:
        errors["video_path"] = err
    clean["srt_path"], err = _check_path(form["srt_path"], roots, (".srt",))
    if err:
        errors["srt_path"] = err

    if form["rights_status"] not in RIGHTS_VALUES:
        errors["rights_status"] = "권리 상태를 고르세요."
    clean["rights_status"] = form["rights_status"]

    enabled = {c["id"] for c in compositions() if not c["disabled"]}
    if form["comp"] not in enabled:
        errors["comp"] = "구성을 고르세요."
    clean["comp"] = form["comp"]
    return clean, errors


def insert_job(conn, v, exec_mode="auto_render", event_message=None):
    """works(같은 작품명은 재사용)·sources·jobs(exec_mode 포함)·첫 job_events 를 한 트랜잭션으로 만든다. job id 반환.

    event_message 가 없으면 만들기 화면 기본 문구를 첫 이벤트로 남긴다.

    같은 작품·회차·원본 영상의 작업이 이미 있으면(중복 제출) 새로 만들지 않고 그 job id 를 돌려준다.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT id FROM works WHERE title = ? ORDER BY id LIMIT 1", (v["work_title"],)).fetchone()
        if row:
            dup = conn.execute(
                "SELECT j.id FROM jobs j JOIN sources s ON s.id = j.source_id"
                " WHERE s.work_id = ? AND s.episode IS ? AND s.file_path = ? ORDER BY j.id LIMIT 1",
                (row["id"], v["episode"], v["video_path"]),
            ).fetchone()
            if dup:
                conn.execute("COMMIT")
                return dup["id"]
        work_id = row["id"] if row else conn.execute(
            "INSERT INTO works(title) VALUES (?)", (v["work_title"],)).lastrowid
        source_id = conn.execute(
            "INSERT INTO sources(work_id, episode, file_path, srt_path, origin, rights_status)"
            " VALUES (?, ?, ?, ?, 'local_file', ?)",
            (work_id, v["episode"], v["video_path"], v["srt_path"], v["rights_status"]),
        ).lastrowid
        job_id = conn.execute(
            "INSERT INTO jobs(source_id, rule_set, exec_mode, state, queue_state, hold_reason, engine_cmd, slot_kind)"
            " VALUES (?, ?, ?, 'queued', 'held_human', ?, 'check', 'gpu')",
            (source_id, json.dumps([BASE_RULE, v["comp"]]), exec_mode, HOLD_REASON),
        ).lastrowid
        conn.execute(
            "INSERT INTO job_events(job_id, from_state, to_state, message) VALUES (?, NULL, 'queued', ?)",
            (job_id, event_message or f"만들기 화면에서 생성 · {HOLD_REASON}"),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return job_id


@router.get("/create", response_class=HTMLResponse)
def create_page(request: Request, db_path=Depends(views.get_db_path), roots=Depends(get_roots)):
    form = {"work_title": "", "episode": "", "video_path": "", "srt_path": "", "rights_status": "", "comp": ""}
    return _render(request, db_path, roots, form, {})


@router.post("/create", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
def create_submit(
    request: Request,
    work_title: str = Form(default=""),
    episode: str = Form(default=""),
    video_path: str = Form(default=""),
    srt_path: str = Form(default=""),
    rights_status: str = Form(default=""),
    comp: str = Form(default=""),
    db_path=Depends(views.get_db_path),
    roots=Depends(get_roots),
):
    form = {"work_title": work_title.strip(), "episode": episode.strip(), "video_path": video_path.strip(),
            "srt_path": srt_path.strip(), "rights_status": rights_status, "comp": comp}
    clean, errors = validate_form(form, roots)
    if errors:
        return _render(request, db_path, roots, form, errors, status_code=422)
    try:
        conn = db.connect_rw(db_path)
        try:
            job_id = insert_job(conn, clean)
        finally:
            conn.close()
    except (db.DBUnavailable, sqlite3.Error) as exc:
        logger.warning("만들기 저장 실패: %s", exc)
        return _render(request, db_path, roots, form, {"_save": "저장하지 못했습니다. DB 연결을 확인하세요."},
                       status_code=503)
    return RedirectResponse(f"/status?job={job_id}", status_code=303)
