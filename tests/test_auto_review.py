# -*- coding: utf-8 -*-
"""docs/13 §10 자동 검수 — 규칙 ID가 있는 4가지: C5-9 싱크 · P8-3/C6-1 대사 끝 여유 · C3-3 첫 소리 0초 · P8-2 SRT 경계 문구."""
import unittest

from _sf import BASE_SRT, ProjectCase, plan_dict, sf


def review(case, srt=None, plan=None, subs=None):
    case.write_project(plan, srt)
    p = case.load()
    resolved, _ = sf.resolve(p)
    lines = sf.parse_srt(case.dir / "원본.srt")
    subs = sf.build_subs(p, resolved, lines) if subs is None else subs
    return sf.review_voice(resolved, subs, lines)


def ids(iss, level=None):
    return [r for lv, r, _ in iss if level in (None, lv)]


class SubtitleSyncC5_9(ProjectCase):
    def test_clean_plan_has_no_review_issues(self):
        self.assertEqual(review(self), [])

    def test_subtitle_not_before_voice(self):
        # 대사 컷 1(타임라인 0~3초)의 대사는 0.1초에 시작 — 0.05초에 뜨는 자막은 소리보다 먼저
        self.write_project()
        p = self.load()
        resolved, _ = sf.resolve(p)
        lines = sf.parse_srt(self.dir / "원본.srt")
        subs = sf.build_subs(p, resolved, lines)
        early = [(0.05, subs[0][1], subs[0][2])] + list(subs[1:])
        iss = sf.review_voice(resolved, early, lines)
        self.assertEqual(ids(iss, "error"), ["C5-9"])
        self.assertIn("자막 1", iss[0][2])

    def test_narration_subtitle_aligned_to_voice_start(self):
        self.write_project()
        p = self.load()
        resolved, _ = sf.resolve(p)
        n = resolved[1]
        subs = [(n["tl_start"] - 0.2, n["tl_start"] + 1.0, "앞당긴 나레이션 자막")]
        lines = sf.parse_srt(self.dir / "원본.srt")
        self.assertEqual(ids(sf.review_voice(resolved, subs, lines), "error"), ["C5-9"])

    def test_cli_check_blocks_nothing_on_clean_plan(self):
        self.write_project()
        code, text = self.cli("check", str(self.plan_path))
        self.assertEqual(code, 0, text)
        self.assertNotIn("C5-9]", text)


class DialogTailP8_3(ProjectCase):
    def test_dialog_tail_preserved(self):
        # 컷 3(13~18초) 끝 직전까지 이어지는 대사 → 닫기 여유 0.1초 < 0.2초
        iss = review(self, srt=BASE_SRT + [(16.5, 17.9, "끝까지 들어요")])
        self.assertEqual(ids(iss), ["P8-3"])
        self.assertIn("끝음절 뒤 여유 0.10초 < 0.20초", iss[0][2])

    def test_cut_through_speech_is_flagged(self):
        # 컷 3 끝(18초)을 넘어가는 대사 → 끝음절 잘림
        iss = review(self, srt=BASE_SRT + [(17.0, 18.6, "말이 잘린다")])
        self.assertEqual(ids(iss), ["P8-3"])
        self.assertIn("끝음절 전에 닫힌다", iss[0][2])

    def test_last_cut_tail_is_c6_1(self):
        # 펀치 컷 6(33~40초) 끝을 넘어가는 대사 → C6-1
        iss = review(self, srt=BASE_SRT + [(38.0, 40.5, "펀치 끝음절")])
        self.assertEqual(ids(iss), ["C6-1"])

    def test_open_margin(self):
        # 컷 5(26~32초) 시작 0.05초 뒤 첫 음절 → 열기 여유 부족
        srt = [x for x in BASE_SRT if x[0] != 26.5] + [(26.05, 29.0, "제가 누군지 아세요")]
        iss = review(self, srt=srt)
        self.assertEqual(ids(iss), ["P8-3"])
        self.assertIn("첫 음절 앞 여유 0.05초 < 0.10초", iss[0][2])

    def test_close_neighbor_midpoint_rule(self):
        # 이웃 줄 간격 0.2초(< 0.3) → 중점(0.1초 뒤)에서 자르면 통과
        srt = [x for x in BASE_SRT if x[0] != 13.5] + [(13.5, 17.9, "다시 말해 보세요"), (18.1, 19.0, "다음 줄")]
        self.assertEqual(review(self, srt=srt), [])

    def test_error_level_blocks(self):   # P8-3 🔒 → error
        self.write_project(srt=BASE_SRT + [(16.5, 17.9, "끝까지 들어요")])
        code, text = self.cli("check", str(self.plan_path))
        self.assertEqual(code, 1, text)
        self.assertIn("✗ [P8-3]", text)


