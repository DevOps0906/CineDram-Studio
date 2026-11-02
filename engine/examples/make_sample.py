#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""합성 샘플 만들기 — 가짜 원본 영상(테스트 패턴 + 사인파), 원본 SRT, 컷표.json, TTS 톤.

실제 영상 없이 shortsflow 전체 흐름을 시험하는 용도다.
  python make_sample.py <빈 폴더>
  python ../shortsflow.py check <빈 폴더>/컷표.json
"""
import json
import subprocess
import sys
from pathlib import Path


def ff(*a):
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", *a], capture_output=True, text=True)
    if r.returncode:
        sys.exit(r.stderr)


def main(d):
    d = Path(d)
    (d / "tts").mkdir(parents=True, exist_ok=True)
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=180", "-f", "lavfi", "-i", "sine=f=300:d=180",
       "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "원본.mp4"))
    srt = []
    for k in range(26):
        s = 90 + 4 * k
        fmt = lambda x: f"00:{int(x // 60):02d}:{x % 60:06.3f}".replace(".", ",")
        srt.append(f"{k + 1}\n{fmt(s)} --> {fmt(s + 2.5)}\n대사 {k + 1}번 지금 뭐라고 했어요\n")
    (d / "원본.srt").write_text("\n".join(srt), encoding="utf-8")
    # TTS 대신 길이만 같은 톤 (파일명 규칙: NN_이름.mp3)
    ff("-f", "lavfi", "-i", "sine=f=440:d=3.4", "-c:a", "libmp3lame", str(d / "tts" / "01_시작.mp3"))
    ff("-f", "lavfi", "-i", "sine=f=523:d=3.0", "-c:a", "libmp3lame", str(d / "tts" / "02_근거.mp3"))
    plan = {
        "version": 1,
        "meta": {
            "work": "샘플작품", "episode": "E01", "title": ["신입 무시하던 부장", "3초 만에 굳은 이유"],
            "rule_set": ["common", "drama"], "source_video": "원본.mp4", "source_srt": "원본.srt",
            "origin": "local_file", "rights_status": "own", "srt_kind": "manual", "production_mode": "auto",
            "template": "topbottom", "channel": "후킹타임",
            "tts": {"provider": "minimax", "model": "speech-2.8-hd", "voice": "이나 A", "speed": 1.2, "pitch": 0, "vol": 1},
            "corrections": {"뭐라고": "뭐라고"},
            "plan": {
                "topic": "첫 출근 신입이 부장의 무시를 정체 공개로 뒤집는다", "conflict_type": "들킬 위기", "appeal": "신작",
                "flow": ["부장이 신입을 무시한다", "막말이 점점 세진다", "신입이 정체를 밝히며 받아친다", "부장이 굳은 얼굴로 선다"],
                "hook": {"time": "01:42.0", "line": "지금 뭐라고 했어요"},
                "ending": "펀치 끝음절 + 부장의 굳은 얼굴"
            },
            "sensitive_review": "컷 5 목조르기: 우회(화면 02:30~)"
        },
        "cuts": [
            {"no": 1, "kind": "dialog", "src_start": "01:41.8", "src_end": "01:44.8", "note": "후킹, 말하는 얼굴"},
            {"no": 2, "kind": "narration", "beat": 1, "label": "시작", "src_start": "01:45.0", "quiet_end": "01:52.0",
             "tts_text": "신입 사원이 첫 출근날 부장에게 무시당하고 있다",
             "sub_lines": ["신입 사원이 첫 출근날", "부장에게 무시당하고 있다"]},
            {"no": 3, "kind": "dialog", "src_start": "01:57.8", "src_end": "02:00.8"},
            {"no": 4, "kind": "narration", "beat": 2, "label": "근거", "src_start": "02:06.0", "quiet_end": "02:13.0",
             "tts_text": "막말은 점점 심해지고 신입은 말없이 듣는다",
             "sub_lines": ["막말은 점점 심해지고", "신입은 말없이 듣는다"]},
            {"no": 5, "kind": "dialog", "src_start": "02:17.8", "src_end": "02:20.8", "visual_start": "02:30.0",
             "note": "민감 장면 우회: 소리는 원음, 화면은 듣는 사람 반응",
             "sensitive": {"type": "목조르기", "method": "bypass", "audio": "duck"}},
            {"no": 6, "kind": "dialog", "src_start": "02:29.8", "src_end": "02:32.8"},
            {"no": 7, "kind": "dialog", "src_start": "02:49.8", "src_end": "02:52.8", "note": "펀치 + 반응 얼굴"}
        ]
    }
    (d / "컷표.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print("샘플 생성:", d)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample")
