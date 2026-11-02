"""외부 프로세스 실행 (docs/16 ADR-003): 인자 목록·셸 없음·env 허용 목록·시간 초과 시 프로세스 트리 종료."""
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

WORK_ROOT = Path(__file__).resolve().parents[1]
MAX_OUTPUT_BYTES = 1_000_000  # 스트림(stdout·stderr)별 상한
ENV_ALLOW = ("SYSTEMROOT", "PATH", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
             "PATHEXT", "COMSPEC")
ENV_PROTECTED = ("PATH", "SYSTEMROOT")


class SubprocRejected(ValueError):
    pass


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False

    @property
    def output(self):
        return self.stdout + self.stderr


def build_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k.upper() in ENV_ALLOW}
    env["PYTHONUTF8"] = "1"  # 자식 파이썬 출력 인코딩을 UTF-8로 고정
    for k, v in (extra or {}).items():
        if k.upper() in ENV_PROTECTED:
            raise SubprocRejected(f"extra_env 로 {k} 를 바꿀 수 없다")
        env[k] = v
    return env


def kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _read(f):
    size = f.seek(0, os.SEEK_END)
    f.seek(0)
    data = f.read(MAX_OUTPUT_BYTES)
    return data.decode("utf-8", errors="replace"), size > MAX_OUTPUT_BYTES


def run(args, timeout, cwd=None, extra_env=None):
    """run(args, timeout, cwd=None, extra_env=None) -> RunResult.

    args[0] 은 shutil.which 로 절대 경로 해석(못 찾으면 SubprocRejected). cwd 기본값은 작업 루트.
    시간 초과면 returncode=-1, timed_out=True, 부분 출력은 유지한다.
    """
    if isinstance(args, (str, bytes)) or not args:
        raise TypeError("args 는 비어 있지 않은 인자 목록이어야 한다(셸 문자열 금지)")
    env = build_env(extra_env)
    exe = shutil.which(str(args[0]), path=env.get("PATH"))
    if not exe:
        raise SubprocRejected(f"실행 파일을 찾을 수 없다: {args[0]}")
    argv = [exe] + [str(a) for a in args[1:]]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with tempfile.TemporaryFile() as fo, tempfile.TemporaryFile() as fe:
        proc = subprocess.Popen(argv, shell=False, cwd=cwd or WORK_ROOT, env=env,
                                stdin=subprocess.DEVNULL, stdout=fo, stderr=fe, **kwargs)
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(proc)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        out, t1 = _read(fo)
        err, t2 = _read(fe)
    rc = -1 if timed_out else proc.returncode
    return RunResult(rc, out, err, timed_out=timed_out, truncated=t1 or t2)
