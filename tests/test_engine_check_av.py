# -*- coding: utf-8 -*-
"""개발 4 E1 — 렌더 전 검사(싱크 C5-9 · 말끝 여유 P8-3 · 한 줄 자막 · 컷 간격 · P8-2 문구)와 render 음량 보정(A10-12·14)."""
import json
import os
import subprocess
import unittest

from _sf import BASE_SRT, HAS_FFMPEG, ProjectCase, make_source_video, make_tone, plan_dict, run_script, sf


def rules(iss, level="error"):
    return [r for lv, r, _ in iss if lv == level]


class CheckCase(ProjectCase):
    def issues(self, plan=None, srt=None):
        self.write_project(plan, srt)
        return sf.do_recalc(self.load(), write=False)

    def subs_of(self):
        plan = self.load()
        resolved, _ = sf.resolve(plan, sf.load_clips(plan))
        lines = sf.parse_srt(self.dir / "원본.srt")
        return plan, resolved, sf.build_subs(plan, resolved, lines), lines

    def run_check(self, plan, resolved, subs, lines):
        return sf.check(plan, resolved, subs, [], 35.0, lines)


class SyncTest(CheckCase):
    """C5-9: 자막은 그 컷의 소리 시작보다 먼저 뜨지 않는다."""

    def setUp(self):
        super().setUp()
        self.write_project()
        self.plan, self.resolved, self.subs, self.lines = self.subs_of()

    def shifted(self, tx_part, delta):
        return [(s + delta, e + delta, tx) if tx_part in tx else (s, e, tx) for s, e, tx in self.subs]

    def test_on_time_passes(self):
        self.assertNotIn("C5-9", rules(self.run_check(self.plan, self.resolved, self.subs, self.lines)))

    def test_narration_subtitle_early_is_error(self):
        subs = self.shifted("신입 사원이", -0.05)
        self.assertIn("C5-9", rules(self.run_check(self.plan, self.resolved, subs, self.lines)))

    def test_narration_within_tolerance_passes(self):
        subs = self.shifted("신입 사원이", -0.005)
        self.assertNotIn("C5-9", rules(self.run_check(self.plan, self.resolved, subs, self.lines)))

    def test_dialog_subtitle_early_is_error(self):
        subs = self.shifted("다시 말해", -0.05)
        self.assertIn("C5-9", rules(self.run_check(self.plan, self.resolved, subs, self.lines)))

    def test_late_is_not_error(self):
        subs = self.shifted("다시 말해", 0.2)
        self.assertNotIn("C5-9", rules(self.run_check(self.plan, self.resolved, subs, self.lines)))


class TailRoomTest(CheckCase):
    """P8-3: 첫 음절 0.1초 전·끝 0.2초 후. 이웃 간격 0.3초 미만이면 중점. 검사만 한다(D3)."""

    def cut1(self, start, end, srt=None):
        plan = plan_dict()
        plan["cuts"][0]["src_start"], plan["cuts"][0]["src_end"] = start, end
        return rules(self.issues(plan, srt)[2])

    def test_end_01_is_error(self):
        self.assertIn("P8-3", self.cut1("00:00.0", "00:02.6"))

    def test_end_02_passes(self):
        self.assertNotIn("P8-3", self.cut1("00:00.0", "00:02.7"))

    def test_start_01_before_passes_and_005_is_error(self):
        self.assertNotIn("P8-3", self.cut1("00:00.4", "00:03.0"))
        self.assertIn("P8-3", self.cut1("00:00.45", "00:03.0"))

    def test_close_neighbor_uses_midpoint(self):
        # 줄 끝 2.5 / 이웃 시작 2.7 (간격 0.2) → 중점 2.6 까지만 있으면 통과, 0.2초 규칙이면 실패했을 값
        srt = [(0.5, 2.5, "지금 뭐라고 했어요"), (2.7, 2.9, "네"), *BASE_SRT[1:]]
        self.assertNotIn("P8-3", self.cut1("00:00.0", "00:02.6", srt))
        self.assertIn("P8-3", self.cut1("00:00.0", "00:02.55", srt))

    def test_does_not_change_timeline(self):
        # 자동 적용 없음 — 위반이어도 컷 시각을 건드리지 않는다
        plan = plan_dict()
        plan["cuts"][0]["src_end"] = "00:02.6"
        resolved, _, iss = self.issues(plan)
        self.assertEqual(resolved[0]["src_end"], 2.6)
        self.assertIn("P8-3", rules(iss))


