"""승인 경로 서비스 (개발 3 U4, 위험 높음). 엔진 `approve` 서브프로세스 호출은 이 파일 한 곳뿐이다(보장 1).

R9(기획 승인)·R10(영상 승인)은 프로세스 안 잠금 하나로 직렬화하고(C11, DB 슬롯 선점 없음),
전이는 조건부 UPDATE 로만 확정한다. 서버는 단일 프로세스(workers=1) 전제.
모든 거부 사유는 사람 말이고 경로·엔진 전체 출력을 담지 않는다.
"""
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path

from core import paths, subproc
from core.approval import (RIGHTS_OK, ApprovalError, check_source_binding, hash_output, read_approval_file,
                           run_gate)
from core.logmask import mask
from server import jobs_repo

ENGINE = Path(__file__).resolve().parents[1] / "engine" / "shortsflow.py"
APPROVER = "사용자"  # 1인 도구 고정값. 요청 값을 받지 않는다.
APPROVE_TIMEOUT = 60
CHECK_COUNT = 6
YES = ("yes", "on")
NOTE_MAX = 500
LOCK_WAIT = 2
GATE_TIMEOUT = 20
# 수정 요청 칩(docs/13 §14 에서 이름이 확정된 것만. `미확인` 2개는 제외). U3 화면과 R11 이 이 상수 하나를 쓴다.
CHANGE_TARGETS = [("subtitle_sync", "자막 싱크"), ("title", "제목"), ("hook", "후킹 첫 장면"),
                  ("ending", "결말 잘림"), ("volume", "소리 크기"), ("bgm", "브금"), ("sfx", "효과음")]
CANCELABLE = ("queued", "matching", "need_source", "topic", "building", "review", "approved")

# R9·R10 직렬화 잠금(C11). 이중 클릭·동시 승인은 먼저 잡은 쪽이 상태를 바꾸고 뒤 쪽은 409 가 된다.
APPROVAL_LOCK = threading.Lock()


class _Locked:
    """APPROVAL_LOCK 을 2초만 기다린다. 못 잡으면 409(S3)."""

    def __enter__(self):
        if not APPROVAL_LOCK.acquire(timeout=LOCK_WAIT):
            raise Decision(409, "다른 승인 처리 중입니다. 잠시 뒤 다시 눌러 주세요.")

    def __exit__(self, *exc):
        APPROVAL_LOCK.release()


