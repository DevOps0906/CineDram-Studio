"""제작 현황 (R5 `/status`, R6 `/status/fragment`). 읽기 전용. R9 폼·R15 버튼은 화면만 그리고 핸들러는 decisions.py(U4)."""
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from core import approval, paths
from core.logmask import mask
from server.deps import get_db, templates

router = APIRouter()

# 화면에 올리는 상태. done·failed·canceled 는 제작 현황 밖.
ACTIVE_STATES = ("queued", "matching", "need_source", "topic", "building", "blocked_disk", "review",
                 "approved", "scheduled", "uploading", "blocked_auth")
# 사람 승인을 기다리는 묶음: 기획 승인(topic) · 영상 승인(review)
WAIT_STATES = ("topic", "review")
# 상태 → (진행 중 칸 0~4 / 칸 옆 상태 문구). 칸: 소재·원본·제작·검수·업로드
STATE_VIEW = {
    "queued": (0, "대기 중"), "topic": (0, "기획 승인 대기"), "matching": (1, "원본 확인 중"),
    "need_source": (1, "막힘"), "building": (2, "제작 중"), "blocked_disk": (2, "일시 정지"),
    "review": (3, "사람 확인"), "approved": (4, "업로드 승인 대기"), "scheduled": (4, "예약됨"),
    "uploading": (4, "업로드 중"), "blocked_auth": (4, "막힘"),
}


def _parse_dt(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def fmt_elapsed(sec):
    sec = max(0, int(sec))
    if sec < 60:
        return f"{sec}초"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m}분 {s}초"
    h, m = divmod(m, 60)
    return f"{h}시간 {m}분"


