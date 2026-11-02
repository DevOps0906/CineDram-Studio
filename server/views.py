"""운영 화면: 제작 현황 /status, 승인함 /inbox(+ 영상 승인 POST) — Jinja2 서버 렌더(ADR-001).

조회는 읽기 전용 연결, 영상 승인만 쓰기 연결 한 트랜잭션(server/db.py). POST는 require_csrf 필수.
CSS는 시안 정본 design/wireframes/_tokens.css·_app.css 를 그대로 내보낸다(사본 없음).
"""
import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from server import db
from server.csrf import CookieConflict, cookie_pair, require_csrf
from worker import queue_ops

logger = logging.getLogger("server.views")

ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = Path(__file__).resolve().parent
RULES_DIR = ROOT / "rules"
STATIC_FILES = {
    "_tokens.css": ROOT / "design" / "wireframes" / "_tokens.css",
    "_app.css": ROOT / "design" / "wireframes" / "_app.css",
    "app.css": SERVER_DIR / "static" / "app.css",
    "app.js": SERVER_DIR / "static" / "app.js",
}
MEDIA_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}

router = APIRouter()
templates = Jinja2Templates(directory=str(SERVER_DIR / "templates"))

# 아이콘: design/wireframes/_app.js 스프라이트 중 이 화면들이 쓰는 것만(상수, 사용자 입력 아님).
ICONS = {
    "film": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
    "home": '<path d="M3 10l9-7 9 7v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
    "zap": '<path d="M13 2L3 14h9l-1 8 10-12h-9z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/>',
    "layers": '<path d="M12 2l10 5-10 5L2 7z"/><path d="M2 17l10 5 10-5M2 12l10 5 10-5"/>',
    "activity": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "inbox": '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"/>',
    "radar": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12l6-6"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M12 8v4M12 16h.01"/>',
    "tv": '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M10 9l5 3-5 3z"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "panel": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18"/>',
    "close": '<path d="M18 6L6 18M6 6l12 12"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "check": '<path d="M20 6L9 17l-5-5"/>',
    "warn": '<path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    "x": '<path d="M18 6L6 18M6 6l12 12"/>',
    "circlecheck": '<circle cx="12" cy="12" r="9"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
    "scaneye": '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="1.5"/><path d="M18.5 12s-2.5 4.5-6.5 4.5S5.5 12 5.5 12 8 7.5 12 7.5s6.5 4.5 6.5 4.5z"/>',
    "filex": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M9.5 12.5l5 5M14.5 12.5l-5 5"/>',
    "disk": '<path d="M22 12H2"/><path d="M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"/><path d="M6 16h.01M10 16h.01"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "play": '<path d="M6 4l14 8-14 8z"/>',
}


def icon(name, extra=""):
    cls = ("ico " + extra).strip()
    return Markup('<svg class="{}" viewBox="0 0 24 24" aria-hidden="true">{}</svg>').format(
        cls, Markup(ICONS.get(name, ""))
    )


templates.env.globals["icon"] = icon

STEPS = ("소재", "원본", "제작", "검수", "업로드")
# 상태 → (진행바 칸, 칸 꼬리표). docs/03: 기획 승인 대기는 제작 칸에서 멈춤.
STATE_STEP = {
    "queued": (0, "대기"),
    "topic": (0, None),
    "matching": (1, None),
    "need_source": (1, "막힘"),
    "building": (2, None),
    "blocked_disk": (2, "일시 정지"),
    "review": (3, "사람 확인"),
    "approved": (4, "예약 대기"),
    "scheduled": (4, "예약됨"),
    "uploading": (4, None),
    "blocked_auth": (4, "인증 필요"),
    "done": (4, "완료"),
    "canceled": (0, "취소"),
}
STATE_LABEL = {
    "queued": "대기", "topic": "소재 선정", "matching": "원본 확인", "need_source": "원본 없음",
    "building": "제작", "review": "검수", "approved": "승인됨", "scheduled": "업로드 예약",
    "uploading": "업로드 중", "blocked_auth": "계정 인증 필요", "done": "완료",
    "blocked_disk": "디스크 부족", "failed": "실패", "canceled": "취소",
}
# 상태 배지 5종(DESIGN.md §3.2): (문구, 클래스, 아이콘)
BADGE_OK = ("제작 가능", "b-ok", "circlecheck")
BADGE_REVIEW = ("업로드 검토 필요", "b-review", "scaneye")
BADGE_NOSRC = ("원본 없음", "b-nosrc", "filex")
BADGE_RIGHTS = ("권리 미확인", "b-rights", "shield")
BADGE_DISK = ("디스크 부족", "b-disk", "disk")