class Decision(Exception):
    """핸들러가 그대로 HTTP 응답으로 바꾸는 거부. status 는 404·409·422·500."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _need_yes(form, *names):
    missing = [n for n in names if form.get(n, "").strip().lower() not in YES]
    if missing:
        raise Decision(422, "확인 항목을 모두 체크해 주세요.")


def _load(conn, job_id):
    job = jobs_repo.get_job(conn, job_id)
    if job is None:
        raise Decision(404, "작업을 찾을 수 없습니다.")
    return job


def _plan_file(root, job):
    """컷표 실경로. 못 구하거나 파일이 없으면 409."""
    try:
        f = paths.resolve_in(root, job["plan_path"] or "")
    except (paths.PathRejected, TypeError, ValueError):
        raise Decision(409, "컷표 경로를 확인할 수 없습니다.") from None
    if not f.is_file():
        raise Decision(409, "컷표 파일을 찾을 수 없습니다.")
    return f


def _check_binding(conn, job, plan_file, root, need_rights):
    """R9·R10 공용: source_id·source_video 실경로·(R10) 권리를 서버가 다시 검사."""
    try:
        return check_source_binding(conn, job["id"], plan_file, root, need_rights=need_rights)[1]
    except ApprovalError as e:
        raise Decision(409, str(e)) from None


def _tail(text, root, n=5):
    """엔진 출력 꼬리 n줄(마스킹, 작업 루트 경로 제거)."""
    s = mask(text)
    for p in {str(root), str(Path(root).resolve())}:
        s = s.replace(p, "<루트>")
    lines = [ln for ln in s.splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def _log(root, job_id, text):
    try:
        d = Path(root) / "var" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (d / f"approve-{job_id}-{stamp}.log").write_text(mask(text), encoding="utf-8")
    except OSError:
        pass


# ───────── R9 ─────────


def approve_plan(conn, root, job_id, form):
    root = Path(root)
    _need_yes(form, "confirm", "gate_ok")
    shown_hash = form.get("plan_hash", "").strip()
    if not shown_hash:
        raise Decision(422, "화면의 컷표 해시가 없습니다. 화면을 새로 열어 다시 확인해 주세요.")
    with _Locked():
        job = _load(conn, job_id)
        if job["state"] != "topic" or job["queue_state"] != "held_human":
            raise Decision(409, "기획 승인을 기다리는 작업이 아닙니다.")
        plan_file = _plan_file(root, job)
        rights = _check_binding(conn, job, plan_file, root, need_rights=False)  # T3: 권리 미확인도 R9 는 허용
        if job["exec_mode"] == "ai_assistant" and rights not in RIGHTS_OK:  # S2: 원본·자막이 외부 AI 로 나간다
            raise Decision(409, "권리 미확인 원본은 외부 AI(Aside)에 맡기는 제작을 승인할 수 없습니다.")
        _ok, current, _why = run_gate(plan_file, timeout=GATE_TIMEOUT)
        if not current or current != shown_hash:
            raise Decision(409, "내용이 바뀌었습니다 — 다시 확인해 주세요.")
        _run_engine_approve(root, job_id, plan_file)
        # 파일이 정본. 비었거나 잘렸으면 미승인(C7)
        try:
            ap = read_approval_file(plan_file.parent)
        except ApprovalError as e:
            _log(root, job_id, str(e))
            raise Decision(500, "승인 기록을 확인하지 못했습니다. 다시 시도해 주세요.") from None
        if ap["plan_hash"] != shown_hash:
            _invalidate_approval(plan_file.parent)  # S1: 본 것 ≠ 승인한 것 — 유효한 승인 파일을 남기지 않는다
            raise Decision(409, "내용이 바뀌었습니다 — 다시 확인해 주세요.")
        try:
            _commit_plan_approval(conn, job_id, ap)
        except sqlite3.Error as e:
            # 파일 앞섬(C7): 자동 승격하지 않는다. 작업은 topic 에 남고 사람이 다시 누르면 된다.
            _log(root, job_id, f"DB 기록 실패: {e!r}")
            raise Decision(500, "승인 기록을 저장하지 못했습니다. 작업은 기획 승인 대기로 남아 있습니다. 다시 눌러 주세요.") from None


def _invalidate_approval(plan_dir):
    f = Path(plan_dir) / "기획승인.json"
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    try:
        f.replace(f.with_name(f"기획승인.무효-{stamp}.json"))
    except OSError:
        pass


def _run_engine_approve(root, job_id, plan_file):
    args = [sys.executable, str(ENGINE), "approve", str(plan_file), "--by", APPROVER]
    r = subproc.run(args, timeout=APPROVE_TIMEOUT)
    if r.timed_out or r.returncode not in (0, 1):
        _log(root, job_id, f"approve 실패 rc={r.returncode} timed_out={r.timed_out}\n{r.output}")
        raise Decision(500, "기획 승인을 처리하지 못했습니다. 잠시 뒤 다시 시도해 주세요(기록은 var/logs).")
    if r.returncode == 1:
        raise Decision(422, "기획 점검에 걸렸습니다:\n" + _tail(r.output, root))


def _commit_plan_approval(conn, job_id, ap):
    """한 트랜잭션: topic → building(waiting) + plan_hash·tts_hash 갱신 + approvals + job_events."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "UPDATE jobs SET state='building', queue_state='waiting', slot_id=NULL, plan_hash=?, tts_hash=?, "
            "hold_reason=NULL, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id=? AND state='topic' AND queue_state='held_human'",
            (ap["plan_hash"], ap["tts_hash"], job_id))
        if cur.rowcount != 1:
            conn.execute("ROLLBACK")
            raise Decision(409, "이미 처리됐거나 상태가 바뀐 작업입니다.")
        conn.execute("INSERT INTO approvals(job_id, plan_hash, tts_hash, approved_by) VALUES (?,?,?,?)",
                     (job_id, ap["plan_hash"], ap["tts_hash"], APPROVER))
        jobs_repo.add_event(conn, job_id, "topic", "building", f"기획 승인: {APPROVER}")
        conn.execute("COMMIT")
    except Decision:
        raise
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


# ───────── R10 ─────────


def approve_video(conn, root, job_id, form):
    root = Path(root)
    _need_yes(form, "confirm", *[f"chk_{i}" for i in range(1, CHECK_COUNT + 1)])
    shown_hash = form.get("plan_hash", "").strip()
    sha_form = form.get("sha256", "").strip()
    oid = form.get("output_id", "").strip()
    if not shown_hash or not sha_form or not oid.isdigit() or len(oid) > 12:
        raise Decision(409, "승인할 산출물 정보가 없습니다. 화면을 새로 열어 주세요.")
    with _Locked():
        job = _load(conn, job_id)
        if job["state"] != "review" or job["queue_state"] != "held_human":
            raise Decision(409, "승인함에서 확인할 상태가 아닙니다.")
        plan_file = _plan_file(root, job)
        _check_binding(conn, job, plan_file, root, need_rights=True)  # 권리 미확인 → 409 (T3)
        ok, current, _why = run_gate(plan_file, timeout=GATE_TIMEOUT)
        if not ok or not current:
            raise Decision(409, "기획 승인 기록과 컷표가 맞지 않습니다 — 수정 요청으로 재승인해 주세요.")
        if current != job["plan_hash"] or current != shown_hash:
            raise Decision(409, "컷표가 승인 뒤 바뀌었습니다 — 수정 요청으로 재승인해 주세요.")
        _check_output(conn, job, plan_file, int(oid), sha_form)
        if not jobs_repo.transition_from(
                conn, job_id, "review", "approved", queue_state="held_human", expect_queue_state="held_human",
                message=f"영상 승인: {APPROVER} · 자동 점검 미실행 · 체크 {CHECK_COUNT}/{CHECK_COUNT}",
                hold_reason="업로드 승인 대기"):
            raise Decision(409, "이미 처리됐거나 상태가 바뀐 작업입니다.")


