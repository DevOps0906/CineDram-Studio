"""포트 없는 큐 워커 (docs/16 ADR-005, docs/14 §4). 표준 라이브러리만 쓴다.

웹 서버와는 SQLite로만 통신한다. 듣는 포트가 없다.
- 단일 실행: 잠금 파일(Windows msvcrt / POSIX fcntl). 임대 회수는 잠금 보유자만.
- 집기: BEGIN IMMEDIATE 안에서 슬롯 종류별 running 수 확인 → 조건부 UPDATE(rowcount 1일 때만).
- 슬롯: gpu 1 고정, llm 은 설정값(기본 1). 실행은 한 번에 하나(순차).
- 상태 전이와 job_events 기록은 한 트랜잭션. 서브프로세스는 집기 트랜잭션을 커밋한 뒤에 띄운다.
- 엔진은 서브프로세스(인자 목록, 시간 제한, 환경 변수 허용 목록). 엔진을 임포트하지 않는다.
- 승인 해시 재검사는 엔진 gate 에 맡긴다: 종료 1 + 출력 "[P0-" → held_human, 그 밖의 1·2 → failed.
"""
import importlib.util
import logging
import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.security import SecurityError, mask_secrets, resolve_within  # noqa: E402

_spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

ENGINE_SCRIPT = ROOT / "engine" / "shortsflow.py"
# approve 는 사람만(AGENTS.md 절대 규칙). 0002 CHECK 와 같은 목록.
ALLOWED_COMMANDS = ("check", "tts-plan", "tts-measure", "recalc", "render", "handoff", "delegate")
GPU_SLOTS = 1  # 고정
# 자식 프로세스에 넘기는 환경 변수. API 키 등은 넘기지 않는다.
# Windows 는 SYSTEMROOT 가 없으면 python 기동(난수·소켓 초기화)이 실패한다.
ENV_ALLOWLIST = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATH", "PATHEXT",
                 "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL")
NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
MAX_EVENT_TEXT = 500
log = logging.getLogger("worker")


class WorkerError(Exception):
    pass


class WorkerLockError(WorkerError):
    pass


# ---------------------------------------------------------------- 단일 실행 잠금
class SingleInstanceLock:
    """잠금 파일 1바이트를 배타 잠금한다. 프로세스가 죽으면 OS가 풀어 준다."""

    def __init__(self, path):
        self.path = Path(path)
        self._fh = None

    @property
    def held(self):
        return self._fh is not None

    def acquire(self):
        if self._fh is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            fh.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            fh.close()
            raise WorkerLockError(f"다른 워커가 실행 중입니다: {self.path}") from e
        self._fh = fh
        return self

    def release(self):
        if self._fh is None:
            return
        try:
            self._fh.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


# ---------------------------------------------------------------- 기동 검사
def verify_migrations(db_path, migrations_dir=migrate_mod.MIGRATIONS_DIR):
    """적용된 마이그레이션 체크섬이 파일과 같고 미적용 파일이 없어야 기동한다(적용은 migrate.py 몫)."""
    files = dict(migrate_mod.list_migrations(migrations_dir))
    if not Path(db_path).exists():
        raise migrate_mod.MigrationError(f"DB가 없습니다: {db_path} — 먼저 python db/migrate.py 실행")
    conn = migrate_mod.connect(db_path)
    try:
        try:
            applied = dict(conn.execute("SELECT version, checksum FROM schema_migrations"))
        except Exception as e:
            raise migrate_mod.MigrationError("schema_migrations 가 없습니다 — 먼저 python db/migrate.py 실행") from e
    finally:
        conn.close()
    for version, saved in applied.items():
        if version not in files:
            raise migrate_mod.MigrationError(f"적용된 마이그레이션 {version} 파일이 없습니다")
        if migrate_mod.checksum(files[version]) != saved:
            raise migrate_mod.MigrationError(f"적용된 파일이 수정됨: {files[version].name}")
    pending = sorted(v for v in files if v not in applied)
    if pending:
        raise migrate_mod.MigrationError(f"미적용 마이그레이션 {', '.join(pending)} — 먼저 python db/migrate.py 실행")


