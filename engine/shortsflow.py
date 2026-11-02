#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shortsflow — 쇼츠 설계도 엔진 (표준 라이브러리 + ffmpeg/ffprobe)

컷표.json 한 파일을 기준으로 검증 → TTS 계획 → 음성 실측 → 재계산 → 제작(자동화 / 캡컷 넘기기).
규칙 ID(C5-10, A10-5, S11-3 …)는 rules/common.md 와 같다.

  python shortsflow.py check       컷표.json
  python shortsflow.py tts-plan    컷표.json
  python shortsflow.py tts-measure 컷표.json [--dir tts]
  python shortsflow.py recalc      컷표.json
  python shortsflow.py approve     컷표.json [--by 이름]            # 기획 확정(사람). 이게 있어야 아래 3개가 동작
  python shortsflow.py render      컷표.json [--out 숏폼.mp4]      # 형태 A: 자동화
  python shortsflow.py handoff     컷표.json [--out 캡컷패키지]      # 형태 B: 캡컷 넘기기
  python shortsflow.py delegate    컷표.json [--out 위임패키지]      # 형태 C: AI 어시스턴트 위임
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TAIL = 0.2          # A10-5 나레이션 꼬리
DEFAULT_CPS = 7.0   # 글자 수 ÷ 7 (생성 전 추정)
MIN_SUB, MAX_SUB, MAX_CPS = 0.8, 4.0, 8.0   # C5-10
DROP_FRAG = 0.3     # C5-11
LINE_CHARS = 14     # 기준표
ORIG_GAIN = 0.15    # A10-8
LOUD_I = -14.0      # A10-12 목표 통합 음량(LUFS)
LOUD_TP = -1.0      # A10-13 true peak 상한(dBTP)
LOUD_LRA = 11.0     # 미확인(A10-14) — loudnorm 기본값. 입력 LRA가 크면 dynamic 으로 바뀐다
LOUD_TOL = 1.0      # 미확인(A10-12) — 잠정 ±1 LU, 벗어나면 warn 만 (D1)
SYNC_TOL = 0.01     # C5-9 싱크 환산 오차(반올림) — 잠정값
LEAD = 0.1          # P8-3 첫 음절 앞 여유
TAILROOM = 0.2      # P8-3 끝 음절 뒤 여유
NEAR_GAP = 0.3      # P8-3 이웃 줄 간격이 이보다 작으면 중점에서 자른다
P82_NOTE = "자동 받아쓰기 SRT — 컷 경계 ±0.5초 확인 필요"
LEN_MIN, LEN_MAX = 20.0, 59.0   # C7-1
HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------- 시간·글자
def nospace(t):
    return len(re.sub(r"\s", "", t or ""))


def parse_time(v):
    """12.5 | '12:03.4' | '1:02:03.4' -> 초"""
    if isinstance(v, (int, float)):
        return float(v)
    sec = 0.0
    for p in str(v).strip().split(":"):
        sec = sec * 60 + float(p.replace(",", "."))
    return sec


def fmt_ts(sec):
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:04.1f}" if h else f"{int(m):02d}:{s:04.1f}"


def srt_ts(sec):
    sec = max(0.0, sec)
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass_ts(sec):
    cs = int(round(max(0.0, sec) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ---------------------------------------------------------------- SRT
def parse_srt(path):
    txt = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    out = []
    pat = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
    for block in re.split(r"\n\s*\n", txt.strip()):
        lines = block.split("\n")
        for i, ln in enumerate(lines):
            m = pat.match(ln.strip())
            if not m:
                continue
            g = m.groups()
            s = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3].ljust(3, "0")[:3]) / 1000
            e = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7].ljust(3, "0")[:3]) / 1000
            text = " ".join(x.strip() for x in lines[i + 1:] if x.strip())
            out.append((s, e, text))
            break
    return out


def wrap(text, width=LINE_CHARS):
    """공백 제외 width+2 를 넘으면 균형 있게 두 줄로."""
    text = text.strip()
    if nospace(text) <= width + 2:
        return [text]
    words = text.split(" ")
    if len(words) == 1:
        mid = len(text) // 2
        return [text[:mid], text[mid:]]
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = abs(nospace(a) - nospace(b))
        if best is None or score < best[0]:
            best = (score, a, b)
    return [best[1], best[2]]


def write_srt(path, subs):
    out = []
    for i, (s, e, tx) in enumerate(subs, 1):
        out.append(f"{i}\n{srt_ts(s)} --> {srt_ts(e)}\n" + "\n".join(wrap(tx)) + "\n")
    Path(path).write_text("\n".join(out), encoding="utf-8")


# ---------------------------------------------------------------- 계획 읽기·계산
TIME_KEYS = ("src_start", "src_end", "quiet_end", "visual_start")


def load_plan(path):
    p = Path(path).resolve()
    plan = json.loads(p.read_text(encoding="utf-8"))
    plan["_hash"] = hashlib.sha1(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    plan["_dir"] = p.parent
    for c in plan["cuts"]:
        for k in TIME_KEYS:
            if k in c and c[k] is not None:
                c[k] = parse_time(c[k])
    return plan


def tts_hash(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:10]


def load_clips(plan):
    f = plan["_dir"] / "tts_clips.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8")).get("clips", {})
    return {}


NO_TTS = "no-tts-clips"   # tts_clips.json 이 없을 때의 고정 값


