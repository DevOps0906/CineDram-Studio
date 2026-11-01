# -*- coding: utf-8 -*-
"""shortsflow 테스트 공용 도구 — 최소 합성 입력(컷표.json·원본.srt)을 임시 폴더에 만든다.

engine/examples/make_sample.py 의 구조를 따르되, 원본 시각을 0초 근처로 옮겨
짧은(40초) 저해상도 원본 영상만으로 렌더까지 시험할 수 있게 했다.
"""
import contextlib
import copy
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "engine"
SCRIPT = ENGINE / "shortsflow.py"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

import shortsflow as sf  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))

# 비트 1: 공백 제외 20자, 비트 2: 18자
TTS1 = "신입 사원이 첫 출근날 부장에게 무시당하고 있다"
TTS2 = "막말은 점점 심해지고 신입은 말없이 듣는다"

BASE_PLAN = {
    "version": 1,
    "meta": {
        "work": "테스트작품", "episode": "E01", "title": ["신입 무시하던 부장", "3초 만에 굳은 이유"],
        "rule_set": ["common", "drama"], "source_video": "원본.mp4", "source_srt": "원본.srt",
        "origin": "local_file", "rights_status": "own", "srt_kind": "manual", "production_mode": "auto",
        "template": "topbottom", "channel": "테스트채널",
        "tts": {"provider": "fake", "voice": "가짜", "speed": 1.0},
        "plan": {
            "topic": "첫 출근 신입이 부장의 무시를 정체 공개로 뒤집는다", "conflict_type": "들킬 위기", "appeal": "신작",
            "flow": ["부장이 신입을 무시한다", "막말이 점점 세진다", "신입이 정체를 밝힌다", "부장이 굳는다"],
            "hook": {"time": "00:00.5", "line": "지금 뭐라고 했어요"},
            "ending": "펀치 끝음절 + 부장의 굳은 얼굴",
        },
        "sensitive_review": "없음",
    },
    "cuts": [
        {"no": 1, "kind": "dialog", "src_start": "00:00.0", "src_end": "00:03.0", "note": "후킹"},
        {"no": 2, "kind": "narration", "beat": 1, "label": "시작", "src_start": "00:03.0", "quiet_end": "00:12.0",
         "tts_text": TTS1, "sub_lines": ["신입 사원이 첫 출근날", "부장에게 무시당하고 있다"]},
        {"no": 3, "kind": "dialog", "src_start": "00:13.0", "src_end": "00:18.0"},
        {"no": 4, "kind": "narration", "beat": 2, "label": "근거", "src_start": "00:18.0", "quiet_end": "00:25.0",
         "tts_text": TTS2, "sub_lines": ["막말은 점점 심해지고", "신입은 말없이 듣는다"]},
        {"no": 5, "kind": "dialog", "src_start": "00:26.0", "src_end": "00:32.0"},
        {"no": 6, "kind": "dialog", "src_start": "00:33.0", "src_end": "00:40.0", "note": "펀치"},
    ],
}

# (시작, 끝, 대사) — 각 대사 컷 안에 한 줄씩
BASE_SRT = [
    (0.5, 2.5, "지금 뭐라고 했어요"),
    (13.5, 16.0, "다시 말해 보세요"),
    (26.5, 29.0, "제가 누군지 아세요"),
    (33.5, 36.0, "오늘부터 대표입니다"),
]


def plan_dict():
    return copy.deepcopy(BASE_PLAN)


def srt_text(lines):
    out = []
    for i, (s, e, tx) in enumerate(lines, 1):
        out.append(f"{i}\n{sf.srt_ts(s)} --> {sf.srt_ts(e)}\n{tx}\n")
    return "\n".join(out)


class ProjectCase(unittest.TestCase):
    """테스트마다 한국어·공백이 섞인 임시 작품 폴더를 만든다."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sf_test_")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "작품 폴더" / "1화"
        self.dir.mkdir(parents=True)
        self.plan_path = self.dir / "컷표.json"

    def write_project(self, plan=None, srt=None):
        plan = plan_dict() if plan is None else plan
        self.plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.dir / "원본.srt").write_text(srt_text(BASE_SRT if srt is None else srt), encoding="utf-8")
        return plan

    def cli(self, *args):
        """in-process 로 main 을 부르고 (종료 코드, 출력) 을 돌려준다."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = sf.main(list(args))
        return code, buf.getvalue()

    def load(self):
        return sf.load_plan(self.plan_path)

    def gate(self):
        """sf.gate 를 출력 가로채기 안에서 부른다 (main 밖에서는 콘솔 인코딩 재설정이 없음)."""
        with contextlib.redirect_stdout(io.StringIO()):
            return sf.gate(self.load())


def run_script(*args, cwd=None, timeout=300):
    """실제 CLI 프로세스 실행 (종료 코드 확인용)."""
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


def ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode:
        raise RuntimeError(r.stderr)


def make_source_video(path, seconds=41):
    """저해상도 테스트 패턴 + 사인파 (짧게)."""
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=s=320x180:r=15:d={seconds}", "-f", "lavfi", "-i", f"sine=f=300:d={seconds}",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path)


def make_tone(path, seconds, freq=440):
    ffmpeg("-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}", "-c:a", "libmp3lame", path)
