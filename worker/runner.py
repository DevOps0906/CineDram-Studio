"""작업 1건 실행: 경로 재검사 → 승인 해시 재검사(D5) → 엔진 호출 → 종료 코드 대응."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from core import approval, logmask, paths, subproc

from . import queue

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine" / "shortsflow.py"
ENGINE_TIMEOUT = 3600
# 렌더 후 점검(inspect). True면 `shortsflow.py inspect <컷표>`, False면 `inspect_av.py <컷표>`를 직접 부른다.
INSPECT_VIA_SHORTSFLOW = True
INSPECT_SCRIPT = ROOT / "engine" / "inspect_av.py"
INSPECT_TIMEOUT = 600
INSPECT_RESULT = "점검결과.json"
INSPECT_RESULT_MAX = 1_000_000
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


class EngineKillFailed(Exception):
    """엔진 프로세스 트리를 못 죽였다. 워커는 다음 작업을 집지 않고 멈춰야 한다(GPU 슬롯 1 보호)."""


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


def _db_file(conn):
    for _, name, file in conn.execute("PRAGMA database_list"):
        if name == "main" and file:
            return file
    return None


def _make_should_stop(conn, job_id):
    """별도 짧은 연결로 queue_state 만 읽는다(트랜잭션 없음). 읽기 실패는 중지로 보지 않는다."""
    db = _db_file(conn)

    def should_stop():
        try:
            c = sqlite3.connect(db, timeout=1) if db else conn
            try:
                row = c.execute("SELECT queue_state FROM jobs WHERE id=?", (job_id,)).fetchone()
            finally:
                if c is not conn:
                    c.close()
        except sqlite3.Error:
            return False
        return row is None or row[0] != "running"
    return should_stop


def _inspect_blocked(plan_file, plan_hash):
    """rc 1 이 진짜 차단인지: 점검결과.json 이 있고 plan_hash 가 승인 해시와 같을 때만 (True, 사유). 아니면 크래시."""
    try:
        f = plan_file.parent / INSPECT_RESULT
        if f.stat().st_size > INSPECT_RESULT_MAX:
            return False, ""
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("plan_hash") != plan_hash:
            return False, ""
        reason = data.get("summary", {}).get("fail_reason")
        return True, reason if isinstance(reason, str) and reason.strip() else "항목 미상"
    except (OSError, ValueError, AttributeError):
        return False, ""


def run_job(conn, job_id, root=ROOT, timeout=ENGINE_TIMEOUT, engine=ENGINE, inspect_engine=None,
            inspect_timeout=INSPECT_TIMEOUT):
    """집은(running) 작업을 실행하고 결과 상태로 전이한다. 최종 (state, queue_state)를 돌려준다.

    어떤 예외도 failed 로 전이한다(같은 작업이 워커를 계속 죽이는 poison job 방지).
    """
    try:
        if inspect_engine is None and engine == ENGINE:  # 엔진을 바꿔 부르면(테스트 스텁) inspect 도 명시해야 한다
            inspect_engine = ENGINE if INSPECT_VIA_SHORTSFLOW else INSPECT_SCRIPT
        return _run_job(conn, job_id, root, timeout, engine, inspect_engine, inspect_timeout)
    except EngineKillFailed:
        raise
    except Exception as e:
        err = _tail(repr(e))
        try:
            queue.transition(conn, job_id, "failed", "finished", message="워커 내부 오류", error=err)
        except Exception:
            pass
        return "failed", "finished"


def _run_job(conn, job_id, root, timeout, engine, inspect_engine=None, inspect_timeout=INSPECT_TIMEOUT):
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

    def abnormal_exit(r, name, limit):
        """kill 실패·실행 중 취소·시간 초과 공통 처리. 해당 없으면 None."""
        if r.kill_failed:
            conn.execute("INSERT INTO job_events(job_id, from_state, to_state, message) VALUES (?,?,?,?)",
                         (job_id, "building", "building", "엔진 종료 실패 — 워커 정지"))
            if r.timed_out:  # 슬롯은 못 비웠지만 작업 자체는 실패로 닫는다
                queue.transition(conn, job_id, "failed", "finished", message=f"{name} 시간 초과",
                                 error=f"timeout {limit}s, 프로세스 종료 실패")
            raise EngineKillFailed(job_id)
        if r.stopped:  # 실행 중 취소: outputs·전이 없이 이벤트만
            row = conn.execute("SELECT state, queue_state FROM jobs WHERE id=?", (job_id,)).fetchone()
            state, qs = row if row else ("building", "canceled")
            conn.execute("INSERT INTO job_events(job_id, from_state, to_state, message) VALUES (?,?,?,?)",
                         (job_id, state, state, "실행 중 취소로 중지"))
            return state, qs
        if r.timed_out:  # returncode -1 이므로 먼저 본다
            log = _save_log(root, job_id, r.output)
            return done("failed", "finished", message=f"{name} 시간 초과", error=f"timeout {limit}s (로그 {log})")
        return None

    cmd = CMD_BY_MODE[exec_mode]
    r = subproc.run([sys.executable, str(engine), cmd, str(plan_file)], timeout=timeout,
                    should_stop=_make_should_stop(conn, job_id))
    abnormal = abnormal_exit(r, cmd, timeout)
    if abnormal:
        return abnormal
    if r.returncode == 0:
        if exec_mode == "auto_render" and inspect_engine is not None:  # 영상이 있는 모드만 점검
            argv = [sys.executable, str(inspect_engine)] + (["inspect"] if inspect_engine == ENGINE else [])
            try:  # 이전 결과가 남아 있으면 크래시를 차단으로 오인한다
                (plan_file.parent / INSPECT_RESULT).unlink(missing_ok=True)
            except OSError as e:
                return done("failed", "finished", message="이전 점검결과 삭제 실패", error=_tail(repr(e)))
            ri = subproc.run(argv + [str(plan_file)], timeout=inspect_timeout,
                             should_stop=_make_should_stop(conn, job_id))
            abnormal = abnormal_exit(ri, "inspect", inspect_timeout)
            if abnormal:
                return abnormal
            if ri.returncode == 1:
                blocked, why = _inspect_blocked(plan_file, saved_hash)
                if blocked:  # 차단: outputs 없이 사람 확인
                    return hold("inspect 차단", "점검 실패: " + why)
                # 결과 없음·해시 불일치 = 크래시(파이썬 예외도 rc 1) → 아래 실패 경로
            if ri.returncode != 0:  # 2 = 실행 불가
                log = _save_log(root, job_id, ri.output)
                return done("failed", "finished", message=f"inspect 실패(종료 코드 {ri.returncode})",
                            error=f"종료 코드 {ri.returncode} (로그 {log}): {_tail(ri.output)}")
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
