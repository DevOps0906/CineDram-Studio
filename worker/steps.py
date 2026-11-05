"""AI 단계(topic·flow) 실행 — docs/13 관문 ①②, tasks/plan-dev6-2.md.

대상 작업: state='topic', queue_state='waiting', plan_path IS NULL (컷표 없이 AI 초안으로 시작한 작업).
기존 만들기 화면은 plan_path 를 필수로 받으므로 이 경로와 겹치지 않는다.

- topic: SRT 전문 → 소재 후보 → topics 행(used=0) → held_human(관문 ① 소재 선택 대기)
- 관문 ①: choose_topic() 이 후보 하나를 used=1 로 정하고 작업을 waiting 으로 되돌린다(사람 선택).
- flow: 선택한 구간 SRT → 흐름·후킹·제목 후보 → var/jobs/<id>/흐름.json → held_human(관문 ② 대기)
- 관문 ②: choose_flow() 가 제목 후보 하나를 정해 선택.json 을 쓰고 작업을 waiting 으로 되돌린다(사람 선택).
- plan: 선택·SRT → 컷 목록(나레이션 tts_text·sub_lines 포함 = subtitle 단계 통합) → 컷표.json 을
  원본 영상 폴더에 쓰고 jobs.plan_path 를 채운다 → 엔진 check → held_human(기획 승인 대기, 기존 승인 경로).
  권리·원본·SRT 경로는 DB 에서만 가져오고 AI 출력으로 바꾸지 못한다. meta.sensitive_review 는 일부러 비워
  사람이 채우기 전에는 기획 승인이 막히게 한다(S11-2). 승인은 사람만.
권리 확인·상한·검증은 core.llm.call 이 맡고, 여기서는 LlmHold 를 큐 상태로 옮기기만 한다.
"""
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from core import llm, paths, subproc

from . import queue

LLM_SLOT = "llm0"  # AI 단계 슬롯 1(직렬). GPU 슬롯(gpu0)과 별개
FLOW_FILE = "흐름.json"
SRT_MAX_CHARS = 120_000
RULE_MAX_CHARS = 8000
CANDIDATES_MAX = 5
GATE1_REASON = "관문 ① 소재 선택 대기"
GATE2_REASON = "관문 ② 흐름·제목 선택 대기"
CHOICE_FILE = "선택.json"
PLAN_FILE = "컷표.json"
PLAN_REASON = "기획 승인 대기"
ENGINE = Path(__file__).resolve().parents[1] / "engine" / "shortsflow.py"
CHECK_TIMEOUT = 300
CUTS_MAX = 40

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "conflict_type": {"type": "string"},
        "ending": {"type": "string"},
        "hook_time": {"type": "number"},
        "hook_line": {"type": "string"},
        "title_lines": {"type": "array", "items": {"type": "string"}},
        "cuts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["dialog", "narration"]},
                    "src_start": {"type": "number"},
                    "src_end": {"type": "number"},
                    "label": {"type": "string"},
                    "tts_text": {"type": "string"},
                    "sub_lines": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kind", "src_start", "src_end", "label", "tts_text", "sub_lines"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["topic", "conflict_type", "ending", "hook_time", "hook_line", "title_lines", "cuts"],
    "additionalProperties": False,
}