class LineAndGapTest(CheckCase):
    def one_line(self, text):
        plan, resolved, _, lines = self.subs_of_written()
        subs = [(0.0, 3.0, text)]
        return self.run_check(plan, resolved, subs, lines)

    def subs_of_written(self):
        self.write_project()
        return self.subs_of()

    def test_over_16_chars_warns_boundary(self):
        # 한 줄(wrap 이후) 16자 이하는 통과, 17자 이상은 경고 (공백 없는 긴 낱말은 두 줄로 쪼개져 16자를 넘기려면 33자)
        ok = self.one_line("가" * 32)
        bad = self.one_line("가" * 34)
        self.assertEqual([m for lv, r, m in ok if "한 줄" in m], [])
        self.assertTrue([m for lv, r, m in bad if "한 줄" in m])
        self.assertEqual([lv for lv, r, m in bad if "한 줄" in m][0], "warn")

    def test_two_lines_allowed(self):
        self.assertEqual([m for lv, r, m in self.one_line("가" * 20) if "한 줄" in m], [])

    def test_cut_gap_is_error(self):
        plan, resolved, subs, lines = self.subs_of_written()
        resolved[2]["tl_start"] += 0.01
        self.assertIn("A10-6", rules(self.run_check(plan, resolved, subs, lines)))
        resolved[2]["tl_start"] -= 0.01
        self.assertNotIn("A10-6", rules(self.run_check(plan, resolved, subs, lines)))


class AutoSrtNoteTest(CheckCase):
    NOTE = "자동 받아쓰기 SRT — 컷 경계 ±0.5초 확인 필요"

    def plan_with(self, kind):
        plan = plan_dict()
        if kind is None:
            plan["meta"].pop("srt_kind", None)
        else:
            plan["meta"]["srt_kind"] = kind
        return plan

    def recalc_doc(self, kind):
        self.write_project(self.plan_with(kind))
        sf.do_recalc(self.load())
        return (self.dir / "편집지시서_표.md").read_text(encoding="utf-8")

    def test_note_unless_manual(self):
        for kind in ("auto", None, "whisper", ""):
            with self.subTest(kind=kind):
                self.assertIn(self.NOTE, self.recalc_doc(kind).splitlines()[0])
                self.assertIn("P8-2", rules(self.issues(self.plan_with(kind))[2], "warn"))

    def test_manual_has_none(self):
        self.assertNotIn(self.NOTE, self.recalc_doc("manual"))
        self.assertNotIn("P8-2", rules(self.issues(self.plan_with("manual"))[2], "warn"))


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe 가 PATH 에 없음")
class LoudnessRenderTest(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write_project()
        make_source_video(self.dir / "원본.mp4")
        (self.dir / "tts").mkdir()
        make_tone(self.dir / "tts" / "01_시작.mp3", 2.8)
        make_tone(self.dir / "tts" / "02_근거.mp3", 2.5, 523)
        self.assertEqual(self.cli("tts-measure", str(self.plan_path))[0], 0)
        self.assertEqual(self.cli("approve", str(self.plan_path), "--by", "테스터")[0], 0)

    @unittest.skipIf(os.environ.get("SF_SKIP_RENDER") == "1", "SF_SKIP_RENDER=1")
    def test_render_normalizes_and_records(self):
        r = run_script("render", self.plan_path, "--out", "결과.mp4", timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rec = json.loads((self.dir / "작업기록.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["schema"], 1)
        fix = rec["auto_fixes"][0]
        self.assertEqual((fix["item"], fix["rule"]), ("loudness", "A10-12"))
        for k in ("i", "tp", "lra", "thresh"):
            self.assertIn(k, fix["before"])
        self.assertIn("i", fix["after"])
        self.assertEqual(fix["mode"], "linear")
        self.assertIn("linear=true", fix["filter"])
        self.assertFalse(fix["out_of_tolerance"])
        self.assertAlmostEqual(fix["after"]["i"], -14.0, delta=1.0)
        # 최종 mp4 를 따로 측정: 통합 음량 -14±1, true peak ≤ -1, 48 kHz
        err = sf.run_capture(["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(self.dir / "결과.mp4"),
                              "-af", "loudnorm=print_format=json", "-f", "null", "-"])
        m = sf.parse_loudnorm(err)
        self.assertAlmostEqual(m["input_i"], -14.0, delta=1.0)
        self.assertLessEqual(m["input_tp"], -1.0)
        rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate",
                               "-of", "default=nw=1:nk=1", str(self.dir / "결과.mp4")], capture_output=True, text=True)
        self.assertEqual(rate.stdout.strip(), "48000")


if __name__ == "__main__":
    unittest.main()
