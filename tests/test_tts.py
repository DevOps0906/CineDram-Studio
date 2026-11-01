# -*- coding: utf-8 -*-
"""④ tts-plan / tts-measure — 비트 파일명, 실측 평균 자막 속도(cps_mean), meta.cps 반영, A10-11."""
import json
import unittest
from unittest import mock

from _sf import HAS_FFMPEG, TTS1, ProjectCase, make_tone, plan_dict, sf

FAKE_DUR = {"01_시작.mp3": 3.4, "02_근거.mp3": 3.0}


def fake_ffprobe(path):
    return FAKE_DUR[path.name]


class TtsPlan(ProjectCase):
    def test_writes_beat_files_with_korean_names(self):
        self.write_project()
        code, text = self.cli("tts-plan", str(self.plan_path))
        self.assertEqual(code, 0, text)
        lines = (self.dir / "tts" / "TTS_줄.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, [f"01_시작\t{TTS1}", "02_근거\t막말은 점점 심해지고 신입은 말없이 듣는다"])
        md = (self.dir / "나레이션.md").read_text(encoding="utf-8")
        self.assertIn("| 1 | `01_시작.mp3` |", md)
        self.assertIn("| 20 | 2.9 |", md)   # 20자 ÷ 7

    def test_digits_and_long_sentence_warned(self):
        p = plan_dict()
        p["cuts"][1]["tts_text"] = "3초 만에 신입 사원이 첫 출근날 부장에게 무시당하고 말았다고 한다 정말로"
        self.write_project(p)
        code, text = self.cli("tts-plan", str(self.plan_path))
        self.assertEqual(code, 0)
        self.assertIn("[A10-1] 비트 1: 숫자", text)
        self.assertIn("28자 한계 초과", text)


class TtsMeasureFake(ProjectCase):
    """ffprobe 를 가짜로 바꿔 결정적으로 시험한다."""

    def setUp(self):
        super().setUp()
        self.write_project()
        (self.dir / "tts").mkdir()
        for n in FAKE_DUR:
            (self.dir / "tts" / n).write_bytes(b"fake")

    def measure(self):
        with mock.patch.object(sf, "ffprobe_duration", side_effect=fake_ffprobe):
            return self.cli("tts-measure", str(self.plan_path))

    def test_clips_and_mean_cps(self):
        code, text = self.measure()
        self.assertEqual(code, 0, text)
        data = json.loads((self.dir / "tts_clips.json").read_text(encoding="utf-8"))
        self.assertEqual(data["cps_mean"], 5.94)   # (20+18) / (3.4+3.0)
        c1 = data["clips"]["1"]
        self.assertEqual(c1["file"].replace("\\", "/"), "tts/01_시작.mp3")
        self.assertEqual((c1["duration"], c1["chars"], c1["cps"]), (3.4, 20, 5.88))
        self.assertEqual(c1["text_hash"], sf.tts_hash(TTS1))
        self.assertEqual(data["settings"], plan_dict()["meta"]["tts"])
        self.assertIn("meta.cps = 5.94", text)

    def test_measure_does_not_edit_plan(self):
        before = self.plan_path.read_bytes()
        self.measure()
        self.assertEqual(self.plan_path.read_bytes(), before, "meta.cps 는 사람이 반영한다(컷표 자동 수정 없음)")

    def test_recalc_uses_measured_duration(self):
        self.measure()
        code, text = self.cli("recalc", str(self.plan_path))
        self.assertEqual(code, 0)
        r = json.loads((self.dir / "컷표.resolved.json").read_text(encoding="utf-8"))
        self.assertEqual([c["dur"] for c in r if c["kind"] == "narration"], [3.6, 3.2])
        self.assertTrue(all(c["measured"] for c in r if c["kind"] == "narration"))
        self.assertNotIn("A10-5", text)

    def test_tts_text_changed_after_measure_a10_11(self):
        self.measure()
        data = json.loads(self.plan_path.read_text(encoding="utf-8"))
        data["cuts"][1]["tts_text"] = TTS1 + " 정말"
        self.plan_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _, text = self.cli("check", str(self.plan_path))
        self.assertIn("[A10-11] 컷 2", text)

    def test_missing_beat_file_exit_1(self):
        (self.dir / "tts" / "02_근거.mp3").unlink()
        code, text = self.measure()
        self.assertEqual(code, 1)
        self.assertIn("음성 파일이 없는 비트: 2", text)
        data = json.loads((self.dir / "tts_clips.json").read_text(encoding="utf-8"))
        self.assertEqual(list(data["clips"]), ["1"])
        self.assertEqual(data["cps_mean"], 5.88)

    def test_non_audio_files_ignored(self):
        (self.dir / "tts" / "02_근거.mp3").unlink()
        (self.dir / "tts" / "02_근거.txt").write_text("x", encoding="utf-8")
        code, _ = self.measure()
        self.assertEqual(code, 1)


class MetaCps(ProjectCase):
    def test_meta_cps_changes_estimate(self):
        p = plan_dict()
        p["meta"]["cps"] = 5.94
        self.write_project(p)
        resolved, _ = sf.resolve(self.load())
        self.assertEqual(resolved[1]["voice_sec"], round(20 / 5.94, 3))
        self.assertEqual(resolved[1]["dur"], round(20 / 5.94 + 0.2, 3))

    def test_missing_cps_uses_default_7(self):
        self.write_project()
        resolved, _ = sf.resolve(self.load())
        self.assertEqual(resolved[1]["voice_sec"], round(20 / 7, 3))


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe 가 PATH 에 없음")
class TtsMeasureReal(ProjectCase):
    def test_real_ffprobe_on_generated_tone(self):
        self.write_project()
        (self.dir / "tts").mkdir()
        make_tone(self.dir / "tts" / "01_시작.mp3", 2.0)
        make_tone(self.dir / "tts" / "02_근거.mp3", 1.5, 523)
        code, text = self.cli("tts-measure", str(self.plan_path))
        self.assertEqual(code, 0, text)
        data = json.loads((self.dir / "tts_clips.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(data["clips"]["1"]["duration"], 2.0, delta=0.1)
        self.assertAlmostEqual(data["clips"]["2"]["duration"], 1.5, delta=0.1)


if __name__ == "__main__":
    unittest.main()