class FirstSoundC3_3(ProjectCase):
    def test_no_silence_gaps(self):
        # 후킹 컷 1(0~3초) 첫 대사가 1.0초에 시작 → 앞 무음 (허용 = P8-3 열기 여유 0.1초)
        srt = [(1.0, 2.5, "지금 뭐라고 했어요")] + BASE_SRT[1:]
        iss = review(self, srt=srt)
        self.assertEqual(ids(iss, "error"), ["C3-3"])
        code, text = self.cli("check", str(self.plan_path))
        self.assertEqual(code, 1, text)

    def test_hook_without_dialog_blocks(self):
        # 후킹 컷 1(0~3초)에 원본 SRT 대사가 0줄 → 첫 소리 확인 불가 → C3-3 error
        iss = review(self, srt=BASE_SRT[1:])
        self.assertEqual(ids(iss, "error"), ["C3-3"])
        self.assertIn("대사가 없다", iss[0][2])

    def test_lead_within_tolerance_passes(self):
        self.assertNotIn("C3-3", ids(review(self)))   # BASE 첫 대사 0.1초 = 경계 안

    def test_lead_just_over_open_pad_blocks(self):
        srt = [(0.15, 2.5, "지금 뭐라고 했어요")] + BASE_SRT[1:]
        self.assertIn("C3-3", ids(review(self, srt=srt), "error"))

    def _auto_check(self, lead):
        p = plan_dict()
        p["meta"]["srt_kind"] = "auto"
        self.write_project(p, [(lead, 2.5, "지금 뭐라고 했어요")] + BASE_SRT[1:])
        return self.cli("check", str(self.plan_path))

    def test_auto_srt_lead_within_half_second_warns(self):
        # 자동 받아쓰기 SRT 는 경계 ±0.5초(P8-2) → 0.4초 첫 소리는 C3-3 warn(차단 아님)
        code, text = self._auto_check(0.4)
        self.assertIn("△ [C3-3]", text)
        self.assertIn("자동 받아쓰기 경계 — 확인 필요", text)
        self.assertNotIn("✗ [C3-3]", text)
        self.assertEqual(code, 0, text)

    def test_auto_srt_lead_within_open_pad_passes(self):
        code, text = self._auto_check(0.1)
        self.assertNotIn("C3-3]", text)
        self.assertEqual(code, 0, text)

    def test_auto_srt_lead_over_half_second_blocks(self):
        code, text = self._auto_check(0.6)
        self.assertIn("✗ [C3-3]", text)
        self.assertEqual(code, 1, text)

    def test_manual_srt_keeps_open_pad(self):
        self.write_project(srt=[(0.4, 2.5, "지금 뭐라고 했어요")] + BASE_SRT[1:])
        code, text = self.cli("check", str(self.plan_path))
        self.assertIn("✗ [C3-3]", text)


class AutoSrtHeaderP8_2(ProjectCase):
    def test_auto_srt_warning_header(self):
        p = plan_dict()
        p["meta"]["srt_kind"] = "auto"
        self.write_project(p)
        self.cli("recalc", str(self.plan_path))
        head = (self.dir / "편집지시서_표.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertIn(sf.AUTO_SRT_NOTE, head)
        self.assertEqual(sf.AUTO_SRT_NOTE, "자동 받아쓰기 SRT — 컷 경계 ±0.5초 확인 필요")

    def test_auto_srt_note_in_delegate_prompt(self):
        p = plan_dict()
        p["meta"]["srt_kind"] = "auto"
        self.write_project(p)
        code, text = self.cli("approve", str(self.plan_path))
        self.assertEqual(code, 0, text)
        code, text = self.cli("delegate", str(self.plan_path), "--out", "위임")
        self.assertEqual(code, 0, text)
        head = (self.dir / "위임" / "위임프롬프트.md").read_text(encoding="utf-8").splitlines()[:3]
        self.assertTrue(head[0].startswith("# 편집 위임 프롬프트"))
        self.assertIn(sf.AUTO_SRT_NOTE, "\n".join(head))

    def test_manual_srt_no_note_in_delegate_prompt(self):
        self.write_project()
        self.cli("approve", str(self.plan_path))
        self.cli("delegate", str(self.plan_path), "--out", "위임")
        self.assertNotIn("자동 받아쓰기", (self.dir / "위임" / "위임프롬프트.md").read_text(encoding="utf-8"))

    def test_manual_srt_has_no_header(self):
        self.write_project()
        self.cli("recalc", str(self.plan_path))
        text = (self.dir / "편집지시서_표.md").read_text(encoding="utf-8")
        self.assertNotIn("자동 받아쓰기", text)


if __name__ == "__main__":
    unittest.main()