def tts_state_hash(plan):
    """승인 대상인 음성 실측 길이(tts_clips.json)의 digest. 들여쓰기·키 순서와 무관하게 비트별 길이만 본다."""
    f = plan["_dir"] / "tts_clips.json"
    if not f.exists():
        return NO_TTS
    clips = json.loads(f.read_text(encoding="utf-8")).get("clips", {})
    norm = {str(b): round(float(v.get("duration", 0)), 3) for b, v in clips.items()}
    return hashlib.sha1(json.dumps(norm, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def resolve(plan, clips=None):
    """컷마다 길이·타임라인 시각을 확정한다. (resolved, issues)"""
    issues = []
    clips = clips or {}
    cps = plan["meta"].get("cps") or DEFAULT_CPS
    t, out = 0.0, []
    for c in plan["cuts"]:
        r = dict(c)
        no = c["no"]
        if c["kind"] == "narration":
            clip = clips.get(str(c.get("beat")))
            if clip:
                voice = clip["duration"]
                r["measured"] = True
                if clip.get("text_hash") and clip["text_hash"] != tts_hash(c.get("tts_text")):
                    issues.append(("warn", "A10-11", f"컷 {no}: 음성 생성 뒤 TTS 텍스트가 바뀌었다 → 재생성 필요"))
            else:
                voice = nospace(c.get("tts_text")) / cps
                r["measured"] = False
            r["voice_sec"] = round(voice, 3)
            r["dur"] = round(voice + TAIL, 3)
            r["src_end"] = c["src_start"] + r["dur"]
            if c.get("quiet_end") is not None and r["src_end"] > c["quiet_end"] + 1e-6:
                issues.append(("error", "A10-7",
                               f"컷 {no}: 음성({r['dur']:.1f}초)이 고른 화면({c['quiet_end'] - c['src_start']:.1f}초)보다 길다 → 대사 없는 다른 화면으로 채우기"))
        else:
            r["dur"] = round(c["src_end"] - c["src_start"], 3)
            if r["dur"] <= 0:
                issues.append(("error", "P8-1", f"컷 {no}: 끝이 시작보다 빠르다"))
        r["tl_start"] = round(t, 3)
        t += r["dur"]
        r["tl_end"] = round(t, 3)
        out.append(r)
    return out, issues


def build_subs(plan, resolved, src_lines):
    corr = plan["meta"].get("corrections", {}) or {}

    def fix(tx):
        for a, b in corr.items():
            tx = tx.replace(a, b)
        return tx

    subs = []
    for r in resolved:
        if r["kind"] == "dialog":
            if r.get("sub_off"):
                continue
            a, b, T = r["src_start"], r["src_end"], r["tl_start"]
            for s, e, tx in src_lines:
                if e <= a or s >= b:
                    continue
                ns, ne = max(s, a) - a + T, min(e, b) - a + T
                if ne - ns < DROP_FRAG:
                    continue
                subs.append([ns, ne, fix(tx)])
        else:
            lines = r.get("sub_lines") or ([r["tts_text"]] if r.get("tts_text") else [])
            if not lines:
                continue
            w = [max(1, nospace(x)) for x in lines]
            x = r["tl_start"]
            for line, wi in zip(lines, w):
                d = r["voice_sec"] * wi / sum(w)
                subs.append([x, x + d, line])
                x += d
    subs.sort(key=lambda s: s[0])
    for i in range(len(subs) - 1):
        if subs[i][1] > subs[i + 1][0] - 0.02:
            subs[i][1] = max(subs[i][0] + 0.1, subs[i + 1][0] - 0.02)
    return [tuple(s) for s in subs]


def total_len(resolved):
    return resolved[-1]["tl_end"] if resolved else 0.0


# ---------------------------------------------------------------- 검증
def check(plan, resolved, subs, issues=None, max_narr_pct=35.0, src_lines=None):
    """(level, rule, msg) 목록. error 가 있으면 제작하지 않는다."""
    iss = list(issues or [])
    meta, cuts = plan["meta"], resolved
    total = total_len(cuts)
    if not cuts:
        return [("error", "C2-1", "컷이 없다")]

    if meta.get("rights_status", "unverified") == "unverified":
        iss.append(("warn", "R9-1", "권리 미확인 — 영상 제작까지만. 업로드 예약 금지"))
    if not (LEN_MIN <= total <= LEN_MAX):
        iss.append(("error", "C7-1", f"총 길이 {total:.1f}초 — 범위 {LEN_MIN:.0f}~{LEN_MAX:.0f}초 밖"))
    if len(cuts) > 1 and not (cuts[0]["kind"] == "dialog" and cuts[1]["kind"] == "narration"):
        iss.append(("warn", "C3-4", "후킹(컷 1) 바로 뒤(컷 2)가 나레이션이 아니다"))
    if cuts[-1]["kind"] == "narration":
        iss.append(("error", "C5-3", "마지막 컷이 나레이션이다 — 원작 대사·얼굴로 끝내야 한다"))
    narr = sum(c["dur"] for c in cuts if c["kind"] == "narration")
    pct = narr / total * 100 if total else 0
    if pct > max_narr_pct:
        iss.append(("warn", "C5-5", f"나레이션 비중 {pct:.0f}% > {max_narr_pct:.0f}%"))
    for c in cuts:
        no = c["no"]
        if c["kind"] != "narration" and c["dur"] > 8:
            iss.append(("warn", "C5-8", f"컷 {no}: {c['dur']:.1f}초 — 8초 초과 사유 필요"))
        if c["kind"] == "narration":
            if not c.get("tts_text"):
                iss.append(("error", "A10-1", f"컷 {no}: tts_text 가 없다"))
            if not c.get("sub_lines"):
                iss.append(("warn", "A10-1", f"컷 {no}: sub_lines 가 없어 TTS 문장이 자막이 된다"))
            if not c.get("beat"):
                iss.append(("error", "A10-2", f"컷 {no}: beat 번호가 없다"))
            if not c["measured"]:
                iss.append(("warn", "A10-5", f"컷 {no}: 음성 실측 전(추정 길이) — tts-measure 후 recalc"))
        s = c.get("sensitive")
        if s:
            if c["tl_start"] < 2.0 or c["tl_end"] > total - 3.0:
                iss.append(("error", "S11-3", f"컷 {no}: 민감 장면이 후킹 0~2초 또는 끝 3초에 걸린다"))
            if s.get("method") == "bypass":
                vs = c.get("visual_start")
                if vs is None:
                    iss.append(("error", "S11-6", f"컷 {no}: 우회인데 visual_start(대체 화면 시작)가 없다"))
                elif abs(vs - c["src_start"]) < 0.05:
                    iss.append(("error", "S11-6", f"컷 {no}: 우회인데 대체 화면이 원래 화면과 같다"))
            if s.get("method") not in ("bypass", "blur", "delete_audio_only"):
                iss.append(("error", "S11-1", f"컷 {no}: sensitive.method 는 bypass | blur | delete_audio_only"))
    for a, b in zip(cuts, cuts[1:]):
        if abs(b["tl_start"] - a["tl_end"]) > 0.001:
            iss.append(("error", "A10-6", f"컷 {a['no']}~{b['no']} 사이에 간격이 있다"))
    for i, (s, e, tx) in enumerate(subs, 1):
        d = e - s
        n = nospace(tx)
        if d < MIN_SUB:
            iss.append(("warn", "C5-10", f"자막 {i}: {d:.2f}초 < {MIN_SUB}초 «{tx[:12]}»"))
        if d > MAX_SUB:
            iss.append(("warn", "C5-10", f"자막 {i}: {d:.2f}초 > {MAX_SUB}초"))
        if d > 0 and n / d > MAX_CPS:
            iss.append(("warn", "C5-10", f"자막 {i}: {n / d:.1f}자/초 > {MAX_CPS}"))
        if any(nospace(x) > LINE_CHARS + 2 for x in wrap(tx)):
            iss.append(("warn", "C5-9", f"자막 {i}: 한 줄 16자 초과 «{tx[:14]}»"))
    if meta.get("srt_kind") != "manual":
        iss.append(("warn", "P8-2", P82_NOTE))
    iss += sync_issues(cuts, subs, src_lines)
    iss += tail_room_issues(cuts, src_lines)
    t = meta.get("title") or []
    if len(t) != 2 or any(nospace(x) > 12 for x in t) or sum(nospace(x) for x in t) > 24:
        iss.append(("warn", "C4-3", "제목은 두 줄, 줄당 12자·합 24자 이내"))
    return iss


def sync_issues(cuts, subs, src_lines):
    """C5-9: 자막 시작이 그 컷의 소리 시작보다 앞서면 error. 나레이션=tl_start, 대사=원본 SRT 줄 시작 환산."""
    iss = []
    for i, (s, e, tx) in enumerate(subs, 1):
        cut = max(cuts, key=lambda c: min(e, c["tl_end"]) - max(s, c["tl_start"]))
        if min(e, cut["tl_end"]) - max(s, cut["tl_start"]) <= 0:
            continue
        if cut["kind"] == "narration":
            starts = [cut["tl_start"]]
        elif src_lines:
            a, b, T = cut["src_start"], cut["src_end"], cut["tl_start"]
            starts = [max(ls, a) - a + T for ls, le, _ in src_lines if le > a and ls < b]
        else:
            continue
        if starts and s < min(starts, key=lambda x: abs(x - s)) - SYNC_TOL:
            iss.append(("error", "C5-9", f"자막 {i}: 소리보다 먼저 뜬다 (컷 {cut['no']}) «{tx[:12]}»"))
    return iss


def tail_room_issues(cuts, src_lines):
    """P8-3: 대사 컷은 첫 음절 0.1초 전에 열고 끝 0.2초 후에 닫는다. 이웃 줄 간격 0.3초 미만이면 중점까지 허용. 검사만 한다(D3)."""
    iss = []
    if not src_lines:
        return iss
    lines = sorted(src_lines, key=lambda x: x[0])
    for c in cuts:
        if c["kind"] != "dialog":
            continue
        a, b = c["src_start"], c["src_end"]
        for k, (ls, le, tx) in enumerate(lines):
            if le <= a or ls >= b:
                continue
            if ls >= a:
                need = ls - LEAD
                if k > 0 and ls - lines[k - 1][1] < NEAR_GAP:
                    need = max(need, (ls + lines[k - 1][1]) / 2)
                if a > need + 1e-3:
                    iss.append(("error", "P8-3", f"컷 {c['no']}: «{tx[:12]}» 첫 음절 앞 여유가 {ls - a:.2f}초 (0.1초 필요)"))
            need = le + TAILROOM
            if k + 1 < len(lines) and lines[k + 1][0] - le < NEAR_GAP:
                need = min(need, (le + lines[k + 1][0]) / 2)
            if b < need - 1e-3:
                iss.append(("error", "P8-3", f"컷 {c['no']}: «{tx[:12]}» 끝 여유가 {b - le:.2f}초 (0.2초 필요)"))
    return iss


def print_issues(iss):
    if not iss:
        print("✓ 문제 없음")
        return 0
    for lv, rule, msg in sorted(iss, key=lambda x: x[0] != "error"):
        print(f"{'✗' if lv == 'error' else '△'} [{rule}] {msg}")
    ne = sum(1 for i in iss if i[0] == "error")
    print(f"— error {ne}, warn {len(iss) - ne}")
    return 1 if ne else 0


# ---------------------------------------------------------------- 파일 이름·TTS
def beat_filename(c):
    label = re.sub(r"[^\w가-힣-]", "", c.get("label") or "나레이션")
    return f"{int(c['beat']):02d}_{label}"


TTS_WATCH = ["얼탱이", "ㅋ", "ㅎㅎ", "TODO", "임시", "??", "…", "..."]


def tts_text_issues(plan):
    out = []
    cuts = plan["cuts"]
    narr = [c for c in cuts if c["kind"] == "narration"]
    for c in narr:
        tx = c.get("tts_text", "")
        for w in TTS_WATCH:
            if w in tx:
                out.append(("warn", "A10-3", f"비트 {c['beat']}: «{w}» — 발음이 어색하거나 미확정일 수 있다"))
        if "  " in tx:
            out.append(("warn", "A10-3", f"비트 {c['beat']}: 공백 두 칸"))
        if re.search(r"\d", tx):
            out.append(("warn", "A10-1", f"비트 {c['beat']}: 숫자 — 읽는 방식으로 적었나 확인 (예: 삼 초)"))
        if nospace(tx) > 28:
            out.append(("warn", "기준표", f"비트 {c['beat']}: {nospace(tx)}자 — 한 문장 28자 한계 초과"))
    if cuts and cuts[-1]["kind"] == "narration":
        out.append(("error", "C5-3", "끝 나레이션 삭제 필요"))
    return out


def cmd_tts_plan(plan):
    d = plan["_dir"]
    narr = [c for c in plan["cuts"] if c["kind"] == "narration"]
    (d / "tts").mkdir(exist_ok=True)
    cps = plan["meta"].get("cps") or DEFAULT_CPS
    md = ["# 나레이션 (TTS 줄 / 자막 줄 분리)", "",
          f"설정: `{json.dumps(plan['meta'].get('tts', {}), ensure_ascii=False)}`  — 한 영상 안에서 바꾸지 않는다 (A10-4)", "",
          "| 비트 | 파일명 | TTS 문장 | 글자 | 추정 초 | 자막 줄 |", "|---|---|---|---|---|---|"]
    lines = []
    for c in narr:
        base = beat_filename(c)
        n = nospace(c["tts_text"])
        md.append(f"| {c['beat']} | `{base}.mp3` | {c['tts_text']} | {n} | {n / cps:.1f} | {' / '.join(c.get('sub_lines', []))} |")
        lines.append(f"{base}\t{c['tts_text']}")
    (d / "나레이션.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (d / "tts" / "TTS_줄.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"나레이션.md, tts/TTS_줄.txt 작성 ({len(narr)}비트)")
    print("→ TTS_줄.txt 의 한 줄씩을 TTS 도구에 넣어 위 파일명으로 저장하세요 (비트당 파일 하나).")
    return print_issues(tts_text_issues(plan))


def ffprobe_duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    if r.returncode or not r.stdout.strip():
        raise RuntimeError(f"ffprobe 실패: {path}\n{r.stderr}")
    return float(r.stdout.strip())


def cmd_tts_measure(plan, tts_dir):
    d = plan["_dir"]
    tdir = (d / tts_dir) if not Path(tts_dir).is_absolute() else Path(tts_dir)
    narr = {str(c["beat"]): c for c in plan["cuts"] if c["kind"] == "narration"}
    clips, miss = {}, []
    for beat, c in narr.items():
        found = sorted(tdir.glob(f"{int(beat):02d}_*.*"))
        found = [f for f in found if f.suffix.lower() in (".mp3", ".wav", ".m4a")]
        if not found:
            miss.append(beat)
            continue
        dur = ffprobe_duration(found[-1])
        n = nospace(c["tts_text"])
        clips[beat] = {"file": str(found[-1].relative_to(d)) if found[-1].is_relative_to(d) else str(found[-1]),
                       "duration": round(dur, 3), "chars": n, "cps": round(n / dur, 2) if dur else 0,
                       "text_hash": tts_hash(c["tts_text"])}
    chars = sum(v["chars"] for v in clips.values())
    secs = sum(v["duration"] for v in clips.values())
    mean = round(chars / secs, 2) if secs else None
    out = {"settings": plan["meta"].get("tts", {}), "cps_mean": mean, "clips": clips}
    (d / "tts_clips.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for b, v in clips.items():
        print(f"비트 {b}: {v['duration']:.2f}초 / {v['chars']}자 = {v['cps']}자/초  {Path(v['file']).name}")
    if mean:
        print(f"\n이 목소리·속도의 평균 {mean}자/초 → 추정식을 `글자 수 ÷ {mean}` 로 교체 (meta.cps = {mean})")
    if miss:
        print(f"✗ 음성 파일이 없는 비트: {', '.join(miss)}")
        return 1
    return 0


# ---------------------------------------------------------------- recalc
def do_recalc(plan, write=True):
    clips = load_clips(plan)
    resolved, iss = resolve(plan, clips)
    src_srt = plan["meta"].get("source_srt")
    lines = parse_srt(plan["_dir"] / src_srt) if src_srt and (plan["_dir"] / src_srt).exists() else []
    if not lines and any(c["kind"] == "dialog" for c in plan["cuts"]):
        iss.append(("error", "P8-1", "원본 SRT 가 없다 — 자막 시각의 근거가 없다"))
    subs = build_subs(plan, resolved, lines)
    iss = check(plan, resolved, subs, iss, plan["meta"].get("max_narration_pct", 35.0), lines)
    if write:
        d = plan["_dir"]
        write_srt(d / "자막.srt", subs)
        clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in resolved]
        (d / "컷표.resolved.json").write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
        rows = ["| 번호 | 종류 | 원본 시작 | 원본 끝 | 타임라인 | 길이 | 들리는 것 | 얼굴(x,y,h) | 메모 |", "|---|---|---|---|---|---|---|---|---|"]
        for r in resolved:
            heard = f"[나레이션 {r['beat']}] {r['tts_text']}" if r["kind"] == "narration" else "(원음)"
            memo = r.get("note", "")
            if r.get("sensitive"):
                s = r["sensitive"]
                memo += f" ⚠민감:{s.get('type', '')}/{s.get('method')}" + (f" 화면={fmt_ts(r['visual_start'])}" if r.get("visual_start") is not None else "")
            est = "" if r["kind"] != "narration" or r["measured"] else " (추정)"
            rows.append(f"| {r['no']} | {r['kind']} | {fmt_ts(r['src_start'])} | {fmt_ts(r['src_end'])} | "
                        f"{r['tl_start']:.1f}~{r['tl_end']:.1f} | {r['dur']:.1f}{est} | {heard} | "
                        + (f"{r['face']['x']},{r['face']['y']},{r['face'].get('h', '-')}" if r.get("face") else "—") + f" | {memo} |")
        rows.append(f"\n총 길이 **{total_len(resolved):.1f}초**, 컷 {len(resolved)}개, 컷 길이 합 = 총 길이 ✓ (간격 0)")
        if plan["meta"].get("srt_kind") != "manual":
            rows = [f"> {P82_NOTE}", ""] + rows
        (d / "편집지시서_표.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return resolved, subs, iss


# ---------------------------------------------------------------- ffmpeg
def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError("ffmpeg 실패:\n" + " ".join(map(str, cmd)) + "\n" + r.stderr[-1800:])


def run_capture(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError("ffmpeg 실패:\n" + " ".join(map(str, cmd)) + "\n" + r.stderr[-1800:])
    return r.stderr


def parse_loudnorm(stderr):
    """loudnorm print_format=json 의 마지막 JSON 블록을 읽는다."""
    blocks = re.findall(r"\{[^{}]*\}", stderr)
    if not blocks:
        raise RuntimeError("loudnorm 측정값을 읽지 못했다")
    out = {}
    for k, v in json.loads(blocks[-1]).items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            out[k] = v
    return out


def loudnorm_base():
    return f"loudnorm=I={LOUD_I:g}:TP={LOUD_TP:g}:LRA={LOUD_LRA:g}"


def measure_loudness(work, name):
    """pass1: 합친 영상의 오디오를 측정한다."""
    err = run_capture(["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", name, "-map", "0:a",
                       "-af", loudnorm_base() + ":print_format=json", "-f", "null", "-"], cwd=work)
    return parse_loudnorm(err)


def vf(w, h, fps):
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p")


def probe_size(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    w, h = r.stdout.strip().split(",")[:2]
    return int(w), int(h)


def crop_plan(face, ow, oh, W, H, v):
    """v6.1: 기본 확대 → 얼굴 크기 확대 → 중앙 배치 보정 → 상한. face = {x,y,h} 원본 대비 0~1."""
    s0 = max(W / ow, H / oh)
    cx, cy = face["x"] * ow, face["y"] * oh
    fh = face.get("h", 0) * oh
    s1 = (v.get("face_ratio", 0.34) * H / fh) if fh > 0 else s0
    edge = min(cx, ow - cx)
    s2 = W / (2 * edge) if edge > 0 else s0
    want = max(s0, s1, s2)
    smax = max(v.get("max_scale", 1.5), s0)
    sc = min(want, smax)
    reason = "기본" if want == s0 else ("얼굴크기" if want == s1 else "중앙배치보정")
    clamped = want > smax + 1e-9
    if clamped:
        reason = "상한도달"
    sw, sh = max(W, round(ow * sc)), max(H, round(oh * sc))
    x0 = min(max(cx * sc - W / 2, 0), sw - W)
    y0 = min(max(cy * sc - H * v.get("face_y", 0.42), 0), sh - H)
    return {"scale": round(sc, 3), "x0": round(x0), "y0": round(y0), "sw": sw, "sh": sh,
            "reason": reason, "clamped": clamped}


def video_filter(plan, r, size, fps, tpl, dims):
    w, h = size
    v = (tpl or {}).get("video", {})
    face = r.get("face")
    if tpl and v.get("fit") == "cover" and face and dims:
        c = crop_plan(face, dims[0], dims[1], w, h, v)
        r["_crop"] = c
        return f"scale={c['sw']}:{c['sh']},crop={w}:{h}:{c['x0']}:{c['y0']},setsar=1,fps={fps},format=yuv420p"
    if tpl and v.get("fit") == "cover":   # 얼굴 좌표 없음 → 가운데 기준으로 꽉 채움
        return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={fps},format=yuv420p"
    return vf(w, h, fps)


def make_segment(plan, r, out, size, fps, mix_tts, acodec, tts_dir, tpl=None, dims=None):
    src = str((plan["_dir"] / plan["meta"]["source_video"]).resolve())
    dur = r["dur"]
    w, h = size
    ac = ["-c:a", "pcm_s16le"] if acodec == "pcm" else ["-c:a", "aac", "-b:a", "192k"]
    enc = ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-ar", "48000", "-ac", "2", *ac]
    if r["kind"] == "dialog":
        vs = r.get("visual_start", r["src_start"])
        s = r.get("sensitive") or {}
        af = "aresample=48000"
        mode = r.get("audio_mode") or s.get("audio")
        if mode == "mute":
            af += ",volume=0"
        elif mode == "duck":
            af += ",volume=0.25"
        if s:   # S11-9 0.1초 페이드
            af += f",afade=t=in:d=0.1,afade=t=out:st={max(0, dur - 0.1):.3f}:d=0.1"
        af += ",apad"
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{vs:.3f}", "-t", f"{dur:.3f}", "-i", src,
               "-ss", f"{r['src_start']:.3f}", "-t", f"{dur:.3f}", "-i", src,
               "-map", "0:v:0", "-map", "1:a:0", "-vf", video_filter(plan, r, size, fps, tpl, dims), "-af", af, "-t", f"{dur:.3f}", *enc, str(out)]
    else:
        tts = None
        clip = load_clips(plan).get(str(r["beat"]))
        if mix_tts and clip:
            tts = (plan["_dir"] / clip["file"]) if not Path(clip["file"]).is_absolute() else Path(clip["file"])
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{r['src_start']:.3f}", "-t", f"{dur:.3f}", "-i", src]
        fc = f"[0:v]{video_filter(plan, r, size, fps, tpl, dims)}[v];[0:a]volume={r.get('orig_gain', ORIG_GAIN)},aresample=48000[o]"
        if tts and tts.exists():
            cmd += ["-i", str(tts)]
            fc += ";[1:a]aresample=48000,apad[t];[o][t]amix=inputs=2:duration=longest:normalize=0[a]"
        else:
            if mix_tts:
                print(f"△ 컷 {r['no']}: TTS 파일 없음 — 원음만 (드라이런)")
            fc += ";[o]anull[a]"
        cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", *enc, str(out)]
    run(cmd)


def build_flat(plan, resolved, work, size, fps, mix_tts, tpl=None):
    work.mkdir(parents=True, exist_ok=True)
    lines = []
    dims = probe_size(plan["_dir"] / plan["meta"]["source_video"])
    for r in resolved:
        seg = f"seg_{r['no']:03d}.mkv"
        make_segment(plan, r, work / seg, size, fps, mix_tts, "pcm", None, tpl, dims)
        if r.get("_crop"):
            c = r["_crop"]
            note = "  ⚠ 확대 상한 도달 — 얼굴 중앙 배치 불가, 구도 확인" if c["clamped"] else ""
            print(f"  컷 {r['no']}: 확대 {c['scale']} ({c['reason']}) x={c['x0']} y={c['y0']}{note}")
        lines.append(f"file '{seg}'")
    (work / "list.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "list.txt", "-c", "copy", "flat.mkv"], cwd=work)
    return work / "flat.mkv"


def load_template(name):
    f = HERE / "templates" / f"{name}.json"
    if not f.exists():
        raise SystemExit(f"템플릿 없음: {f}")
    return json.loads(f.read_text(encoding="utf-8"))


def find_fonts(tpl):
    found = []
    for fn in tpl["font"]["files"]:
        for sd in tpl["font"]["search_dirs"]:
            p = Path(sd) / fn
            if p.exists():
                found.append(p)
                break
    if not found:
        raise SystemExit("한글 폰트를 찾지 못했다 — templates/topbottom.json 의 font.search_dirs 에 폰트 폴더를 추가하세요")
    return found


def make_ass(tpl, title, subs, total, channel=None, caption=None):
    W, H = tpl["canvas"]
    fam = tpl["font"]["family"]
    t, s, ch = tpl["title"], tpl["subtitle"], tpl["channel"]

    def bgr(hexrgb):
        return f"&H00{hexrgb[4:6]}{hexrgb[2:4]}{hexrgb[0:2]}"

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Title,{fam},{t['size']},{bgr(t['color'])},&H000000FF,&H00000000,&H00000000,{-1 if t.get('bold') else 0},0,0,0,100,100,0,0,1,0,0,5,40,40,40,1
Style: Sub,{fam},{s['size']},{bgr(s['color'])},&H000000FF,{bgr(s['outline_color'])},&H00000000,{-1 if s.get('bold') else 0},0,0,0,100,100,0,0,1,{s['outline']},0,5,40,40,40,1
Style: Cap,{fam},{tpl.get('caption', {}).get('size', 44)},{bgr(tpl.get('caption', {}).get('color', 'FFFFFF'))},&H000000FF,&H00000000,&H00000000,0,-1,0,0,100,100,0,0,1,0,0,5,40,40,40,1
Style: Chan,{fam},{ch['size']},{bgr(ch['color'])},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,40,40,40,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    ev = []
    cols = t.get("colors") or []
    tl = [(("{\\c" + bgr(cols[n]) + "}") if n < len(cols) else "") + ln for n, ln in enumerate(title)]
    ev.append(f"Dialogue: 0,{ass_ts(0)},{ass_ts(total)},Title,,0,0,0,,{{\\pos({W // 2},{t['center_y']})}}" + "\\N".join(tl))
    if caption and tpl.get("caption"):
        ev.append(f"Dialogue: 0,{ass_ts(0)},{ass_ts(total)},Cap,,0,0,0,,{{\\pos({W // 2},{tpl['caption']['center_y']})}}{caption}")
    if channel:
        ev.append(f"Dialogue: 0,{ass_ts(0)},{ass_ts(total)},Chan,,0,0,0,,{{\\pos({W // 2},{ch['center_y']})}}{channel}")
    for a, b, tx in subs:
        ev.append(f"Dialogue: 1,{ass_ts(a)},{ass_ts(b)},Sub,,0,0,0,,{{\\pos({W // 2},{s['center_y']})}}" + "\\N".join(wrap(tx)))
    return head + "\n".join(ev) + "\n"


def cmd_render(plan, out_name, template="topbottom"):
    if not gate(plan):
        return 1
    resolved, subs, iss = do_recalc(plan)
    if print_issues(iss):
        print("error 를 고친 뒤 다시 실행하세요.")
        return 1
    if any(i[1] == "R9-1" for i in iss):
        print("※ 권리 미확인: 이 영상은 업로드하지 않는다 (R9-1)")
    tpl = load_template(template or plan["meta"].get("template", "topbottom"))
    W, H = tpl["canvas"]
    v = tpl["video"]
    work = plan["_dir"] / "_work"
    total = total_len(resolved)
    print(f"세그먼트 {len(resolved)}개 렌더…")
    flat = build_flat(plan, resolved, work, (v["w"], v["h"]), tpl["fps"], mix_tts=True, tpl=tpl)
    fonts = work / "fonts"
    fonts.mkdir(exist_ok=True)
    for f in find_fonts(tpl):
        shutil.copy(f, fonts / f.name)
    (work / "final.ass").write_text(make_ass(tpl, plan["meta"]["title"], subs, total, plan["meta"].get("channel"), plan["meta"].get("source_caption")), encoding="utf-8")
    c = tpl["colors"]
    fc = (f"[0:v]drawbox=x=0:y=0:w={W}:h={tpl['top']['h']}:color={c['top_bg']}:t=fill,"  +
          (f"drawbox=x=0:y={tpl['top']['h'] - tpl['top']['accent_h']}:w={W}:h={tpl['top']['accent_h']}:color={c['accent']}:t=fill" if tpl['top']['accent_h'] else "null") + "[bg];" +
          f"[bg][1:v]overlay={v['x']}:{v['y']}:shortest=1[o];[o]ass=final.ass:fontsdir=fonts[out]")
    out = (plan["_dir"] / out_name).resolve()
    # A10-12·14: 최종 연결 영상 1개에 loudnorm 2-pass(선형). 컷 단위 믹스(A10-8)는 건드리지 않는다.
    before = measure_loudness(work, "flat.mkv")
    afilter, fixes = [], []
    keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if all(isinstance(before.get(k), float) and abs(before[k]) != float("inf") for k in keys):
        flt = (f"{loudnorm_base()}:measured_I={before['input_i']}:measured_TP={before['input_tp']}:"
               f"measured_LRA={before['input_lra']}:measured_thresh={before['input_thresh']}:"
               f"offset={before['target_offset']}:linear=true")
        afilter = ["-af", flt + ":print_format=json"]
    else:
        print("△ [A10-12] 오디오가 무음이라 음량을 보정하지 못했다")
    err = run_capture(["ffmpeg", "-y", "-hide_banner", "-nostats", "-v", "info", "-f", "lavfi", "-i", f"color=c={c['bottom_bg']}:s={W}x{H}:r={tpl['fps']}",
                       "-i", "flat.mkv", "-filter_complex", fc, "-map", "[out]", "-map", "1:a", "-c:v", "libx264", "-crf", "18",
                       "-preset", "medium", "-pix_fmt", "yuv420p", *afilter, "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                       "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)], cwd=work)
    if afilter:
        after = parse_loudnorm(err)
        mode = "linear" if str(after.get("normalization_type", "")).lower() == "linear" else "dynamic"
        off = abs(after["output_i"] - LOUD_I) > LOUD_TOL
        fixes.append({"item": "loudness", "rule": "A10-12", "filter": flt,
                      "before": {"i": before["input_i"], "tp": before["input_tp"], "lra": before["input_lra"], "thresh": before["input_thresh"]},
                      "after": {"i": after["output_i"], "tp": after["output_tp"], "lra": after["output_lra"]},
                      "offset": before["target_offset"], "mode": mode,
                      "target": {"i": LOUD_I, "tp": LOUD_TP}, "tolerance_lu": LOUD_TOL, "out_of_tolerance": off,
                      "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        print(f"  음량 {before['input_i']:.1f} → {after['output_i']:.1f} LUFS ({mode}), TP {after['output_tp']:.1f} dBTP")
        if off:
            print(f"△ [A10-12] 보정 뒤 음량이 목표 {LOUD_I:g}±{LOUD_TOL:g} LU 밖이다 — 확인")
        if mode != "linear":
            print("△ [A10-14] 보정이 선형이 아니라 동적 모드로 바뀌었다 — 전후 값은 작업기록.json")
    (plan["_dir"] / "작업기록.json").write_text(json.dumps({"schema": 1, "auto_fixes": fixes}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ {out}  ({total:.1f}초, {W}x{H})")
    print("다음: 영상을 직접 확인하고 승인함에서 승인하세요. 업로드는 항상 사람이 합니다.")
    return 0


# ---------------------------------------------------------------- 캡컷 넘기기
def cmd_handoff(plan, out_dir, template="topbottom"):
    if not gate(plan):
        return 1
    resolved, subs, iss = do_recalc(plan)
    if print_issues(iss):
        print("error 를 고친 뒤 다시 실행하세요.")
        return 1
    tpl = load_template(template or plan["meta"].get("template", "topbottom"))
    out = (plan["_dir"] / out_dir).resolve()
    (out / "cuts").mkdir(parents=True, exist_ok=True)
    (out / "tts").mkdir(exist_ok=True)
    total = total_len(resolved)
    print("컷별 파일 생성 (TTS 미포함 — 캡컷에서 비트별로 배치)…")
    for r in resolved:
        make_segment(plan, r, out / "cuts" / f"{r['no']:02d}.mp4", (1920, 1080), tpl["fps"], False, "aac", None)
    print("참고용 미리보기(TTS 포함) 생성…")
    work = plan["_dir"] / "_work_preview"
    flat = build_flat(plan, resolved, work, (1920, 1080), tpl["fps"], mix_tts=True)
    run(["ffmpeg", "-y", "-v", "error", "-i", "flat.mkv", "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
         "-c:a", "aac", "-b:a", "192k", str(out / "미리보기.mp4")], cwd=work)
    shutil.rmtree(work, ignore_errors=True)
    for r in resolved:
        if r["kind"] == "narration":
            clip = load_clips(plan).get(str(r["beat"]))
            if clip and (plan["_dir"] / clip["file"]).exists():
                shutil.copy(plan["_dir"] / clip["file"], out / "tts" / Path(clip["file"]).name)
    shutil.copy(plan["_dir"] / "자막.srt", out / "자막.srt")
    shutil.copy(plan["_dir"] / "편집지시서_표.md", out / "편집지시서_표.md")
    (out / "캡컷체크리스트.md").write_text(handoff_checklist(plan, resolved, tpl, total, bool(iss)), encoding="utf-8")
    print(f"✓ {out}")
    print("다음: 캡컷체크리스트.md 순서대로 마무리. 시각 오차는 작업기록에 입력.")
    return 0


def handoff_checklist(plan, resolved, tpl, total, has_warn):
    v, t, s, W = tpl["video"], tpl["title"], tpl["subtitle"], tpl["canvas"][0]
    beats = [c for c in resolved if c["kind"] == "narration"]
    rows = "\n".join(f"| {c['tl_start']:.1f} | {c['dur']:.1f}초 | `tts/{Path(load_clips(plan).get(str(c['beat']), {}).get('file', beat_filename(c) + '.mp3')).name}` | {c['tts_text']} |" for c in beats)
    sens = [c for c in resolved if c.get("sensitive")]
    sens_txt = "\n".join(f"- 컷 {c['no']} ({c['sensitive'].get('type', '')}): {c['sensitive'].get('method')} — 우회 화면은 이미 반영됨, 가림(blur)·소리 처리 확인" for c in sens) or "- 민감 처리 컷 없음"
    return f"""# 캡컷 체크리스트 — {plan['meta'].get('work', '')} {plan['meta'].get('episode', '')}

총 {total:.1f}초 · 컷 {len(resolved)}개 · {'⚠ 경고 있음(편집지시서_표.md 확인)' if has_warn else '경고 없음'}
권리 상태: `{plan['meta'].get('rights_status', 'unverified')}` — unverified 면 업로드하지 않는다(R9-1)

## 1. 상하 템플릿 (캔버스 {W}x{tpl['canvas'][1]}, 9:16)

| 영역 | 위치 | 설정 |
|---|---|---|
| 위 제목 | 중심 y={t['center_y']} | 두 줄 «{' / '.join(plan['meta']['title'])}», 크기 약 {t['size']}px 굵게, 흰색 |
| 위 배경 | y 0~{tpl['top']['h']} | 색 {tpl['colors']['top_bg'].replace('0x', '#')}, 하단에 파란 선 {tpl['top']['accent_h']}px |
| 가운데 영상 | x {v['x']}, y {v['y']}, {v['w']}x{v['h']} | 16:9 영상을 폭 {v['w']}에 맞춤(잘리지 않게) |
| 아래 자막 | 중심 y={s['center_y']} | 크기 약 {s['size']}px 굵게, 흰색 + 검은 외곽선 {s['outline']}px |
| 아래 배경 | 나머지 | 색 {tpl['colors']['bottom_bg'].replace('0x', '#')} |

안전 영역: 하단 약 15%와 우측은 앱 UI가 덮는다. 자막은 위 위치를 지킨다.

## 2. 작업 순서

- [ ] 1. `미리보기.mp4`(TTS 포함, 참고용)로 완성본을 한 번 본다. 최종은 아래 컷으로 만든다
- [ ] 2. 새 프로젝트 9:16 · 위 템플릿 배경색·제목 배치
- [ ] 3. `cuts/01.mp4 …` 를 번호 순서로 가운데 영역에 이어 붙인다 (이미 길이·간격이 맞춰져 있다)
- [ ] 4. `tts/` 파일을 한 번에 가져온다 — 파일명 번호가 배치 순서
- [ ] 5. 나레이션 컷 시작점에 음성 배치 (첫 소리는 컷 시작과 함께, A10-9)
- [ ] 6. 원음: 나레이션 구간은 낮추고(15%), 대사 구간은 유지 (A10-8)
- [ ] 7. `자막.srt` 가져오기 — TTS 줄과 같은 문장, 소리보다 먼저 뜨지 않게
- [ ] 8. 민감 처리 확인
{sens_txt}
- [ ] 9. 끝 3초: 펀치 대사 + 반응 얼굴, 나레이션 없음
- [ ] 10. 내보내기 → 보관함에서 검수·승인 (업로드는 사람이)

## 3. 나레이션 배치표 (타임라인 시각 기준)

| 타임라인 시작 | 컷 길이 | 파일 | TTS 문장 |
|---|---|---|---|
{rows}

## 4. 시각 오차 기록

| 항목 | 값 |
|---|---|
| 보정한 컷 수 | |
| 최대 보정(초) | |
| 총 소요(분) | |

## 5. 합성 음성 확인 (A10-10)

- [ ] TTS 요금제의 상업적 이용 약관 확인
- [ ] 업로드 플랫폼의 AI 생성 콘텐츠 표시 정책 확인
"""


# ---------------------------------------------------------------- 기획 선행 게이트 (P0)
def plan_issues(plan):
    """P0-1: 영상을 만들기 전에 기획이 구현(기록)돼 있어야 한다."""
    m = plan["meta"]
    pl = m.get("plan") or {}
    iss = []
    if not pl.get("topic"):
        iss.append(("error", "P0-1", "기획: 소재 한 줄(meta.plan.topic)이 없다"))
    flow = pl.get("flow") or []
    if len(flow) != 4 or not all(str(x).strip() for x in flow):
        iss.append(("error", "P0-1", "기획: 흐름 4줄(meta.plan.flow)이 없거나 4줄이 아니다 (C2-1)"))
    hk = pl.get("hook") or {}
    if not hk.get("line") or hk.get("time") in (None, ""):
        iss.append(("error", "P0-1", "기획: 후킹 시각·대사(meta.plan.hook)가 없다"))
    if not pl.get("ending"):
        iss.append(("error", "P0-1", "기획: 끝 장면(meta.plan.ending)이 없다 (C4-2)"))
    if not pl.get("conflict_type"):
        iss.append(("warn", "C1-1", "기획: 갈등 유형(meta.plan.conflict_type: 누명·오해 | 들킬 위기 | 선택의 기로 | 그 외)을 기록하면 기획점 분석에 쓸 수 있다"))
    if not pl.get("appeal"):
        iss.append(("warn", "C1-10", "기획: 소재 매력(meta.plan.appeal: 신작 | 명작 | 스타)이 없다"))
    if len(m.get("title") or []) != 2:
        iss.append(("error", "P0-1", "기획: 제목 두 줄이 없다"))
    if not m.get("rights_status"):
        iss.append(("error", "P0-1", "기획: rights_status 가 없다 (R9-1)"))
    if "sensitive_review" not in m:
        iss.append(("error", "P0-1", "기획: 민감 장면 검토(meta.sensitive_review: 없음 | 처리 내용)가 없다 (S11-2)"))
    return iss


def write_plan_doc(plan, resolved):
    m, pl = plan["meta"], plan["meta"].get("plan", {})
    L = [f"# 기획서 — {m.get('work', '')} {m.get('episode', '')}", "",
         f"- 제목: **{' / '.join(m.get('title', []))}**", f"- 소재: {pl.get('topic', '')}",
         f"- 후킹: {pl.get('hook', {}).get('time', '')} «{pl.get('hook', {}).get('line', '')}»",
         f"- 끝 장면: {pl.get('ending', '')}",
         f"- 갈등 유형: {pl.get('conflict_type', '미기록')} · 소재 매력: {pl.get('appeal', '미기록')}",
         f"- 구성: {', '.join(m.get('rule_set', []))} · 템플릿 {m.get('template', '')}",
         f"- 권리: origin={m.get('origin', '')}, rights_status={m.get('rights_status', '')}",
         f"- 민감 장면 검토: {m.get('sensitive_review', '')}", "", "## 흐름 4줄"]
    L += [f"{i}. {x}" for i, x in enumerate(pl.get("flow", []), 1)]
    L += ["", f"## 컷 ({len(resolved)}개, 총 {total_len(resolved):.1f}초)", "", "편집지시서_표.md 참고. 이 기획서와 컷표.json 이 영상 생성의 유일한 입력이다.", ""]
    (plan["_dir"] / "기획서.md").write_text("\n".join(L), encoding="utf-8")


def cmd_approve(plan, who):
    resolved, subs, iss = do_recalc(plan)
    iss = plan_issues(plan) + iss
    if print_issues(iss):
        print("기획이 완성되지 않았다 — 승인할 수 없다. error 를 고치세요.")
        return 1
    write_plan_doc(plan, resolved)
    import datetime
    ap = {"plan_hash": plan["_hash"], "tts_hash": tts_state_hash(plan), "approved_by": who,
          "approved_at": datetime.datetime.now().isoformat(timespec="seconds"),
          "total_sec": round(total_len(resolved), 1), "cuts": len(resolved),
          "rights_status": plan["meta"].get("rights_status")}
    (plan["_dir"] / "기획승인.json").write_text(json.dumps(ap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 기획 승인 기록 → 기획서.md, 기획승인.json (hash {plan['_hash']}, 승인자 {who})")
    print("이제 render / handoff / delegate 로 영상을 만들 수 있다. 컷표.json 을 고치면 다시 승인해야 한다.")
    return 0


def gate(plan):
    """영상 생성 전 게이트: 승인된 기획과 현재 컷표가 같아야 한다. 우회 옵션 없음."""
    f = plan["_dir"] / "기획승인.json"
    if not f.exists():
        print("✗ [P0-1] 기획 승인 전에는 영상을 만들 수 없다. 먼저 `approve` 로 기획을 확정하세요.")
        return False
    ap = json.loads(f.read_text(encoding="utf-8"))
    if ap.get("plan_hash") != plan["_hash"]:
        print("✗ [P0-2] 승인 뒤에 컷표.json 이 바뀌었다 — 승인된 기획으로만 영상을 만든다. 다시 `approve` 하세요.")
        return False
    if "tts_hash" not in ap:
        print("✗ [P0-2] 음성 실측 길이(tts_clips.json)가 빠진 예전 승인 기록이다 — 재승인 필요. 다시 `approve` 하세요.")
        return False
    if ap["tts_hash"] != tts_state_hash(plan):
        print("✗ [P0-2] 승인 뒤에 음성 실측 길이(tts_clips.json)가 바뀌었다 — 타임라인이 승인과 다르다. 다시 `approve` 하세요.")
        return False
    return True


# ---------------------------------------------------------------- 방식 ③ AI 어시스턴트 위임
def cmd_delegate(plan, out_dir, template="topbottom"):
    """캡컷 AI 어시스턴트 등에 넘길 위임 패키지: 프롬프트 + 컷표 + 음성 + 자막. 판단은 규칙이, 어시스턴트는 실행만."""
    if not gate(plan):
        return 1
    resolved, subs, iss = do_recalc(plan)
    if print_issues(iss):
        print("error 를 고친 뒤 다시 실행하세요.")
        return 1
    tpl = load_template(template or plan["meta"].get("template", "topbottom"))
    v = tpl["video"]
    out = (plan["_dir"] / out_dir).resolve()
    (out / "tts").mkdir(parents=True, exist_ok=True)
    clips = load_clips(plan)
    total = total_len(resolved)
    rows = []
    for r in resolved:
        face = f"얼굴(x{r['face']['x']}, y{r['face']['y']})" if r.get("face") else "얼굴 좌표 없음-가운데"
        if r["kind"] == "narration":
            fn = Path(clips[str(r["beat"])]["file"]).name if str(r["beat"]) in clips else beat_filename(r) + ".mp3"
            what = f"나레이션 `tts/{fn}` (원음 15%) — 대사 없는 화면"
            if str(r["beat"]) in clips:
                shutil.copy(plan["_dir"] / clips[str(r["beat"])]["file"], out / "tts" / fn)
        else:
            what = "원음 100%"
            if r.get("visual_start") is not None:
                what += f" · **화면만 {fmt_ts(r['visual_start'])} 부터 대체**(소리는 {fmt_ts(r['src_start'])} 그대로)"
        rows.append(f"| {r['no']} | {fmt_ts(r['src_start'])}~{fmt_ts(r['src_end'])} | {r['tl_start']:.1f}~{r['tl_end']:.1f} | {r['dur']:.1f}초 | {what} | {face} |")
    shutil.copy(plan["_dir"] / "자막.srt", out / "자막.srt")
    title = " / ".join(plan["meta"]["title"])
    prompt = f"""# 편집 위임 프롬프트 — {plan['meta'].get('work', '')} {plan['meta'].get('episode', '')}

아래 지시서를 **그대로** 실행해 9:16 쇼츠 타임라인을 만들어 주세요. 판단은 이미 끝났고, 당신은 실행만 합니다.

## 목표
- 캔버스 {tpl['canvas'][0]}x{tpl['canvas'][1]}, 총 길이 **{total:.1f}초**, 컷 **{len(resolved)}개**
- 원본 파일: `{plan['meta']['source_video']}` (아래 구간만 사용)

## 템플릿 ({tpl['name']})
- 위: 제목 두 줄 «{title}» (첫 줄 {tpl['title'].get('colors', ['FFFFFF'])[0]}, 둘째 줄 {(tpl['title'].get('colors') or ['', ''])[-1]}), 중심 y={tpl['title']['center_y']}
- 가운데 영상 창: x{v['x']} y{v['y']} {v['w']}x{v['h']}. 영상은 창을 **빈 곳 없이 채우고**, 말하는 얼굴이 창 중앙(세로는 위에서 약 {int(v.get('face_y', 0.42) * 100)}%)에 오게 위치를 맞춘다. 확대는 원본 대비 최대 {v.get('max_scale', 1.5)}배
- 자막: 중심 y={tpl['subtitle']['center_y']}, 한 줄 14자 안팎, 폭은 프레임의 90% 이내
- 아래 출처 띠: «{plan['meta'].get('source_caption', '')}»

## 컷 표 (순서·구간 변경 금지)
| 번호 | 원본 구간 | 타임라인 | 길이 | 소리 | 화면 |
|---|---|---|---|---|---|
{chr(10).join(rows)}

## 자막
- 첨부 `자막.srt` 를 **그대로** 사용한다. 자동 자막 생성은 끈다(중복 방지).

## 금지
- 컷 순서 변경, 컷 임의 삭제·추가, 없는 장면 삽입
- 지시서에 없는 효과음·BGM·전환 효과
- 나레이션으로 영상을 끝내기 — 마지막은 원음 대사와 반응 얼굴
- 민감 장면(우회 표시된 컷)의 원래 화면을 쓰는 것

## 끝나면 보고
- 컷 순서·개수가 표와 같은지, 총 길이, 시각이 어긋난 컷, 얼굴이 중앙에 못 온 컷, 임의로 바꾼 것을 목록으로 적는다.
"""
    (out / "위임프롬프트.md").write_text(prompt, encoding="utf-8")
    log = {"exec_mode": "ai_assistant", "job": plan["meta"].get("work"), "checks": {
        "order_and_count_match": None, "timing_error_sec": None, "human_edited_cuts": None,
        "speaker_centered_per_cut": None, "minutes_spent": None, "assistant_changes": []}}
    (out / "방식비교기록.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ {out}\\위임프롬프트.md  (+ 자막.srt, tts/, 방식비교기록.json)")
    print("다음: 프롬프트와 파일을 어시스턴트에 전달 → 결과를 점검표로 검수 → 방식비교기록.json 입력. 업로드는 사람이 승인.")
    return 0


# ---------------------------------------------------------------- CLI
def utf8_console():
    for stream in (sys.stdout, sys.stderr):   # Windows cp949 콘솔에서 ✓ « 출력 오류 방지
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


utf8_console()   # main 을 거치지 않고 gate() 등을 import 해 불러도 안전하게


def main(argv=None):
    utf8_console()
    ap =argparse.ArgumentParser(prog="shortsflow", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["check", "tts-plan", "tts-measure", "recalc", "approve", "render", "handoff", "delegate", "inspect"])
    ap.add_argument("plan", help="컷표.json")
    ap.add_argument("--dir", default="tts", help="tts-measure: 음성 폴더")
    ap.add_argument("--out", default=None, help="render: 출력 mp4 / handoff: 출력 폴더")
    ap.add_argument("--template", default=None)
    ap.add_argument("--by", default="사용자", help="approve: 승인자 이름(사람)")
    a = ap.parse_args(argv)
    plan = load_plan(a.plan)
    if a.cmd == "check":
        _, _, iss = do_recalc(plan, write=False)
        return print_issues(iss)
    if a.cmd == "inspect":
        from inspect_av import inspect_output  # 순환 import 회피: inspect_av 가 shortsflow 를 import 한다
        return inspect_output(plan, plan["_dir"] / (a.out or "숏폼_상하템플릿.mp4"))
    if a.cmd == "tts-plan":
        return cmd_tts_plan(plan)
    if a.cmd == "tts-measure":
        return cmd_tts_measure(plan, a.dir)
    if a.cmd == "recalc":
        resolved, subs, iss = do_recalc(plan)
        print(f"총 {total_len(resolved):.1f}초, 컷 {len(resolved)}개, 자막 {len(subs)}줄 → 자막.srt, 편집지시서_표.md, 컷표.resolved.json")
        return print_issues(iss)
    if a.cmd == "approve":
        return cmd_approve(plan, a.by)
    if a.cmd == "render":
        return cmd_render(plan, a.out or "숏폼_상하템플릿.mp4", a.template)
    if a.cmd == "handoff":
        return cmd_handoff(plan, a.out or "캡컷패키지", a.template)
    if a.cmd == "delegate":
        return cmd_delegate(plan, a.out or "위임패키지", a.template)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
