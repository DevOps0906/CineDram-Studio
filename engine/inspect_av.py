#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""inspect_av — 렌더 후 검수 (무음·첫 소리·검은 화면·음량·true peak) → 점검결과.json

  python inspect_av.py 컷표.json [--out 숏폼_상하템플릿.mp4]
종료 코드: 0 통과(warn 허용) / 1 차단(fail ≥ 1, 파일은 항상 씀) / 2 실행 불가(파일 안 씀).
근거: tasks/plan-dev4.md §2·§3, rules/common.md A10-12·13·16~18, tasks/draft-rules-av.md.
공개: inspect_output(plan, out_path) -> int / loudnorm_stats(path, i, tp, lra) -> dict
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import shortsflow as sf  # noqa: E402

RESULT_NAME = "점검결과.json"
LOG_NAME = "작업기록.json"
SILENCE_NOISE = "-60dB"
SILENCE_MIN = 0.3        # A10-16
SILENCE_EPS = 0.001
FIRST_SOUND_MAX = 0.1    # A10-17
TARGET_I, TARGET_TP = -14.0, -1.0   # A10-12·13
LOUD_TOL = 1.0           # 통합 음량 허용 오차(미확인, 잠정값)
TP_EPS = 0.05            # 측정 반올림 여유
FF_TIMEOUT = 600


class InspectError(RuntimeError):
    """실행 불가(rc 2)."""


def _ff(args, tool="ffmpeg"):
    try:
        r = subprocess.run([tool, "-hide_banner", "-nostats", *map(str, args)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=FF_TIMEOUT)
    except FileNotFoundError:
        raise InspectError(f"{tool} 를 찾을 수 없다")
    except subprocess.TimeoutExpired:
        raise InspectError(f"{tool} 시간 초과")
    if r.returncode:
        raise InspectError(f"{tool} 실패: {r.stderr[-500:]}")
    return r.stderr


def ffmpeg_version():
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise InspectError("ffmpeg 를 찾을 수 없다")
    m = re.search(r"ffmpeg version\s+n?(\d+(?:\.\d+)*)", r.stdout)
    return m.group(1) if m else "unknown"


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                        "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise InspectError(f"ffprobe 실패: {r.stderr[-300:]}")
    d = json.loads(r.stdout)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in d.get("streams", []))
    return float(d["format"]["duration"]), v.get("width"), v.get("height"), has_audio


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return float("-inf")


def loudnorm_stats(path, i=TARGET_I, tp=TARGET_TP, lra=7.0):
    """loudnorm 1차 측정(출력 버림). input_i·input_tp·input_lra·input_thresh·target_offset 를 float 로."""
    err = _ff(["-i", path, "-vn", "-af", f"loudnorm=I={i}:TP={tp}:LRA={lra}:print_format=json", "-f", "null", "-"])
    a, b = err.rfind("{"), err.rfind("}")
    if a < 0 or b < a:
        raise InspectError("loudnorm 결과를 읽지 못했다")
    d = json.loads(err[a:b + 1])
    return {k: _num(d.get(k)) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}