def get_db_path():
    return db.db_path_from_env()


@lru_cache(maxsize=None)
def rule_labels():
    """rules/<id>.md 첫 제목에서 구성 이름을 읽는다(구성 추가 = rules 파일 추가, 코드 수정 없음)."""
    labels = {}
    for path in sorted(RULES_DIR.glob("*.md")):
        try:
            first = path.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError, UnicodeDecodeError):
            continue
        name = re.split(r"\s+[—(]", first.lstrip("# ").strip(), maxsplit=1)[0].strip()
        if name:
            labels[path.stem] = name
    return labels


def _comp_labels(rule_set):
    try:
        ids = json.loads(rule_set or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(ids, list):
        return []
    labels = rule_labels()
    return [labels.get(str(i), str(i)) for i in ids if str(i) != "common"]


def _parse_ts(text):
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_elapsed(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, sec = divmod(rest, 60)
    if hours:
        return f"{hours}시간 {minutes}분"
    return f"{minutes}분 {sec}초"


def fmt_local(text):
    dt = _parse_ts(text)
    return dt.astimezone().strftime("%m-%d %H:%M") if dt else "—"


def _job_name(job):
    if not job["work_title"]:
        return f"작업 #{job['id']}"
    if job["episode"] is None:
        return job["work_title"]
    return f"{job['work_title']} · E{job['episode']:02d}"


def _unverified(job):
    # 소스가 없거나(None) 권리 확인 전이면 미확인으로 취급한다(R9-1, 업로드 경로 닫힘).
    return job["rights_status"] not in ("own", "licensed")


def _badge(job):
    state = job["state"]
    if state == "need_source":
        return BADGE_NOSRC
    if _unverified(job):
        return BADGE_RIGHTS
    if state == "blocked_disk":
        return BADGE_DISK
    if state == "review":
        return BADGE_REVIEW
    if state in ("failed", "canceled"):
        return None
    return BADGE_OK


def _progress(job):
    state = job["state"]
    if state == "failed":
        step = STATE_STEP.get(job["last_from_state"] or "", (0, None))[0]
        tail = "실패"
    else:
        step, tail = STATE_STEP.get(state, (0, None))
    items = []
    for i, name in enumerate(STEPS):
        kind = "done" if i < step else "now" if i == step else ""
        items.append({"name": f"{name} · {tail}" if kind == "now" and tail else name, "kind": kind})
    return items


def _channel_hint(channel_id):
    # 채널 참조 문자열(토큰 아님)도 화면에는 끝 6자만 보인다(시안 "ID …k3x7Qa").
    if not channel_id:
        return None
    return "…" + channel_id[-6:]


def _view_job(job, now):
    start = _parse_ts(job["started_at"]) or _parse_ts(job["created_at"])
    end = _parse_ts(job["finished_at"]) or now
    return {
        "id": job["id"],
        "state": job["state"],
        "state_label": STATE_LABEL.get(job["state"], job["state"]),
        "name": _job_name(job),
        "title": job["topic_title"],
        "comps": _comp_labels(job["rule_set"]),
        "elapsed": fmt_elapsed((end - start).total_seconds()) if start else "—",
        "started": fmt_local(job["started_at"] or job["created_at"]),
        "render_count": job["render_count"],
        "rights": job["rights_status"],
        "unverified": _unverified(job),
        "badge": _badge(job),
        "steps": _progress(job),
        "channel": _channel_hint(job["channel_id"]),
        "can_attach": queue_ops.can_attach(job.get("queue_state"), job.get("hold_reason")),
    }


def _view_events(events):
    return [
        {
            "label": STATE_LABEL.get(e["to_state"], e["to_state"]),
            "message": e["message"] or e["error"],
            "failed": e["to_state"] == "failed",
            "at": fmt_local(e["at"]),
        }
        for e in events
    ]


def _pick(jobs, job_id):
    for job in jobs:
        if job["id"] == job_id:
            return job
    return jobs[0] if jobs else None


def _nav_counts(counts):
    active = sum(counts.get(s, 0) for s in db.ACTIVE_STATES + db.REVIEW_STATES)
    return {"status": active, "inbox": sum(counts.get(s, 0) for s in db.REVIEW_STATES)}


def _render(request, name, context, status_code=200):
    # CSRF 토큰은 미들웨어가 scope state 에 둔다(다음 라운드 폼 hidden 입력용 자리).
    base = {"csrf_token": getattr(request.state, "csrf_token", ""), "nav_counts": None, "error": False,
            "picked": False}
    base.update(context)
    return templates.TemplateResponse(request, name, base, status_code=status_code)


@router.get("/status", response_class=HTMLResponse)
def status_page(request: Request, job: Optional[int] = None, db_path=Depends(get_db_path)):
    now = datetime.now(timezone.utc)
    try:
        conn = db.connect_ro(db_path)
        try:
            counts = db.state_counts(conn)
            active = [_view_job(j, now) for j in db.list_jobs(conn, db.ACTIVE_STATES)]
            review = [_view_job(j, now) for j in db.list_jobs(conn, db.REVIEW_STATES)]
            for item in active + review:
                item["events"] = _view_events(db.job_events(conn, item["id"]))
            selected = _pick(active + review, job)
            events = selected["events"] if selected else []
        finally:
            conn.close()
    except (db.DBUnavailable, sqlite3.Error) as exc:
        logger.warning("제작 현황 조회 실패: %s", exc)
        return _render(request, "status.html", {"page": "status", "error": True, "active": [], "review": [],
                                                "selected": None, "events": []}, status_code=503)
    return _render(request, "status.html", {
        "page": "status", "nav_counts": _nav_counts(counts), "active": active, "review": review,
        "selected": selected, "events": events, "picked": job is not None,
        "attach_choices": [(c, ATTACH_LABEL.get(c, c)) for c in queue_ops.ATTACH_COMMANDS],
    })


@router.get("/inbox", response_class=HTMLResponse)
def inbox_page(request: Request, tab: str = "pending", job: Optional[int] = None, db_path=Depends(get_db_path)):
    return _inbox_response(request, db_path, tab, job)


def _inbox_response(request, db_path, tab, job, notice=None, status_code=200):
    if tab not in ("pending", "approved"):
        tab = "pending"
    now = datetime.now(timezone.utc)
    try:
        conn = db.connect_ro(db_path)
        try:
            counts = db.state_counts(conn)
            states = db.REVIEW_STATES if tab == "pending" else db.APPROVED_STATES
            cards = [_view_job(j, now) for j in db.list_jobs(conn, states)]
            for card in cards:
                # 영상 승인 기록은 job_events(review→approved)가 정본. approvals 는 기획 승인 사본이라 쓰지 않는다.
                card["approvals"] = [
                    {"by": APPROVER, "at": fmt_local(e["at"])} for e in db.job_events(conn, card["id"])
                    if e["to_state"] == "approved" and (e["message"] or "").startswith(VIDEO_APPROVAL_PREFIX)
                ]
            selected = _pick(cards, job)
            events = _view_events(db.job_events(conn, selected["id"])) if selected else []
            output = db.latest_output(conn, selected["id"]) if selected else None
        finally:
            conn.close()
    except (db.DBUnavailable, sqlite3.Error) as exc:
        logger.warning("승인함 조회 실패: %s", exc)
        return _render(request, "inbox.html", {"page": "inbox", "error": True, "tab": tab, "cards": [],
                                               "tab_counts": None, "selected": None, "events": [],
                                               "output": None, "notice": notice}, status_code=503)
    tab_counts = {
        "pending": sum(counts.get(s, 0) for s in db.REVIEW_STATES),
        "approved": sum(counts.get(s, 0) for s in db.APPROVED_STATES),
    }
    return _render(request, "inbox.html", {
        "page": "inbox", "nav_counts": _nav_counts(counts), "tab": tab, "tab_counts": tab_counts,
        "cards": cards, "selected": selected, "events": events, "picked": job is not None, "notice": notice,
        "output": {"ver": output["render_ver"], "name": Path(output["file_path"]).name} if output else None,
    }, status_code=status_code)


# 컷표 지정 폼(POST /queue/{id}/plan, server/views_queue.py)의 엔진 명령 표기. approve 는 선택지에 없다.
ATTACH_LABEL = {"check": "점검 (check)", "recalc": "다시 계산 (recalc)", "render": "렌더 (render)"}


# ---------------------------------------------------------------- 영상 승인(review → approved, docs/01 §2)
# 엔진 approve(기획 승인, 기획승인.json)는 절대 부르지 않는다: 덮어쓰면 P0-2 우회가 된다.
APPROVER = "사용자"  # 엔진 approve --by 기본값과 같은 상수(사람 1인 로컬 도구)
APPROVABLE_RIGHTS = ("own", "licensed")
VIDEO_APPROVAL_PREFIX = "사람 영상 승인"
MSG_RIGHTS = "권리(rights_status)가 확인되지 않아 승인할 수 없습니다. 업로드 경로가 열리지 않습니다 (R9-1)."
MSG_STATE = "이미 처리됐거나 지금 승인할 수 없는 상태입니다(실행 중 포함). 새로고침 후 확인하세요."
MSG_MISSING = "작업을 찾을 수 없습니다."


class ApproveRejected(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _request_tag(request):
    """감사용 요청 표지: sid 쿠키의 sha256 앞 8자(쿠키 값 자체는 남기지 않는다)."""
    try:
        sid, _ = cookie_pair(request.scope.get("headers", []))
    except CookieConflict:
        sid = None
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()[:8] if sid else "없음"


def approve_job(conn, job_id, request_tag):
    """한 트랜잭션: 권리 확인 → 조건부 상태 전이 → job_events. 실패하면 전부 되돌린다.
    approvals 는 기획 승인(엔진 기획승인.json) 감사 사본이라 영상 승인은 넣지 않는다(0001·ADR-004)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT j.channel_id, s.rights_status FROM jobs j"
            " LEFT JOIN sources s ON s.id = j.source_id WHERE j.id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise ApproveRejected(404, MSG_MISSING)
        if row["rights_status"] not in APPROVABLE_RIGHTS:
            raise ApproveRejected(409, MSG_RIGHTS)
        cur = conn.execute(
            # 승인한 작업은 큐에서 뺀다(queue_state finished): 워커가 다시 집지 않게. 업로드는 큐 밖(docs/14 §4).
            "UPDATE jobs SET state = 'approved', queue_state = 'finished', hold_reason = NULL, slot_id = NULL,"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            " WHERE id = ? AND state = 'review' AND queue_state IS NOT 'running'",
            (job_id,),
        )
        if cur.rowcount != 1:
            raise ApproveRejected(409, MSG_STATE)
        channel = row["channel_id"][-6:] if row["channel_id"] else "없음"
        conn.execute(
            "INSERT INTO job_events(job_id, from_state, to_state, message) VALUES (?, 'review', 'approved', ?)",
            (job_id, f"{VIDEO_APPROVAL_PREFIX} · 채널 {channel} · 요청 {request_tag}"),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


@router.post("/inbox/{job_id}/approve", response_class=HTMLResponse, dependencies=[Depends(require_csrf)])
def inbox_approve(request: Request, job_id: int, db_path=Depends(get_db_path)):
    try:
        conn = db.connect_rw(db_path)
        try:
            approve_job(conn, job_id, _request_tag(request))
        finally:
            conn.close()
    except ApproveRejected as exc:
        # 거부 응답은 카드를 고르지 않는다(job=None): 없는 id·다른 탭 카드가 엉뚱하게 선택되지 않게.
        return _inbox_response(request, db_path, "pending", None, notice=exc.message, status_code=exc.status_code)
    except (db.DBUnavailable, sqlite3.Error) as exc:
        logger.warning("영상 승인 실패: %s", exc)
        return _inbox_response(request, db_path, "pending", None,
                               notice="승인을 저장하지 못했습니다. DB 연결을 확인하세요.", status_code=503)
    return RedirectResponse("/inbox", status_code=303)


@router.get("/static/{name}")
def static_file(name: str):
    path = STATIC_FILES.get(name)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=MEDIA_TYPES[path.suffix])