def _check_output(conn, job, plan_file, output_id, sha_form):
    """C1·C14: 폼 output_id 가 이 작업의 최신 행이고 plan_hash 가 승인 해시와 같으며, 실제 파일 해시가 폼·DB 와 같아야 한다."""
    latest = conn.execute("SELECT id, file_path, sha256, plan_hash FROM outputs WHERE job_id=? "
                          "ORDER BY id DESC LIMIT 1", (job["id"],)).fetchone()
    if latest is None or latest["id"] != output_id:
        raise Decision(409, "다시 렌더된 산출물이 있습니다 — 화면을 새로 열어 확인해 주세요.")
    if latest["plan_hash"] != job["plan_hash"]:
        raise Decision(409, "이 산출물은 현재 승인된 기획으로 만든 것이 아닙니다.")
    if not latest["sha256"] or latest["sha256"] != sha_form:
        raise Decision(409, "화면에서 본 산출물과 다릅니다 — 다시 확인해 주세요.")
    try:
        actual = hash_output(paths.resolve_in(plan_file.parent, latest["file_path"]))
    except (paths.PathRejected, FileNotFoundError, OSError):
        raise Decision(409, "산출물 파일을 찾을 수 없습니다.") from None
    if actual != latest["sha256"]:
        raise Decision(409, "산출물이 바뀌었습니다(다시 렌더됨) — 다시 확인해 주세요.")


# ───────── R11~R15 ─────────


def request_change(conn, job_id, form):
    note = form.get("note", "").strip()
    if len(note) > NOTE_MAX:
        raise Decision(422, f"메모는 {NOTE_MAX}자까지입니다.")
    targets = [t.strip() for t in form.getlist("targets") if t.strip()]
    if any(t not in dict(CHANGE_TARGETS) for t in targets):
        raise Decision(422, "수정 대상 값이 올바르지 않습니다.")
    _load(conn, job_id)
    msg = "수정 요청" + (f" [대상: {', '.join(targets)}]" if targets else "") + (f": {note}" if note else "")
    if not jobs_repo.transition_from(
            conn, job_id, "review", "topic", queue_state=jobs_repo.TOPIC_QUEUE_STATE,
            expect_queue_state="held_human", message=msg, hold_reason="수정 요청 — 컷표 수정 후 기획 재승인"):
        raise Decision(409, "승인함에서 확인할 상태가 아닙니다.")


def rebuild(conn, root, job_id, form):
    _need_yes(form, "confirm")
    root = Path(root)
    with _Locked():
        job = _load(conn, job_id)
        if job["state"] != "review" or job["queue_state"] != "held_human":
            raise Decision(409, "승인함에서 확인할 상태가 아닙니다.")
        plan_file = _plan_file(root, job)
        ok, current, _why = run_gate(plan_file, timeout=GATE_TIMEOUT)
        if not ok or not current or current != job["plan_hash"]:
            raise Decision(409, "컷표가 승인 뒤 바뀌었습니다 — 수정 요청으로 재승인해 주세요.")
        if not jobs_repo.transition_from(
                conn, job_id, "review", "building", queue_state="waiting", expect_queue_state="held_human",
                message="다시 만들기 요청", hold_reason=None):
            raise Decision(409, "이미 처리됐거나 상태가 바뀐 작업입니다.")


def hold(conn, job_id, form):
    _need_yes(form, "confirm")
    _load(conn, job_id)
    r = conn.execute("SELECT s.rights_status FROM jobs j LEFT JOIN sources s ON s.id=j.source_id WHERE j.id=?",
                     (job_id,)).fetchone()
    why = "보관(권리 미확인)" if r is None or r[0] not in RIGHTS_OK else "보관"
    if not jobs_repo.transition_from(
            conn, job_id, "review", "review", expect_queue_state="held_human", message=why, hold_reason=why):
        raise Decision(409, "승인함에서 확인할 상태가 아닙니다.")


def cancel(conn, job_id, form):
    _need_yes(form, "confirm")
    job = _load(conn, job_id)
    if job["queue_state"] == "running":
        raise Decision(409, "실행 중인 작업은 취소할 수 없습니다.")
    if job["state"] not in CANCELABLE or not jobs_repo.transition_from(
            conn, job_id, job["state"], "canceled", queue_state="finished",
            expect_queue_state=("waiting", "held_human", "held_resource", "paused"),
            message="취소", hold_reason=None):
        raise Decision(409, "취소할 수 없는 상태입니다.")


def revert_plan(conn, job_id, form):
    _need_yes(form, "confirm")
    _load(conn, job_id)
    if not jobs_repo.transition_from(
            conn, job_id, "building", "topic", queue_state=jobs_repo.TOPIC_QUEUE_STATE,
            expect_queue_state="held_human", message="기획 수정으로 되돌림 — 재승인 필요",
            hold_reason="기획 승인 대기"):
        raise Decision(409, "되돌릴 수 있는 상태(제작 보류)가 아닙니다.")