def true_peak(path):
    """ebur128=peak=true 요약의 True peak(dBTP)."""
    err = _ff(["-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = err[err.rfind("Summary:"):] if "Summary:" in err else err
    m = re.search(r"True peak:\s*Peak:\s*(-?inf|-?[\d.]+)\s*dBFS", tail)
    if not m:
        raise InspectError("true peak 결과를 읽지 못했다")
    return _num(m.group(1))


def silences(path, dur_min, trim=None):
    """silencedetect → [(start, end)] (끝 없는 무음은 파일 끝까지)."""
    pre = f"atrim=0:{trim}," if trim else ""
    err = _ff(["-i", path, "-vn", "-af", f"{pre}silencedetect=noise={SILENCE_NOISE}:d={dur_min}", "-f", "null", "-"])
    out, start = [], None
    for line in err.splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
        m = re.search(r"silence_end:\s*(-?[\d.]+)", line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:
        out.append((start, None))
    return out


def black_frames(path, region, fps):
    """blackdetect=d=1/fps. region=(x,y,w,h) 영상 영역만 본다(레터박스 제외)."""
    x, y, w, h = region
    vf = f"crop={w}:{h}:{x}:{y},blackdetect=d={0.5 / fps:.5f}:pic_th=0.98:pix_th=0.10"
    err = _ff(["-i", path, "-an", "-vf", vf, "-f", "null", "-"])
    out = []
    for m in re.finditer(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)", err):
        out.append((float(m.group(1)), float(m.group(2))))
    return out


def _subtract(span, cuts):
    """span 에서 cuts 구간들을 뺀 나머지 [(s, e)]."""
    left = [span]
    for cs, ce in cuts:
        nxt = []
        for s, e in left:
            if ce <= s or cs >= e:
                nxt.append((s, e))
                continue
            if cs > s:
                nxt.append((s, cs))
            if ce < e:
                nxt.append((ce, e))
        left = nxt
    return left


def _r(x):
    return round(x, 3)


def _item(id_, rule, level, msg, measured=None, limit=None, unit=None, at=None, excluded=None):
    return {"id": id_, "rule": rule, "level": level, "measured": None if measured is None else _r(measured),
            "limit": limit, "unit": unit, "at": at or [], "excluded": excluded or [], "msg": msg}


def _mute_spans(resolved):
    out = []
    for r in resolved:
        if r["kind"] != "dialog":
            continue
        if (r.get("audio_mode") or (r.get("sensitive") or {}).get("audio")) == "mute":
            out.append((r["tl_start"], r["tl_end"], r["no"]))
    return out


def _video_region(tpl, width, height):
    v = tpl["video"]
    cw, ch = tpl["canvas"]
    if not width or not height:
        return v["x"], v["y"], v["w"], v["h"]
    sx, sy = width / cw, height / ch
    x, y = int(round(v["x"] * sx)), int(round(v["y"] * sy))
    w, h = int(round(v["w"] * sx)), int(round(v["h"] * sy))
    return x, y, max(2, min(w, width - x)), max(2, min(h, height - y))


def build_items(plan, out_path):
    """측정해 5개 항목을 만든다. (items, duration, version)"""
    clips = sf.load_clips(plan)
    resolved, _ = sf.resolve(plan, clips)
    tpl = sf.load_template(plan["meta"].get("template", "topbottom"))
    dur, width, height, has_audio = probe(out_path)
    ver = ffmpeg_version()
    items = []

    mutes = _mute_spans(resolved)
    tails = [(r["tl_end"] - sf.TAIL, r["tl_end"]) for r in resolved if r["kind"] == "narration"]
    excl_cuts = [(s, e) for s, e, _ in mutes] + tails

    if has_audio:
        st = loudnorm_stats(out_path)
        i = st["input_i"]
        if abs(i - TARGET_I) > LOUD_TOL:
            items.append(_item("loudness", "A10-12", "warn", f"통합 음량 {i:.1f} LUFS (목표 {TARGET_I:g}±{LOUD_TOL:g}, 오차는 미확인 잠정값)",
                               i, TARGET_I, "LUFS"))
        else:
            items.append(_item("loudness", "A10-12", "ok", f"통합 음량 {i:.1f} LUFS", i, TARGET_I, "LUFS"))
        tp = true_peak(out_path)
        if tp > TARGET_TP + TP_EPS:
            items.append(_item("true_peak", "A10-13", "fail", f"true peak {tp:.1f} dBTP > {TARGET_TP:g}", tp, TARGET_TP, "dBTP"))
        else:
            items.append(_item("true_peak", "A10-13", "ok", f"true peak {tp:.1f} dBTP", tp, TARGET_TP, "dBTP"))

        # 무음
        found, excluded = [], []
        for s, e in silences(out_path, SILENCE_MIN):
            e = dur if e is None else e
            for rs, re_ in _subtract((s, e), excl_cuts):
                if re_ - rs > SILENCE_MIN + SILENCE_EPS:
                    found.append({"start": _r(rs), "end": _r(re_)})
            for cs, ce, _ in mutes:
                if min(e, ce) > max(s, cs):
                    excluded.append({"why": "mute", "start": _r(max(s, cs)), "end": _r(min(e, ce))})
        worst = max((f["end"] - f["start"] for f in found), default=0.0)
        if found:
            items.append(_item("silence", "A10-16", "fail", f"무음 {len(found)}곳 (최대 {worst:.2f}초 > {SILENCE_MIN:g}초, 첫 {found[0]['start']:.2f}초)",
                               worst, SILENCE_MIN, "s", found, excluded))
        else:
            items.append(_item("silence", "A10-16", "ok", "의도하지 않은 무음 없음", 0.0, SILENCE_MIN, "s", excluded=excluded))

        # 첫 소리
        first = resolved[0] if resolved else None
        if first and mutes and mutes[0][2] == first["no"]:
            items.append(_item("first_sound", "A10-17", "warn", "첫 컷이 mute라 첫 소리 검사를 건너뜀", limit=FIRST_SOUND_MAX, unit="s"))
        else:
            lead = [x for x in silences(out_path, FIRST_SOUND_MAX, trim=3) if x[0] <= SILENCE_EPS]
            t0 = 0.0
            if lead:
                t0 = 3.0 if lead[0][1] is None else lead[0][1]
            if t0 > FIRST_SOUND_MAX + SILENCE_EPS:
                items.append(_item("first_sound", "A10-17", "fail", f"첫 소리가 {t0:.2f}초에 시작 (한도 {FIRST_SOUND_MAX:g}초)",
                                   t0, FIRST_SOUND_MAX, "s", [{"start": 0.0, "end": _r(t0)}]))
            else:
                items.append(_item("first_sound", "A10-17", "ok", f"첫 소리 {t0:.2f}초", t0, FIRST_SOUND_MAX, "s"))
    else:
        for id_, rule in (("loudness", "A10-12"), ("true_peak", "A10-13"), ("silence", "A10-16"), ("first_sound", "A10-17")):
            items.append(_item(id_, rule, "fail", "오디오 스트림 없음"))

    # 검은 화면 (mute 는 소리만이라 화면은 제외하지 않는다)
    fps = tpl["fps"]
    blk = black_frames(out_path, _video_region(tpl, width, height), fps)
    if blk:
        at = [{"start": _r(s), "end": _r(e)} for s, e in blk]
        items.append(_item("black_frames", "A10-18", "fail", f"검은 화면 {len(blk)}곳 (첫 {at[0]['start']:.2f}초)",
                           sum(e - s for s, e in blk), round(1 / fps, 4), "s", at))
    else:
        items.append(_item("black_frames", "A10-18", "ok", "검은 화면 없음", 0.0, round(1 / fps, 4), "s"))

    order = ["loudness", "true_peak", "silence", "first_sound", "black_frames"]
    fixes = _auto_fixes(plan)
    bad = [f for f in fixes if f.get("mode") in ("dynamic", "out_of_tolerance") or f.get("out_of_tolerance")]
    if bad:
        it = next((x for x in items if x["id"] == "loudness"), None)
        if it and it["level"] != "fail":
            it["level"] = "warn"
            it["msg"] += " / 음량 보정이 선형이 아니거나 허용 오차 밖 (작업기록.json)"
    items.sort(key=lambda it: order.index(it["id"]))
    return items, dur, ver


def _auto_fixes(plan):
    logf = Path(plan["_dir"]) / LOG_NAME
    try:
        return [f for f in json.loads(logf.read_text(encoding="utf-8")).get("auto_fixes", []) if isinstance(f, dict)]
    except (ValueError, OSError):
        return []


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def inspect_output(plan, out_path):
    """plan: shortsflow.load_plan() 결과. 0 통과 / 1 차단 / 2 실행 불가."""
    try:
        out = Path(out_path)
        if not out.is_file():
            raise InspectError(f"산출물이 없다: {out.name}")
        items, dur, ver = build_items(plan, out)
    except (InspectError, OSError, KeyError, ValueError) as e:
        print(f"inspect 실행 불가: {e}", file=sys.stderr)
        return 2
    base = Path(plan["_dir"])
    try:
        rel = out.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        rel = out.name
    fixes = 0
    logf = base / LOG_NAME
    if logf.is_file():
        try:
            fl = json.loads(logf.read_text(encoding="utf-8")).get("auto_fixes", [])
            fl = [f for f in fl if isinstance(f, dict)] if isinstance(fl, list) else []
        except (ValueError, OSError, AttributeError):
            fl = []
        fixes = len(fl)
        dyn = [f for f in fl if f.get("mode") == "dynamic" or f.get("out_of_tolerance") is True]
        if dyn:
            items.append(_item("auto_fix_mode", "A10-14", "warn",
                               f"음량 자동 보정이 동적 모드로 전환되었거나 허용 오차를 벗어난 항목 {len(dyn)}건"))
    n = {lv: sum(1 for it in items if it["level"] == lv) for lv in ("ok", "warn", "fail")}
    reason = "; ".join(it["msg"] for it in items if it["level"] == "fail")[:200] or None
    res = {"schema": 1, "plan_hash": plan["_hash"],
           "inspected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ffmpeg": ver,
           "output": {"file": rel, "size": out.stat().st_size, "sha256": _sha256(out), "duration": _r(dur)},
           "summary": {**n, "auto_fixes": fixes, "fail_reason": reason}, "items": items}
    (base / RESULT_NAME).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    for it in items:
        print(f"[{it['level']}] {it['id']} ({it['rule']}): {it['msg']}")
    return 1 if n["fail"] else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="inspect_av", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", help="컷표.json")
    ap.add_argument("--out", default=None, help="검사할 mp4 (기본 컷표 폴더의 숏폼_상하템플릿.mp4)")
    a = ap.parse_args(argv)
    try:
        plan = sf.load_plan(a.plan)
    except (OSError, ValueError, KeyError) as e:
        print(f"컷표 읽기 실패: {e}", file=sys.stderr)
        return 2
    out = Path(a.out) if a.out else plan["_dir"] / "숏폼_상하템플릿.mp4"
    if not out.is_absolute() and not out.exists():
        out = plan["_dir"] / out
    return inspect_output(plan, out)


if __name__ == "__main__":
    sf.utf8_console()
    sys.exit(main())
