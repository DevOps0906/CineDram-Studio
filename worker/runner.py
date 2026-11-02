"""작업 1건 실행: 경로 재검사 → 승인 해시 재검사(D5) → 엔진 호출 → 종료 코드 대응."""
import sys
from datetime import datetime, timezone
from pathlib import Path

from core import approval, logmask, paths, subproc

from . import queue

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine" / "shortsflow.py"
ENGINE_TIMEOUT = 3600
# exec_mode → 엔진 명령. 모두 승인 게이트(render·handoff·delegate)를 거치므로 해시 재검사 대상이다.
CMD_BY_MODE = {"auto_render": "render", "manual": "handoff", "ai_assistant": "delegate"}
# 엔진 기본 산출물 이름(컷표 폴더 기준). --out 을 주지 않으므로 이 이름으로 만들어진다.
OUT_BY_MODE = {"auto_render": "숏폼_상하템플릿.mp4", "manual": "캡컷패키지", "ai_assistant": "위임패키지"}


def verify_approval(plan_file, saved_plan_hash=None):
    """승인 해시 재검사. (ok, 사유). 정본은 기획승인.json — 엔진 gate 를 서브프로세스(core.gate_cli)로 부른다(C5)."""
    ok, plan_hash, reason = approval.run_gate(plan_file)
    if not ok:
        return False, reason
    if saved_plan_hash and saved_plan_hash != plan_hash:
        return False, "[P0-2] DB에 기록된 승인 컷표 해시와 현재 컷표가 다르다 — 다시 승인하세요"
    return True, ""


LOG_MAX_CHARS = 200_000
TAIL_LINES = 5


def _save_log(root, job_id, text):
    """마스킹한 출력만 파일로 남긴다(크기 상한)."""
    d = Path(root) / "var" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    p = d / f"job-{job_id}-{stamp}.log"
    p.write_text(logmask.mask(text)[-LOG_MAX_CHARS:], encoding="utf-8")
    return p.name


def _tail(text, lines=TAIL_LINES, n=500):
    """마스킹한 뒤 꼬리 몇 줄만."""
    t = "\n".join(logmask.mask(text).strip().splitlines()[-lines:])
    return t[-n:]


class _NotRunning(Exception):
    pass


def _record_and_review(conn, job_id, plan_file, out, mode, plan_hash, message):
    """K5: outputs 기록 + review 전이 + job_events 를 한 트랜잭션으로. 전이가 안 되면 outputs 도 남지 않는다(False)."""
    try:
        with queue.tx(conn):
            approval.record_output(conn, job_id, plan_file, out, mode, plan_hash)
            cur = conn.execute(
                f"UPDATE jobs SET state='review', queue_state='held_human', hold_reason='검수 대기', slot_id=NULL, "
                f"updated_at={queue.NOW} WHERE id=? AND queue_state='running'", (job_id,))
            if cur.rowcount != 1:
                raise _NotRunning()
            conn.execute("INSERT INTO job_events(job_id, from_state, to_state, message) VALUES (?,?,?,?)",
                         (job_id, "building", "review", message))
        return True
    except _NotRunning:
        return False


def run_job(conn, job_id, root=ROOT, timeout=ENGINE_TIMEOUT, engine=ENGINE):
    """집은(running) 작업을 실행하고 결과 상태로 전이한다. 최종 (state, queue_state)를 돌려준다.

    어떤 예외도 failed 로 전이한다(같은 작업이 워커를 계속 죽이는 poison job 방지).
    """
    try:
        return _run_job(conn, job_id, root, timeout, engine)
    except Exception as e:
        err = _tail(repr(e))
        try:
            queue.transition(conn, job_id, "failed", "finished", message="워커 내부 오류", error=err)
        except Exception:
            pass
        return "failed", "finished"


def _run_job(conn, job_id, root, timeout, engine):
    plan_path, exec_mode, saved_hash = conn.execute(
        "SELECT plan_path, exec_mode, plan_hash FROM jobs WHERE id=?", (job_id,)).fetchone()

    def done(state, qs, **kw):
        queue.transition(conn, job_id, state, qs, **kw)
        return state, qs

    def hold(msg, reason):
        return done("building", "held_human", message=msg, hold_reason=_tail(reason))

    try:
        plan_file = paths.resolve_in(root, plan_path)  # 읽을 때 재검사
    except paths.PathRejected as e:
        return done("failed", "finished", message="컷표 경로 거부", error=_tail(str(e)))
    if not plan_file.is_file():
        return done("failed", "finished", message="컷표 없음", error=f"{plan_path} 없음")

    try:
        ok, reason = verify_approval(plan_file, saved_hash)
    except Exception as e:  # 컷표 파싱 실패 등
        return done("failed", "finished", message="컷표 읽기 실패", error=_tail(repr(e)))
    if not ok:
        return hold("승인 해시 불일치", reason)
    if not saved_hash:
        return hold("승인 해시 미기록", "승인 해시 미기록: jobs.plan_hash 가 비어 있다")
    try:
        approval.source_video_path(plan_file)  # 엔진이 여는 경로(_dir / source_video)와 같은 기준
    except approval.ApprovalError as e:
        return hold("원본 경로 위반", str(e))

    cmd = CMD_BY_MODE[exec_mode]
    r = subproc.run([sys.executable, str(engine), cmd, str(plan_file)], timeout=timeout)
    if r.timed_out:  # returncode -1 이므로 먼저 본다
        log = _save_log(root, job_id, r.output)
        return done("failed", "finished", message="엔진 시간 초과", error=f"timeout {timeout}s (로그 {log})")
    if r.returncode == 0:
        out = plan_file.parent / OUT_BY_MODE[exec_mode]
        try:
            if _record_and_review(conn, job_id, plan_file, out, exec_mode, saved_hash, f"{cmd} 완료"):  # C1·K5
                return "review", "held_human"
        except FileNotFoundError:  # C14: 산출물 없이 review 로 보내지 않는다
            return hold(f"{cmd} 완료했으나 산출물 없음", "산출물 없음")
        # 실행 중 상태가 바뀜(취소 등): 덮어쓰지 않고 무시 이벤트만 남긴다
        return done("review", "held_human", message=f"{cmd} 완료", hold_reason="검수 대기")
    if r.returncode == 1 and "[P0-" in r.output:
        return hold(f"{cmd}: 사람 확인 필요", r.output)
    log = _save_log(root, job_id, r.output)
    return done("failed", "finished", message=f"{cmd} 실패(종료 코드 {r.returncode})",
                error=f"종료 코드 {r.returncode} (로그 {log}): {_tail(r.output)}")