def build_env(source=None):
    """허용 목록에 있는 변수만 복사하고 UTF-8 출력을 강제한다."""
    source = os.environ if source is None else source
    upper = {k.upper(): v for k, v in source.items()}
    env = {k: upper[k] for k in ENV_ALLOWLIST if k in upper}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


# ---------------------------------------------------------------- 트랜잭션 도우미
def _short(text):
    text = mask_secrets(text or "")
    return text if len(text) <= MAX_EVENT_TEXT else text[:MAX_EVENT_TEXT] + "…"


def _event(conn, job_id, from_state, to_state, message=None, error=None, render_count=None):
    """job_events 의 from/to 에는 queue_state 값을 쓴다(작업 state 전이는 message 에 함께 적는다)."""
    conn.execute(
        "INSERT INTO job_events(job_id, from_state, to_state, message, error, render_count) VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, from_state, to_state, _short(message) if message else None, _short(error) if error else None,
         render_count))


class _Tx:
    """BEGIN IMMEDIATE … COMMIT. 예외면 ROLLBACK 후 다시 올린다."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.execute("COMMIT")
        elif self.conn.in_transaction:
            self.conn.execute("ROLLBACK")
        return False


def _fail_waiting(conn, job_id, reason):
    """집기 전 검증 실패: waiting → finished(state=failed). 호출자가 트랜잭션 안에서 부른다."""
    cur = conn.execute(
        f"UPDATE jobs SET queue_state='finished', state='failed', slot_id=NULL, hold_reason=NULL, "
        f"finished_at={NOW_SQL}, updated_at={NOW_SQL} WHERE id=? AND queue_state='waiting'", (job_id,))
    if cur.rowcount == 1:
        _event(conn, job_id, "waiting", "finished", "state→failed: 실행 전 검증 실패", reason)


# ---------------------------------------------------------------- 임대 회수
def reclaim(conn, lock, expired_only=True):
    """running 행 회수. 잠금 보유자만 부른다.

    expired_only=False 는 기동 직후용: 잠금을 쥐었으니 남은 running 은 모두 죽은 워커 것이다.
    attempts < max_attempts → waiting(재시도), 아니면 finished(state=failed). 회수한 job id 목록을 돌려준다.
    """
    if lock is None or not lock.held:
        raise WorkerLockError("임대 회수는 단일 실행 잠금 보유자만 할 수 있습니다")
    done = []
    with _Tx(conn):
        sql = "SELECT id, attempts, max_attempts FROM jobs WHERE queue_state='running'"
        if expired_only:
            sql += f" AND (lease_expires_at IS NULL OR lease_expires_at < {NOW_SQL})"
        for job_id, attempts, max_attempts in conn.execute(sql).fetchall():
            if attempts < max_attempts:
                conn.execute(
                    "UPDATE jobs SET queue_state='waiting', slot_id=NULL, lease_owner=NULL, "
                    f"lease_expires_at=NULL, heartbeat_at=NULL, updated_at={NOW_SQL} "
                    "WHERE id=? AND queue_state='running'", (job_id,))
                _event(conn, job_id, "running", "waiting", f"임대 회수: 재시도 ({attempts}/{max_attempts})")
            else:
                conn.execute(
                    "UPDATE jobs SET queue_state='finished', state='failed', slot_id=NULL, lease_owner=NULL, "
                    f"lease_expires_at=NULL, finished_at={NOW_SQL}, updated_at={NOW_SQL} "
                    "WHERE id=? AND queue_state='running'", (job_id,))
                _event(conn, job_id, "running", "finished", "state→failed: 임대 회수",
                       f"재시도 상한 도달 ({attempts}/{max_attempts})")
            done.append(job_id)
    return done


# ---------------------------------------------------------------- 집기
def _slot_capacity(kind, llm_slots):
    return GPU_SLOTS if kind == "gpu" else max(1, int(llm_slots))


def claim(conn, worker_id, roots, llm_slots=1, lease_seconds=60):
    """다음 작업 하나를 집어 커밋한다. 집은 작업 dict 또는 None.

    검증(명령 허용 목록·경로 제한)에 걸린 행은 같은 트랜잭션에서 failed 로 보내고 다음 행을 본다.
    """
    with _Tx(conn):
        for kind in ("gpu", "llm"):
            cap = _slot_capacity(kind, llm_slots)
            used = [r[0] for r in conn.execute(
                "SELECT slot_id FROM jobs WHERE queue_state='running' AND slot_kind=?", (kind,))]
            if len(used) >= cap:
                continue
            slot_id = next(f"{kind}-{n}" for n in range(1, cap + 1) if f"{kind}-{n}" not in used)
            rows = conn.execute(
                "SELECT id, engine_cmd, plan_path FROM jobs WHERE queue_state='waiting' AND slot_kind=? "
                "AND engine_cmd IS NOT NULL ORDER BY priority DESC, id", (kind,)).fetchall()
            for job_id, cmd, plan_path in rows:
                if cmd not in ALLOWED_COMMANDS:
                    _fail_waiting(conn, job_id, f"허용되지 않은 엔진 명령: {cmd}")
                    continue
                try:
                    plan = resolve_within(plan_path, roots)
                except SecurityError as e:
                    _fail_waiting(conn, job_id, f"컷표 경로 거부: {e}")
                    continue
                # render 를 집으면 같은 트랜잭션에서 state→building(docs/01 §2 topic→building→review)
                building = ", state='building'" if cmd == "render" else ""
                cur = conn.execute(
                    f"UPDATE jobs SET queue_state='running'{building}, slot_id=?, attempts=attempts+1, lease_owner=?, "
                    f"lease_expires_at=strftime('%Y-%m-%dT%H:%M:%fZ','now','+{int(lease_seconds)} seconds'), "
                    f"heartbeat_at={NOW_SQL}, started_at={NOW_SQL}, updated_at={NOW_SQL}, hold_reason=NULL "
                    "WHERE id=? AND queue_state='waiting'", (slot_id, worker_id, job_id))
                if cur.rowcount != 1:
                    continue
                attempts = conn.execute("SELECT attempts FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                _event(conn, job_id, "waiting", "running", f"{cmd} 시작 (슬롯 {slot_id}, 시도 {attempts})"
                       + (", state→building" if building else ""))
                return {"id": job_id, "cmd": cmd, "plan": plan, "slot_id": slot_id, "attempts": attempts}
    return None


# ---------------------------------------------------------------- 실행
def kill_tree(proc):
    """자식과 그 자손까지 강제 종료한다."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _drain(proc, timeout=10):
    """종료된 프로세스의 남은 출력을 모은다. 파이프가 안 닫히면 받은 것만 돌려주고 닫는다."""
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        out = e.output or b""
        if proc.stdout:
            proc.stdout.close()
    return (out or b"").decode("utf-8", "replace")


