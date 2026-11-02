"""승인함(S-IB, 개발 3 U3): R7 GET /inbox, R8 GET /jobs/{id}/video.

화면과 영상 스트리밍만 한다. 승인·수정 요청·다시 만들기·보관(R10~R13)의 POST 핸들러는 U4(decisions.py)."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from core import paths
from server.deps import get_db, templates

from server.approval_service import CHANGE_TARGETS  # K2: 칩 상수 정본

router = APIRouter()

ROOT = Path(__file__).resolve().parents[2]
HOLD_REASON = "보관(권리 미확인)"
RIGHTS_OK = ("own", "licensed")
MAX_LISTED_FILES = 30
CAPCUT_STEPS = ("미리보기 불러오기", "캔버스 9:16 크롭", "자막·민감 처리", "나레이션 TTS", "제목 텍스트", "내보내기")
TABS = (("review", "승인 대기"), ("approved", "승인됨"), ("held", "보관"))

_SELECT = (
    "SELECT j.*, s.rights_status AS rights, s.episode AS episode, w.title AS work_title "
    "FROM jobs j LEFT JOIN sources s ON s.id = j.source_id LEFT JOIN works w ON w.id = s.work_id "
    "WHERE j.state IN ('review', 'approved') ORDER BY j.updated_at DESC, j.id DESC")


def _root(request):
    return Path(getattr(request.app.state, "root", ROOT))


MAX_INSPECT_BYTES = 1_000_000  # 점검결과.json은 신뢰하지 않는 입력(plan-dev4 §6)


MAX_REASON = 200
INSPECT_LABELS = {"loudness": "음량", "true_peak": "트루피크", "silence": "무음", "first_sound": "첫 소리",
                  "black_frames": "검은 화면"}


def _clip(s):
    return s if len(s) <= MAX_REASON else s[:MAX_REASON] + "…"


def _count(v):
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError("count")
    return v


def inspect_for(conn, root, job):
    """점검결과.json(plan-dev4 §3) → {ok, warn, fail, fail_reason, auto_fixes}. 없음·깨짐·스키마 불일치·
    경로 이탈·plan_hash/size 불일치면 None(= '자동 점검 미실행')."""
    try:
        if not job["plan_path"]:
            return None
        folder = paths.resolve_in(root, job["plan_path"]).parent
        f = paths.resolve_in(folder, "점검결과.json")
        if not f.is_file() or f.stat().st_size > MAX_INSPECT_BYTES:
            return None
        data = json.loads(f.read_text(encoding="utf-8"))
        if data["schema"] != 1 or not job["plan_hash"] or data["plan_hash"] != job["plan_hash"]:
            return None
        out = data["output"]
        real = paths.resolve_in(folder, out["file"])
        if not real.is_file() or _count(out["size"]) != real.stat().st_size:
            return None
        row = _latest_output(conn, job)  # 재해시 없이 DB 기록과만 대조
        sha = out.get("sha256")
        if row is None or not sha or not isinstance(sha, str) or sha != row["sha256"]:
            return None
        s = data["summary"]
        fr = s.get("fail_reason")
        if fr is not None and not isinstance(fr, str):
            return None
        items = []
        for it in data.get("items") or []:
            if not isinstance(it, dict):
                return None
            lv = it.get("level")
            msg = it.get("msg")
            items.append({"label": INSPECT_LABELS.get(it.get("id"), "기타 점검"),
                          "level": lv if lv in ("ok", "warn", "fail") else "warn",
                          "msg": _clip(msg) if isinstance(msg, str) else ""})
        return {"ok": _count(s["ok"]), "warn": _count(s["warn"]), "fail": _count(s["fail"]),
                "fail_reason": _clip(fr) if fr is not None else None, "auto_fixes": _count(s.get("auto_fixes", 0)),
                "items": items}
    except (paths.PathRejected, KeyError, TypeError, ValueError, OSError, AttributeError):
        return None


def _elapsed(iso):
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    mins = max(0, int((datetime.now(timezone.utc) - t).total_seconds() // 60))
    return f"{mins // 60}시간 {mins % 60}분" if mins >= 60 else f"{mins}분"


def _local_time(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%m-%d %H:%M")
    except (AttributeError, ValueError):
        return ""


def _plan_title(root, plan_path):
    try:
        meta = json.loads(paths.resolve_in(root, plan_path).read_text(encoding="utf-8"))["meta"]
        t = meta.get("title")
        t = " ".join(str(x) for x in t) if isinstance(t, list) else str(t or "")
        return t.strip()
    except (paths.PathRejected, KeyError, TypeError, ValueError, OSError, AttributeError):
        return ""


def _latest_output(conn, job):
    return conn.execute("SELECT * FROM outputs WHERE job_id=? ORDER BY render_ver DESC, id DESC LIMIT 1",
                        (job["id"],)).fetchone()


def _output_path(root, job, out):
    """outputs.file_path(컷표 폴더 기준) 실경로. 경로 위반·루트 밖이면 None."""
    if not job["plan_path"] or out is None:
        return None
    try:
        plan_file = paths.resolve_in(root, job["plan_path"])
        return paths.resolve_in(plan_file.parent, out["file_path"])
    except paths.PathRejected:
        return None


def _output_view(root, job, out):
    if out is None:
        return None
    real = _output_path(root, job, out)
    view = {"id": out["id"], "sha256": out["sha256"] or "", "plan_hash": out["plan_hash"] or job["plan_hash"] or "",
            "kind": "missing", "files": []}
    if real is None:
        return view
    if real.is_file():
        view["kind"] = "video" if real.suffix.lower() == ".mp4" else "file"
        view["files"] = [real.name]
    elif real.is_dir():
        view["kind"] = "folder"
        view["files"] = sorted(f.relative_to(real).as_posix() for f in real.rglob("*") if f.is_file())[:MAX_LISTED_FILES]
    return view


def _approval_log(conn, job_id):
    rows = conn.execute("SELECT at, from_state, to_state, message FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT 20",
                        (job_id,)).fetchall()
    return [{"at": _local_time(r["at"]), "text": r["message"] or f"{r['from_state'] or '-'} → {r['to_state']}"} for r in rows]


def build_card(conn, root, job):
    try:
        rule_set = [x for x in json.loads(job["rule_set"] or "[]") if x != "common"]
    except (TypeError, ValueError):
        rule_set = []
    ep = job["episode"]
    held = (job["hold_reason"] or "").startswith("보관")
    card = {
        "id": job["id"], "state": job["state"], "kind": "approved" if job["state"] == "approved" else "review",
        "title": _plan_title(root, job["plan_path"]) or f"작업 #{job['id']}",
        "work": job["work_title"] or "작품 미지정", "episode": f"E{ep:02d}" if isinstance(ep, int) else "",
        "comp": " · ".join(rule_set) or None, "elapsed": _elapsed(job["updated_at"]),
        "rights": job["rights"], "rights_ok": job["rights"] in RIGHTS_OK,  # 권리 정본 = DB sources.rights_status
        "held": held, "busy": False, "channel": job["channel_id"], "plan_hash": job["plan_hash"] or "",
        "output": _output_view(root, job, _latest_output(conn, job)), "inspect": inspect_for(conn, root, job),
        "log": _approval_log(conn, job["id"]),
    }
    return card


@router.get("/inbox", response_class=HTMLResponse)
def inbox(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    root = _root(request)
    cards = [build_card(conn, root, dict(r)) for r in conn.execute(_SELECT)]
    groups = {
        "review": [c for c in cards if c["kind"] == "review" and not c["held"]],
        "approved": [c for c in cards if c["kind"] == "approved"],
        "held": [c for c in cards if c["kind"] == "review" and c["held"]],
    }
    tab = request.query_params.get("tab", "review")
    if tab not in groups:
        tab = "review"
    wanted = request.query_params.get("job", "")
    if wanted.isdigit():  # 다른 탭의 작업을 가리키면 그 탭으로 이동
        for name, group in groups.items():
            if any(c["id"] == int(wanted) for c in group):
                tab = name
    shown = groups[tab]
    selected = next((c for c in shown if str(c["id"]) == wanted), shown[0] if shown else None)
    ctx = {
        "page": "inbox", "title": "승인함", "has_aside": True, "aside_label": "점검 로그",
        "nav_counts": {"inbox": len(groups["review"])}, "tab": tab, "shown": shown, "selected": selected,
        "tabs": [(k, label, len(groups[k])) for k, label in TABS], "targets": CHANGE_TARGETS, "steps": CAPCUT_STEPS,
    }
    return templates.TemplateResponse(request, "inbox.html", ctx, headers={"Cache-Control": "no-store"})


@router.get("/jobs/{job_id}/video")
def job_video(job_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    row = conn.execute("SELECT id, plan_path FROM jobs WHERE id=?", (job_id,)).fetchone()
    out = _latest_output(conn, {"id": job_id}) if row else None
    real = _output_path(_root(request), dict(row), out) if row else None
    if real is None or real.suffix.lower() != ".mp4" or not real.is_file():
        raise HTTPException(status_code=404, detail="영상을 찾을 수 없습니다")
    return FileResponse(real, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})  # Range 는 Starlette 가 처리