TOPIC_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "hook_line": {"type": "string"},
                    "tone_primary": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["start", "end", "hook_line", "tone_primary", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

FLOW_SCHEMA = {
    "type": "object",
    "properties": {
        "flow": {"type": "array", "items": {"type": "string"}},
        "hook_line": {"type": "string"},
        "titles": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["flow", "hook_line", "titles"],
    "additionalProperties": False,
}

_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
_RULE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class StepHold(Exception):
    """AI 호출 없이 사람 확인이 필요하다(입력 문제 등)."""

    def __init__(self, queue_state, reason):
        super().__init__(reason)
        self.queue_state = queue_state
        self.reason = reason


def _sec(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000


def parse_srt(text):
    """[(start, end, 본문)] — 형식이 깨진 블록은 건너뛴다."""
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip().replace("\ufeff", "")):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            m = _TIME.search(ln)
            if m:
                g = m.groups()
                cues.append((_sec(*g[:4]), _sec(*g[4:]), " ".join(lines[i + 1:]).strip()))
                break
    return cues


def _fmt_cues(cues):
    return "\n".join(f"[{s:.1f}-{e:.1f}] {t}" for s, e, t in cues)


def job_dir(root, job_id):
    return paths.resolve_in(root, f"var/jobs/{int(job_id)}")


def _job(conn, job_id):
    row = conn.execute(
        "SELECT j.rule_set, s.srt_path FROM jobs j LEFT JOIN sources s ON s.id = j.source_id WHERE j.id=?",
        (job_id,)).fetchone()
    if row is None:
        raise KeyError(job_id)
    return row


def _read_srt(root, srt_path):
    """SRT 는 작업의 source 에서 직접 읽는다(권리-내용 결속). 작업 루트 안으로 제한."""
    if not srt_path:
        raise StepHold("held_human", "자막(SRT)이 등록되지 않음")
    try:
        f = paths.resolve_in(root, srt_path)  # file_path 와 같은 기준(작업 루트 상대)
    except paths.PathRejected as e:
        raise StepHold("held_human", f"자막 경로 거부: {e}") from None
    if not f.is_file():
        raise StepHold("held_human", "자막 파일 없음")
    text = f.read_text(encoding="utf-8-sig", errors="replace")
    if len(text) > SRT_MAX_CHARS:
        raise StepHold("held_human", f"자막이 너무 길다({len(text)}자 > {SRT_MAX_CHARS})")
    cues = parse_srt(text)
    if not cues:
        raise StepHold("held_human", "자막에서 대사를 찾지 못함")
    return cues


def _rules_text(rule_set_json, root=None):
    """구성 규칙 파일(common 제외) 앞부분. 구성별 분기 없이 파일 내용만 전달한다."""
    try:
        names = [n for n in json.loads(rule_set_json or "[]") if isinstance(n, str)]
    except ValueError:
        names = []
    base = Path(__file__).resolve().parents[1] / "rules"
    out = []
    for n in names:
        if n == "common" or not _RULE_NAME.match(n):
            continue
        f = base / f"{n}.md"
        if f.is_file():
            out.append(f.read_text(encoding="utf-8")[:RULE_MAX_CHARS])
    return "\n\n".join(out) or "(구성 규칙 없음)"


def next_step(conn, job_id, root):
    """'topic' | 'flow' | 'plan' | None. 소재 후보 없음→topic, 소재 선택 후 흐름 없음→flow,
    제목 선택(선택.json) 후 컷표 없음→plan."""
    n = conn.execute("SELECT COUNT(*) FROM topics WHERE job_id=?", (job_id,)).fetchone()[0]
    if n == 0:
        return "topic"
    used = conn.execute("SELECT COUNT(*) FROM topics WHERE job_id=? AND used=1", (job_id,)).fetchone()[0]
    d = job_dir(root, job_id)
    if used == 1 and not (d / FLOW_FILE).exists():
        return "flow"
    if used == 1 and (d / CHOICE_FILE).exists():
        return "plan"
    return None


def claim_next_llm(conn, slot=LLM_SLOT):
    """AI 단계 대기 작업 1건을 집는다(슬롯 1). 없거나 슬롯이 차 있으면 None."""
    with queue.tx(conn):
        cur = conn.execute(
            f"UPDATE jobs SET queue_state='running', slot_id=?, hold_reason=NULL, started_at={queue.NOW}, "
            f"updated_at={queue.NOW} WHERE id=(SELECT id FROM jobs WHERE queue_state='waiting' "
            "AND state='topic' AND plan_path IS NULL ORDER BY priority DESC, id LIMIT 1) "
            "AND queue_state='waiting' "
            "AND NOT EXISTS (SELECT 1 FROM jobs WHERE queue_state='running' AND slot_id=?)", (slot, slot))
        if cur.rowcount != 1:
            return None
        return conn.execute("SELECT id FROM jobs WHERE queue_state='running' AND slot_id=?", (slot,)).fetchone()[0]


def _topic_prompt(rules, cues):
    return [
        "아래 자막에서 쇼츠로 만들 소재 후보를 3개 고른다. 구간은 자막 시각(초) 범위 안이어야 하고 "
        "서로 겹치지 않는다. 각 후보에 후킹 대사(원문 그대로)·결 태그·이유를 쓴다.",
        "[구성 규칙]\n" + rules,
        "[자막]\n" + _fmt_cues(cues),
    ]


def _flow_prompt(rules, cues, start, end):
    part = [c for c in cues if c[1] > start and c[0] < end]
    return [
        "선택한 구간의 자막으로 쇼츠 흐름을 4줄로 정리하고, 후킹 대사 1개와 제목 후보 3개를 쓴다.",
        "[구성 규칙]\n" + rules,
        f"[구간 {start:.1f}-{end:.1f}초 자막]\n" + _fmt_cues(part),
    ]


def _check_candidates(data, cues):
    """구간이 자막 범위 안인지·역전 여부. 후보 수는 상한으로 자른다."""
    lo, hi = cues[0][0], max(c[1] for c in cues)
    good = [c for c in data["candidates"][:CANDIDATES_MAX]
            if 0 <= c["start"] < c["end"] and c["start"] >= lo - 1 and c["end"] <= hi + 1]
    if not good:
        raise llm.LlmHold("held_human", "AI 소재 후보의 구간이 자막 범위를 벗어남")
    return good


def _run_topic(conn, job_id, root, cues, rules):
    data = llm.call(conn, job_id, "topic", _topic_prompt(rules, cues), TOPIC_SCHEMA)
    good = _check_candidates(data, cues)
    with queue.tx(conn):
        for c in good:
            conn.execute("INSERT INTO topics(job_id, source_start, source_end, hook_line, tone_primary) "
                         "VALUES (?,?,?,?,?)", (job_id, c["start"], c["end"], c["hook_line"], c["tone_primary"]))
    return f"소재 후보 {len(good)}개 생성", GATE1_REASON


def _run_flow(conn, job_id, root, cues, rules):
    start, end = conn.execute("SELECT source_start, source_end FROM topics WHERE job_id=? AND used=1",
                              (job_id,)).fetchone()
    data = llm.call(conn, job_id, "flow", _flow_prompt(rules, cues, start, end), FLOW_SCHEMA)
    d = job_dir(root, job_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / FLOW_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return "흐름·제목 후보 생성", GATE2_REASON


def _plan_prompt(rules, cues, choice):
    start, end = choice["start"], choice["end"]
    part = [c for c in cues if c[1] > start and c[0] < end]
    return [
        "선택한 흐름으로 쇼츠 컷 목록을 만든다. dialog 컷은 원본 대사 구간(src_start~src_end 초), narration 컷은 "
        "화면으로 쓸 원본 구간(src_start~src_end)과 나레이션 문장(tts_text)·자막 줄(sub_lines, 줄당 16자 이내)을 쓴다. "
        "첫 컷은 후킹 dialog, 둘째는 narration, 마지막은 dialog 로 끝낸다. 총 길이는 20~59초. "
        "dialog 컷의 tts_text·sub_lines 는 비운다. 제목은 두 줄(줄당 12자 이내).",
        "[구성 규칙]\n" + rules,
        "[선택]\n" + json.dumps({k: choice[k] for k in ("title", "hook_line", "flow")}, ensure_ascii=False),
        f"[구간 {start:.1f}-{end:.1f}초 자막]\n" + _fmt_cues(part),
    ]


def build_plan(src, rule_set_json, data, choice):
    """AI 출력 + DB 사실 → 컷표 dict. 권리·원본·SRT·구성은 DB 값만 쓴다(AI 출력은 못 바꾼다)."""
    meta = {
        "work": src["title"], "episode": f"E{src['episode']:02d}" if src["episode"] is not None else "",
        "title": data["title_lines"], "rule_set": json.loads(rule_set_json or "[]"),
        "source_video": Path(src["file_path"]).name, "source_srt": Path(src["srt_path"]).name,
        "origin": src["origin"], "rights_status": src["rights_status"], "srt_kind": "auto",
        "production_mode": "auto", "template": "topbottom",
        "plan": {"topic": data["topic"], "conflict_type": data["conflict_type"], "flow": choice["flow"],
                 "hook": {"time": f"{int(data['hook_time'] // 60):02d}:{data['hook_time'] % 60:04.1f}",
                          "line": data["hook_line"]},
                 "ending": data["ending"]},
        # sensitive_review 는 넣지 않는다: 사람이 채우기 전에는 P0-1 로 기획 승인이 막힌다(S11-2)
    }
    cuts, beat = [], 0
    for i, c in enumerate(data["cuts"][:CUTS_MAX], 1):
        if c["kind"] == "dialog":
            cuts.append({"no": i, "kind": "dialog", "src_start": c["src_start"], "src_end": c["src_end"]})
        else:
            beat += 1
            cuts.append({"no": i, "kind": "narration", "beat": beat, "label": c["label"],
                         "src_start": c["src_start"], "quiet_end": c["src_end"],
                         "tts_text": c["tts_text"], "sub_lines": c["sub_lines"]})
    return {"version": 1, "meta": meta, "cuts": cuts}


def _run_plan(conn, job_id, root, cues, rules):
    d = job_dir(root, job_id)
    choice = json.loads((d / CHOICE_FILE).read_text(encoding="utf-8"))
    row = conn.execute(
        "SELECT w.title, s.episode, s.file_path, s.srt_path, s.origin, s.rights_status, j.rule_set FROM jobs j "
        "JOIN sources s ON s.id = j.source_id JOIN works w ON w.id = s.work_id WHERE j.id=?",
        (job_id,)).fetchone()
    cols = ("title", "episode", "file_path", "srt_path", "origin", "rights_status", "rule_set")
    src = dict(zip(cols, row))
    try:
        video = paths.resolve_in(root, src["file_path"])
    except paths.PathRejected as e:
        raise StepHold("held_human", f"원본 경로 거부: {e}") from None
    plan_dir = video.parent  # 엔진은 source_video 를 컷표 폴더 안에서만 연다
    plan_file = plan_dir / PLAN_FILE
    if plan_file.exists():
        raise StepHold("held_human", "원본 폴더에 컷표.json 이 이미 있다 — 덮어쓰지 않음")
    data = llm.call(conn, job_id, "plan", _plan_prompt(rules, cues, choice), PLAN_SCHEMA)
    if len(data["title_lines"]) != 2 or not data["cuts"]:
        raise llm.LlmHold("held_human", "AI 컷표 초안 형식 오류(제목 두 줄·컷 필요)")
    plan = build_plan(src, src["rule_set"], data, choice)
    plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    rel = plan_file.relative_to(Path(os.path.realpath(root))).as_posix()
    try:
        conn.execute(f"UPDATE jobs SET plan_path=?, updated_at={queue.NOW} WHERE id=?", (rel, job_id))
    except sqlite3.IntegrityError:  # 같은 컷표를 쓰는 활성 작업이 있음
        plan_file.unlink(missing_ok=True)
        raise StepHold("held_human", "같은 컷표 경로를 쓰는 진행 중 작업이 있다") from None
    r = subproc.run([sys.executable, str(ENGINE), "check", str(plan_file)], timeout=CHECK_TIMEOUT)
    note = "" if r.returncode == 0 else f" · 엔진 check 오류 있음(종료 코드 {r.returncode}) — 컷표 수정 필요"
    return "컷표 초안 생성", PLAN_REASON + note


def choose_flow(conn, job_id, root, title_index):
    """관문 ② 사람 선택: 제목 후보 하나를 정해 선택.json 을 쓰고 plan 단계를 위해 waiting 으로 돌린다. 성공 True."""
    with queue.tx(conn):
        ok = conn.execute("SELECT 1 FROM jobs WHERE id=? AND state='topic' AND queue_state='held_human' "
                          "AND plan_path IS NULL AND hold_reason=?", (job_id, GATE2_REASON)).fetchone()
        used = conn.execute("SELECT source_start, source_end FROM topics WHERE job_id=? AND used=1",
                            (job_id,)).fetchone()
        if ok is None or used is None:
            return False
        try:
            flow = json.loads((job_dir(root, job_id) / FLOW_FILE).read_text(encoding="utf-8"))
            if int(title_index) < 0:
                return False
            title = flow["titles"][int(title_index)]
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            return False
        choice = {"title": title, "hook_line": flow["hook_line"], "flow": flow["flow"],
                  "start": used[0], "end": used[1]}
        (job_dir(root, job_id) / CHOICE_FILE).write_text(json.dumps(choice, ensure_ascii=False, indent=2),
                                                         encoding="utf-8")
        conn.execute(f"UPDATE jobs SET queue_state='waiting', hold_reason=NULL, updated_at={queue.NOW} WHERE id=?",
                     (job_id,))
        conn.execute("INSERT INTO job_events(job_id, from_state, to_state, message) VALUES (?,?,?,?)",
                     (job_id, "topic", "topic", f"관문 ② 제목 {int(title_index) + 1}번 선택"))
        return True


def run_step(conn, job_id, root):
    """집은(running) AI 단계 작업 1건 실행. 결과는 항상 queue.transition 으로 남긴다. (state, queue_state)."""
    def hold(qs, reason, msg):
        queue.transition(conn, job_id, "topic", qs, message=msg, hold_reason=reason[:300])
        return "topic", qs

    try:
        step = next_step(conn, job_id, root)
        if step is None:
            return hold("held_human", "다음 단계 없음 — 소재 선택 또는 후속 작업 필요", "AI 단계 없음")
        rule_set, srt_path = _job(conn, job_id)
        cues = _read_srt(root, srt_path)
        msg, reason = {"topic": _run_topic, "flow": _run_flow, "plan": _run_plan}[step](conn, job_id, root, cues, _rules_text(rule_set))
        return hold("held_human", reason, msg)
    except StepHold as e:
        return hold(e.queue_state, e.reason, "AI 단계 보류")
    except llm.LlmHold as e:
        return hold(e.queue_state, e.reason, "AI 단계 보류")
    except Exception as e:  # poison job 방지: 어떤 예외도 failed 로 닫는다
        try:
            queue.transition(conn, job_id, "failed", "finished", message="AI 단계 내부 오류",
                             error=repr(e)[:300])
        except Exception:
            pass
        return "failed", "finished"


def choose_topic(conn, job_id, topic_id):
    """관문 ① 사람 선택: 후보 하나를 정하고 flow 단계를 위해 waiting 으로 돌린다. 성공 True."""
    with queue.tx(conn):
        row = conn.execute("SELECT 1 FROM jobs WHERE id=? AND state='topic' AND queue_state='held_human' "
                           "AND plan_path IS NULL AND hold_reason=?", (job_id, GATE1_REASON)).fetchone()
        if row is None:
            return False
        if conn.execute("SELECT 1 FROM topics WHERE id=? AND job_id=?", (topic_id, job_id)).fetchone() is None:
            return False
        conn.execute("UPDATE topics SET used=0 WHERE job_id=?", (job_id,))
        conn.execute("UPDATE topics SET used=1 WHERE id=?", (topic_id,))
        conn.execute(f"UPDATE jobs SET queue_state='waiting', hold_reason=NULL, updated_at={queue.NOW} WHERE id=?",
                     (job_id,))
        conn.execute("INSERT INTO job_events(job_id, from_state, to_state, message) VALUES (?,?,?,?)",
                     (job_id, "topic", "topic", f"관문 ① 소재 {topic_id} 선택"))
        return True
