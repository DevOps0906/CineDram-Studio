# -*- coding: utf-8 -*-
"""⑤ delegate / handoff 산출물 구조 (승인 후). handoff·render 는 ffmpeg 가 필요 → 없으면 skip."""
import json
import os
import time
import unittest

from _sf import HAS_FFMPEG, ProjectCase, make_source_video, make_tone, run_script, sf


def _has_font():
    try:
        return bool(sf.find_fonts(sf.load_template("topbottom")))
    except SystemExit:
        return False


HAS_FONT = _has_font()


class Delegate(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write_project()
        code, text = self.cli("approve", str(self.plan_path))
        self.assertEqual(code, 0, text)
        code, self.out = self.cli("delegate", str(self.plan_path), "--out", "위임 패키지")
        self.assertEqual(code, 0, self.out)
        self.pkg = self.dir / "위임 패키지"

    def test_package_files(self):
        self.assertEqual(sorted(p.name for p in self.pkg.iterdir()),
                         sorted(["tts", "위임프롬프트.md", "자막.srt", "방식비교기록.json"]))
        self.assertEqual((self.pkg / "자막.srt").read_bytes(), (self.dir / "자막.srt").read_bytes())

    def test_prompt_has_forbidden_block_and_cut_table(self):
        md = (self.pkg / "위임프롬프트.md").read_text(encoding="utf-8")
        self.assertIn("## 금지", md)
        forbid = md.split("## 금지", 1)[1].split("##", 1)[0]
        for phrase in ("컷 순서 변경", "효과음·BGM·전환 효과", "나레이션으로 영상을 끝내기", "원래 화면을 쓰는 것"):
            self.assertIn(phrase, forbid)
        self.assertIn("총 길이 **26.8초**, 컷 **6개**", md)
        rows = [ln for ln in md.splitlines() if ln.startswith("| ") and ln[2:3].isdigit()]
        self.assertEqual(len(rows), 6)
        self.assertIn("나레이션 `tts/01_시작.mp3`", rows[1])
        self.assertIn("자동 자막 생성은 끈다", md)

    def test_comparison_log_structure(self):
        log = json.loads((self.pkg / "방식비교기록.json").read_text(encoding="utf-8"))
        self.assertEqual(log["exec_mode"], "ai_assistant")
        self.assertEqual(log["job"], "테스트작품")
        self.assertEqual(set(log["checks"]), {"order_and_count_match", "timing_error_sec", "human_edited_cuts",
                                              "speaker_centered_per_cut", "minutes_spent", "assistant_changes"})

    def test_measured_tts_copied(self):
        (self.dir / "tts").mkdir(exist_ok=True)
        (self.dir / "tts" / "01_시작.mp3").write_bytes(b"fake-audio")
        clips = {"clips": {"1": {"file": "tts/01_시작.mp3", "duration": 2.857, "chars": 20, "cps": 7.0,
                                 "text_hash": sf.tts_hash(self.load()["cuts"][1]["tts_text"])}}}
        (self.dir / "tts_clips.json").write_text(json.dumps(clips, ensure_ascii=False), encoding="utf-8")
        code, text = self.cli("delegate", str(self.plan_path), "--out", "위임 패키지")
        self.assertEqual(code, 1, "실측 추가 뒤에는 재승인 필요 (P0-2)")
        code, text = self.cli("approve", str(self.plan_path))
        self.assertEqual(code, 0, text)
        code, text = self.cli("delegate", str(self.plan_path), "--out", "위임 패키지")
        self.assertEqual(code, 0, text)
        self.assertEqual((self.pkg / "tts" / "01_시작.mp3").read_bytes(), b"fake-audio")


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe 가 PATH 에 없음")
class WithFfmpeg(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write_project()
        make_source_video(self.dir / "원본.mp4")
        (self.dir / "tts").mkdir()
        make_tone(self.dir / "tts" / "01_시작.mp3", 2.8)
        make_tone(self.dir / "tts" / "02_근거.mp3", 2.5, 523)
        code, text = self.cli("tts-measure", str(self.plan_path))
        self.assertEqual(code, 0, text)
        code, text = self.cli("approve", str(self.plan_path), "--by", "테스터")
        self.assertEqual(code, 0, text)

    def test_handoff_package(self):
        r = run_script("handoff", self.plan_path, "--out", "캡컷 패키지")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = self.dir / "캡컷 패키지"
        self.assertEqual(sorted(p.name for p in (out / "cuts").iterdir()), [f"{i:02d}.mp4" for i in range(1, 7)])
        self.assertEqual(sorted(p.name for p in (out / "tts").iterdir()), ["01_시작.mp3", "02_근거.mp3"])
        for n in ("미리보기.mp4", "자막.srt", "편집지시서_표.md", "캡컷체크리스트.md"):
            self.assertTrue((out / n).is_file(), n)
        self.assertFalse((self.dir / "_work_preview").exists())
        resolved = json.loads((self.dir / "컷표.resolved.json").read_text(encoding="utf-8"))
        for c in resolved:
            self.assertAlmostEqual(sf.ffprobe_duration(out / "cuts" / f"{c['no']:02d}.mp4"), c["dur"], delta=0.15)
        total = resolved[-1]["tl_end"]
        self.assertAlmostEqual(sf.ffprobe_duration(out / "미리보기.mp4"), total, delta=0.3)
        chk = (out / "캡컷체크리스트.md").read_text(encoding="utf-8")
        self.assertIn(f"총 {total:.1f}초 · 컷 6개", chk)
        self.assertIn("`tts/01_시작.mp3`", chk)

    @unittest.skipIf(os.environ.get("SF_SKIP_RENDER") == "1", "SF_SKIP_RENDER=1 — render(약 10초) 생략")
    @unittest.skipUnless(HAS_FONT, "템플릿 한글 폰트(malgun.ttf)를 search_dirs 에서 찾지 못함")
    def test_render_final_video(self):
        t0 = time.time()
        r = run_script("render", self.plan_path, "--out", "숏폼 결과.mp4", timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = self.dir / "숏폼 결과.mp4"
        self.assertEqual(sf.probe_size(out), (1080, 1920))
        resolved = json.loads((self.dir / "컷표.resolved.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(sf.ffprobe_duration(out), resolved[-1]["tl_end"], delta=0.3)
        print(f"render {time.time() - t0:.1f}s")


if __name__ == "__main__":
    unittest.main()