def run_engine(cmd, plan, timeout, engine_script=ENGINE_SCRIPT, on_tick=None, tick=5.0):
    """엔진 서브프로세스 실행 → (종료 코드 또는 None(시간 초과), 출력 문자열).

    Popen 실패(OSError)는 그대로 올린다. 실행 중 어떤 예외(Ctrl+C 포함)든 자식 트리를 죽이고 올린다.
    on_tick(하트비트) 실패는 로그만 남기고 계속한다. on_tick 이 False 를 돌려주면(임대 잃음 = 사람이 취소)
    자식 트리를 죽이고 (None, 출력)을 돌려준다.
    """
    if cmd not in ALLOWED_COMMANDS:
        raise WorkerError(f"허용되지 않은 엔진 명령: {cmd}")
    plan = Path(plan)
    argv = [sys.executable, str(engine_script), cmd, str(plan)]
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(argv, cwd=str(plan.parent), env=build_env(), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
    try:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                kill_tree(proc)
                return None, _drain(proc)
            try:
                out, _ = proc.communicate(timeout=min(tick, remaining))
                return proc.returncode, out.decode("utf-8", "replace")
            except subprocess.TimeoutExpired:
                pass
            if on_tick:
                try:
                    held = on_tick()
                except Exception as e:  # 하트비트 실패로 실행을 멈추지 않는다
                    log.warning("하트비트 실패(계속 실행): %s", mask_secrets(str(e)))
                else:
                    if held is False:  # 임대 확인 실패 = 사람이 취소 → 프로세스 트리 종료
                        log.info("임대 잃음(취소): 프로세스 트리 종료")
                        kill_tree(proc)
                        return None, _drain(proc)
    except BaseException:
        kill_tree(proc)
        raise


GATED_COMMANDS = ("render", "handoff", "delegate")  # 엔진 gate 를 호출하는 명령(ADR-005)


def classify(returncode, output, cmd=None, error=None):
    """(queue_state, job state 또는 None, hold_reason/error)."""
    if error:
        return "finished", "failed", error
    if returncode is None:
        return "finished", "failed", "시간 초과로 프로세스 트리 종료"
    if returncode == 0:
        return "finished", None, None
    if returncode == 1 and cmd in GATED_COMMANDS:
        p0 = next((ln.strip() for ln in output.splitlines() if "[P0-" in ln), None)
        if p0:
            return "held_human", None, p0
    return "finished", "failed", f"엔진 종료 코드 {returncode}"


def heartbeat(conn, job_id, worker_id, lease_seconds):
    """임대를 늘린다. 임대가 아직 내 것이면 True, 아니면(사람이 취소 등) False."""
    with _Tx(conn):
        cur = conn.execute(
            f"UPDATE jobs SET heartbeat_at={NOW_SQL}, "
            f"lease_expires_at=strftime('%Y-%m-%dT%H:%M:%fZ','now','+{int(lease_seconds)} seconds') "
            "WHERE id=? AND queue_state='running' AND lease_owner=?", (job_id, worker_id))
    return cur.rowcount == 1


# 성공(종료 0) 때 전이(docs/01 §2): (queue_state, 작업 state 또는 None=유지, hold_reason).
# render: building → review(산출물 생성) = 승인함 사람 대기(docs/14 §4 held_human ↔ review).
# check·recalc: state 유지, 사람이 다음 명령을 지정할 때까지 대기(queue_ops.attach_plan).
NEXT_CMD_HOLD = "다음 명령 지정 대기"
SUCCESS_TRANSITIONS = {
    "render": ("held_human", "review", "승인함 영상 확인 대기"),
    "check": ("held_human", None, NEXT_CMD_HOLD),
    "recalc": ("held_human", None, NEXT_CMD_HOLD),
}


def finish(conn, job, worker_id, returncode, output, error=None):
    """실행 결과 전이 + job_events 한 트랜잭션. 임대가 이미 넘어갔으면 False."""
    queue_state, state, reason = classify(returncode, output, job["cmd"], error)
    success = returncode == 0 and not error and job["cmd"] in SUCCESS_TRANSITIONS
    if success:
        queue_state, state, reason = SUCCESS_TRANSITIONS[job["cmd"]]
    with _Tx(conn):
        sets = ["queue_state=?", "slot_id=NULL", "lease_owner=NULL", "lease_expires_at=NULL",
                f"updated_at={NOW_SQL}", "hold_reason=?"]
        params = [queue_state, _short(reason) if queue_state == "held_human" else None]
        if queue_state == "finished":
            sets.append(f"finished_at={NOW_SQL}")
        if state:
            sets.append("state=?")
            params.append(state)
        cur = conn.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE id=? AND queue_state='running' AND lease_owner=?",
            params + [job["id"], worker_id])
        if cur.rowcount != 1:
            return False
        msg = f"{job['cmd']} 종료 코드 {returncode}" + (f", state→{state}" if state else "")
        if queue_state == "held_human":
            msg += f", {reason}" if success else f", 재승인 대기: {reason}"
        error = None
        if state == "failed":
            tail = "\n".join(output.strip().splitlines()[-5:])
            error = reason + (f"\n{tail}" if tail else "")
        render_count = None
        if success and job["cmd"] == "render":  # 렌더 횟수: 그 작업의 직전 최대값 + 1
            render_count = conn.execute(
                "SELECT COALESCE(MAX(render_count), 0) + 1 FROM job_events WHERE job_id=?", (job["id"],)).fetchone()[0]
        _event(conn, job["id"], "running", queue_state, msg, error, render_count)
    return True