def _secs(v):
    """'01:40.0'·'1:02:03'·숫자 → 초. 못 읽으면 None."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and re.fullmatch(r"\d+(:\d+){0,2}(\.\d+)?", v.strip()):
        t = 0.0
        for part in v.strip().split(":"):
            t = t * 60 + float(part)
        return t
    return None


def plan_summary(root, plan_path):
    """컷표 요약 {cuts, dialog, narration, span_sec, titles} 또는 None(못 읽음). 길이는 원본 구간 합계(음성 길이 반영 전 근사)."""
    try:
        data = json.loads(paths.resolve_in(root, plan_path).read_text(encoding="utf-8"))
        cuts = data["cuts"]
        titles = [str(t) for t in (data.get("meta", {}).get("title") or [])]
        span = 0.0
        for c in cuts:
            a = _secs(c.get("src_start"))
            b = _secs(c.get("src_end") if c.get("kind") == "dialog" else c.get("quiet_end"))
            if a is not None and b is not None and b >= a:
                span += b - a
        return {"cuts": len(cuts), "dialog": sum(c.get("kind") == "dialog" for c in cuts),
                "narration": sum(c.get("kind") == "narration" for c in cuts),
                "span_sec": round(span, 1), "titles": titles}
    except (paths.PathRejected, OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def current_plan_hash(root, plan_path):
    """R9 hidden `plan_hash`(C15): [기획 승인] 폼을 열 때만 gate 서브프로세스(core.approval.run_gate)로 계산한다.
    run_gate 는 미승인 컷표에서도 plan_hash 를 돌려주므로 통과 여부와 무관하게 해시만 쓴다. 못 구하면 None."""
    try:
        f = paths.resolve_in(root, plan_path)
    except paths.PathRejected:
        return None
    _ok, h, _reason = approval.run_gate(f)
    return h or None


def badge_kinds(state, rights, source_exists):
    """상태 배지 종류 목록(ok·review·nosrc·rights·disk). 상태 배지와 권리 미확인은 겹쳐 보인다."""
    kinds = []
    if state == "need_source":
        kinds.append("nosrc")
    elif state == "blocked_disk":
        kinds.append("disk")
    elif state == "review":
        kinds.append("review")
    if state != "need_source" and (not source_exists or rights not in approval.RIGHTS_OK):
        kinds.append("rights")
    return kinds or ["ok"]


def build_cards(conn, root, now=None, open_id=None):
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT j.*, w.title AS work_title, s.episode AS episode, s.rights_status AS rights, "
        "s.id AS src_id FROM jobs j LEFT JOIN sources s ON s.id = j.source_id "
        "LEFT JOIN works w ON w.id = s.work_id "
        f"WHERE j.state IN ({','.join('?' * len(ACTIVE_STATES))}) ORDER BY j.created_at DESC, j.id DESC",
        ACTIVE_STATES).fetchall()
    cards = []
    for r in rows:
        job = dict(r)
        step, note = STATE_VIEW[job["state"]]
        start = _parse_dt(job["created_at"])
        title = job["work_title"] or "작품 미정"
        if job["episode"] is not None:
            title += f" · E{int(job['episode']):02d}"
        try:
            rule_set = [x for x in json.loads(job["rule_set"] or "[]") if x != "common"]
        except ValueError:
            rule_set = []
        kinds = badge_kinds(job["state"], job["rights"], job["src_id"] is not None)
        card = {
            "id": job["id"], "state": job["state"], "title": title, "comp": " · ".join(map(str, rule_set)) or None,
            "step": step, "note": note, "elapsed": fmt_elapsed((now - start).total_seconds()) if start else None,
            "blocked": next((k for k in kinds if k in ("nosrc", "disk")), None),  # 진행 중 칸 옆 막힘 배지
            "badges": kinds, "hold_reason": job["hold_reason"],
            "can_revert": job["state"] == "building" and job["queue_state"] == "held_human",
            "plan_form": None,
        }
        # R9 폼은 topic + held_human 에만. 해시는 열린 카드 하나만 계산한다(폴링 목록에서는 계산 안 함).
        card["can_approve"] = job["state"] == "topic" and job["queue_state"] == "held_human"
        if card["can_approve"] and job["id"] == open_id:
            summary = plan_summary(root, job["plan_path"]) if job["plan_path"] else None
            h = current_plan_hash(root, job["plan_path"]) if summary else None
            card["plan_form"] = {"summary": summary, "plan_hash": h, "ok": bool(summary and h)}
        cards.append(card)
    return cards


def recent_failures(conn, limit=10):
    """K3: failed + finished 최근 limit 건. 사유는 job_events.error(없으면 message)의 마스킹한 꼬리."""
    out = []
    rows = conn.execute(
        "SELECT j.id, j.finished_at, w.title AS work_title, s.episode AS episode, "
        "(SELECT COALESCE(e.error, e.message) FROM job_events e WHERE e.job_id = j.id AND e.to_state = 'failed' "
        " ORDER BY e.id DESC LIMIT 1) AS reason "
        "FROM jobs j LEFT JOIN sources s ON s.id = j.source_id LEFT JOIN works w ON w.id = s.work_id "
        "WHERE j.state = 'failed' AND j.queue_state = 'finished' ORDER BY j.finished_at DESC, j.id DESC LIMIT ?",
        (limit,)).fetchall()
    for r in rows:
        title = r["work_title"] or "작품 미정"
        if r["episode"] is not None:
            title += f" · E{int(r['episode']):02d}"
        lines = [ln for ln in mask(r["reason"] or "").strip().splitlines() if ln.strip()]
        reason = lines[-1].strip()[-200:] if lines else "사유 기록 없음"
        out.append({"id": r["id"], "title": title, "reason": reason, "at": r["finished_at"]})
    return out


def _context(request, conn, open_id=None):
    root = getattr(request.app.state, "root", None) or paths.default_root()
    cards = build_cards(conn, root, open_id=open_id)
    waiting = [c for c in cards if c["state"] in WAIT_STATES]
    running = [c for c in cards if c["state"] not in WAIT_STATES]
    return {"running": running, "waiting": waiting, "failed": recent_failures(conn), "csrf": request.state.csrf_token}


@router.get("/status", response_class=HTMLResponse)
def status_page(request: Request, approve: int | None = None, conn=Depends(get_db)):
    ctx = _context(request, conn, open_id=approve)
    ctx.update(page="status", title="제작 현황",
               nav_counts={"inbox": sum(c["state"] == "review" for c in ctx["waiting"]),
                           "status": len(ctx["running"]) + len(ctx["waiting"])})
    return templates.TemplateResponse(request, "status.html", ctx)


@router.get("/status/fragment", response_class=HTMLResponse)
def status_fragment(request: Request, conn=Depends(get_db)):
    return templates.TemplateResponse(request, "_status_list.html", _context(request, conn))
