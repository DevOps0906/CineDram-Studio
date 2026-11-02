"""승인 경로 공용 검사 (개발 3 T4·C1·C2·C5·C7). 서버(R9·R10)와 워커 runner 가 함께 쓴다.

엔진은 import 하지 않는다: gate 는 `core/gate_cli.py` 서브프로세스로만 부른다.
"""
import hashlib
import json
import logging
import sys
from pathlib import Path

from . import logmask, paths, subproc

log = logging.getLogger(__name__)

GATE_CLI = Path(__file__).resolve().parent / "gate_cli.py"
GATE_TIMEOUT = 60
RIGHTS_OK = ("own", "licensed")
APPROVAL_FILE = "기획승인.json"


class ApprovalError(Exception):
    """사람 말 사유. HTTP 409 로 매핑되는 검사 실패."""


def read_approval_file(plan_dir):
    """기획승인.json(정본)에서 {plan_hash, tts_hash} 를 읽는다. 없음·빈 파일·잘림·키 누락은 ApprovalError(미승인)."""
    f = Path(plan_dir) / APPROVAL_FILE
    try:
        ap = json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ApprovalError("기획승인.json 이 없다 — 미승인") from None
    except (OSError, ValueError):
        raise ApprovalError("기획승인.json 을 읽을 수 없다(비었거나 잘림) — 미승인") from None
    if not isinstance(ap, dict) or not ap.get("plan_hash") or "tts_hash" not in ap:
        raise ApprovalError("기획승인.json 내용이 불완전하다 — 미승인")
    return {"plan_hash": ap["plan_hash"], "tts_hash": ap["tts_hash"]}


def source_video_path(plan_file, require_exists=False):
    """meta.source_video 의 실경로(컷표 폴더 기준, 엔진이 여는 경로와 같은 기준). 위반이면 ApprovalError."""
    plan_file = Path(plan_file)
    try:
        src = json.loads(plan_file.read_text(encoding="utf-8"))["meta"]["source_video"]
        real = paths.resolve_in(plan_file.parent, src)
    except (paths.PathRejected, KeyError, TypeError, ValueError, OSError) as e:
        log.warning("원본 경로 검사 실패: %s", logmask.mask(repr(e)))  # 상세는 마스킹 로그로만(S5)
        raise ApprovalError("원본 경로가 허용 범위 밖입니다") from None
    if require_exists and not real.is_file():
        raise ApprovalError("원본 영상 파일이 없다")
    return real


def check_source_binding(conn, job_id, plan_file, root, need_rights=False, require_exists=True):
    """C2: source_id NULL → 거부, source_video 실경로 ≠ sources.file_path 실경로 → 거부.

    need_rights=True(R10)이면 rights ∉ {own, licensed} 도 거부. 권리 정본은 DB sources.rights_status.
    sources.file_path 는 작업 루트 기준 상대 경로. 통과하면 (source_id, rights_status).
    """
    row = conn.execute(
        "SELECT j.source_id, s.file_path, s.rights_status FROM jobs j "
        "LEFT JOIN sources s ON s.id = j.source_id WHERE j.id=?", (job_id,)).fetchone()
    if row is None:
        raise ApprovalError("작업이 없다")
    source_id, db_path, rights = row
    if source_id is None or db_path is None:
        raise ApprovalError("원본(source_id)이 연결되지 않았다")
    video = source_video_path(plan_file, require_exists=require_exists)
    try:
        db_real = paths.resolve_in(root, db_path)
    except paths.PathRejected as e:
        log.warning("등록된 원본 경로 검사 실패: %s", logmask.mask(repr(e)))
        raise ApprovalError("등록된 원본 경로가 허용 범위 밖입니다") from None
    if paths._norm(video) != paths._norm(db_real):
        raise ApprovalError("컷표의 원본 영상이 등록된 원본과 다르다")
    if need_rights and rights not in RIGHTS_OK:
        raise ApprovalError("권리 미확인 — 영상 승인·업로드 불가")
    return source_id, rights


def run_gate(plan_file, timeout=GATE_TIMEOUT):
    """엔진 gate 를 서브프로세스로 실행. (ok, plan_hash, reason). 읽기 오류·시간 초과도 ok=False."""
    r = subproc.run([sys.executable, str(GATE_CLI), str(plan_file)], timeout=timeout)
    if r.timed_out:
        return False, None, "gate 시간 초과"
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, None, f"gate 응답 오류(종료 코드 {r.returncode})"
    return bool(d.get("ok")) and r.returncode == 0, d.get("plan_hash"), d.get("reason") or ""


def hash_output(path):
    """C1: 파일이면 sha256(내용), 폴더면 정렬한 (상대경로, 내용 해시) 목록의 sha256. 없으면 FileNotFoundError."""
    p = Path(path)
    if p.is_file():
        return _file_sha(p)
    if not p.is_dir():
        raise FileNotFoundError(str(p))
    h = hashlib.sha256()
    for f in sorted(x for x in p.rglob("*") if x.is_file()):
        h.update(f.relative_to(p).as_posix().encode("utf-8") + b"\0" + _file_sha(f).encode() + b"\n")
    return h.hexdigest()


def _file_sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record_output(conn, job_id, plan_file, out_path, mode, plan_hash):
    """워커 성공 시 outputs 행. file_path = 컷표 폴더 기준 상대. render_ver = 이 작업의 이전 행 수 + 1. output id 반환."""
    out_path = Path(out_path)
    sha = hash_output(out_path)
    rel = out_path.resolve().relative_to(Path(plan_file).resolve().parent).as_posix()
    ver = conn.execute("SELECT COUNT(*) FROM outputs WHERE job_id=?", (job_id,)).fetchone()[0] + 1
    cur = conn.execute(
        "INSERT INTO outputs(job_id, render_ver, file_path, sha256, mode, plan_hash) VALUES (?,?,?,?,?,?)",
        (job_id, ver, rel, sha, mode, plan_hash))
    return cur.lastrowid