def lock_path_for(db_path):
    """잠금 파일은 DB 경로에서만 유도한다(옵션으로 바꿔 두 워커를 띄우는 우회 차단)."""
    db = Path(db_path).resolve()
    return db.with_name(db.name + ".worker.lock")


class Worker:
    def __init__(self, db_path, roots, timeout=3600, lease_seconds=120,
                 heartbeat_seconds=10, llm_slots=1, engine_script=ENGINE_SCRIPT, log_dir=None):
        self.db_path = Path(db_path)
        self.roots = [Path(r) for r in roots]
        self.lock = SingleInstanceLock(lock_path_for(self.db_path))
        self.timeout = timeout
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.llm_slots = llm_slots
        self.engine_script = engine_script
        self.log_dir = Path(log_dir) if log_dir else self.db_path.parent / "worker-logs"
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self.conn = None

    def start(self):
        """잠금 → 마이그레이션 체크섬 검사 → 연결 → 남은 running 회수."""
        self.lock.acquire()
        try:
            verify_migrations(self.db_path)
            self.conn = migrate_mod.connect(self.db_path)
            reclaim(self.conn, self.lock, expired_only=False)
        except BaseException:
            self.stop()
            raise
        return self

    def stop(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        self.lock.release()

    def run_once(self):
        """회수 → 집기 → 실행 → 기록. 실행한 job dict 또는 None."""
        reclaim(self.conn, self.lock, expired_only=True)
        job = claim(self.conn, self.worker_id, self.roots, self.llm_slots, self.lease_seconds)
        if job is None:
            return None
        lease = {"lost": False}

        def tick():
            held = heartbeat(self.conn, job["id"], self.worker_id, self.lease_seconds)
            if held is False:
                lease["lost"] = True
            return held

        try:
            rc, out = run_engine(job["cmd"], job["plan"], self.timeout, self.engine_script,
                                 on_tick=tick, tick=self.heartbeat_seconds)
        except OSError as e:  # Popen 실패(엔진·cwd 없음 등)
            rc, out = None, ""
            recorded = finish(self.conn, job, self.worker_id, rc, out,
                              error=f"엔진 실행 실패: {type(e).__name__}: {e}")
        else:
            self._write_log(job, out)
            if lease["lost"]:  # 사람 취소: 시간 초과가 아니다. 결과를 기록하지 않는다
                recorded = False
                self._record_cancel_kill(job["id"])
            else:
                recorded = finish(self.conn, job, self.worker_id, rc, out)
        if not recorded:
            log.info("작업 %s: 임대 잃음/취소됨, 결과 버림", job["id"])
        job["returncode"] = rc
        job["canceled"] = lease["lost"]
        return job

    def _record_cancel_kill(self, job_id):
        """취소로 트리를 끝냈음을 남긴다. 이미 finished(canceled) 행일 때만 이벤트만 추가한다."""
        with _Tx(self.conn):
            row = self.conn.execute("SELECT queue_state, state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row == ("finished", "canceled"):
                _event(self.conn, job_id, "finished", "finished", "워커: 취소 확인, 프로세스 트리 종료")

    def _write_log(self, job, output):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"job-{job['id']}-{job['attempts']}.log"
        path.write_text(mask_secrets(output), encoding="utf-8")

    def run_forever(self, idle_seconds=2.0):
        while True:
            try:
                job = self.run_once()
            except sqlite3.OperationalError as e:  # DB 잠김 등: 기록하고 다음 반복
                log.warning("DB 오류(다음 반복에서 재시도): %s", e)
                job = None
            if job is None:
                time.sleep(idle_seconds)
