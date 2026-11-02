# -*- coding: utf-8 -*-
"""렌더 전 C3-3 — 후킹(컷 1) 첫 소리 0초를 원본 SRT로 본다. 수동 SRT 0.1초 초과 error, 자동 SRT 0.1~0.5초 warn·0.5초 초과 error.
렌더 결과의 첫 소리 측정(A10-17)은 inspect_av 쪽이다."""
import unittest

from _sf import BASE_SRT, ProjectCase, plan_dict, sf

HOOK = "지금 뭐라고 했어요"


def c33(case, lead, srt_kind="manual", srt=None):
    plan = plan_dict()
    plan["meta"]["srt_kind"] = srt_kind
    srt = [(lead, 2.5, HOOK)] + BASE_SRT[1:] if srt is None else srt
    case.write_project(plan, srt)
    p = case.load()
    resolved, _ = sf.resolve(p)
    return [(lv, msg) for lv, r, msg in sf.hook_first_sound_issues(resolved, sf.parse_srt(case.dir / "원본.srt"),
                                                                   srt_kind) if r == "C3-3"]


def levels(found):
    return [lv for lv, _ in found]


class HookFirstSoundManual(ProjectCase):
    def test_within_lead_passes(self):
        self.assertEqual(c33(self, 0.1), [])

    def test_sound_already_running_at_cut_start_passes(self):
        # 줄이 컷 시작 전부터 이어지면 첫 소리는 0초 (자르는 문제는 P8-3 몫)
        self.assertEqual(c33(self, 0.0, srt=[(-0.0, 2.5, HOOK)] + BASE_SRT[1:]), [])

    def test_lead_plus_one_frame_passes_p8_3_and_c3_3(self):
        # 앞 여유 0.12초: P8-3(0.1초 이상) 통과, C3-3(0.1초+1프레임 이하) 통과
        self.assertEqual(c33(self, 0.12), [])
        _, _, iss = sf.do_recalc(self.load(), write=False)
        self.assertFalse([r for lv, r, _ in iss if lv == "error" and r in ("P8-3", "C3-3")], iss)

    def test_over_one_frame_blocks(self):
        self.assertEqual(levels(c33(self, 0.15)), ["error"])

    def test_manual_has_no_auto_tolerance(self):
        self.assertEqual(levels(c33(self, 0.4)), ["error"])

    def test_hook_without_dialog_blocks(self):
        found = c33(self, 0, srt=BASE_SRT[1:])
        self.assertEqual(levels(found), ["error"])
        self.assertIn("대사가 없다", found[0][1])

    def test_narration_hook_not_checked(self):
        plan = plan_dict()
        plan["cuts"][0]["kind"] = "narration"
        self.write_project(plan, [(1.0, 2.5, HOOK)] + BASE_SRT[1:])
        p = self.load()
        resolved, _ = sf.resolve(p)
        self.assertEqual(sf.hook_first_sound_issues(resolved, sf.parse_srt(self.dir / "원본.srt"), "manual"), [])

    def test_cli_check_exit_1(self):
        self.write_project(srt=[(1.0, 2.5, HOOK)] + BASE_SRT[1:])
        code, text = self.cli("check", str(self.plan_path))
        self.assertIn("✗ [C3-3]", text)
        self.assertEqual(code, 1, text)


class HookFirstSoundAuto(ProjectCase):
    def test_within_lead_passes(self):
        self.assertEqual(c33(self, 0.1, "auto"), [])

    def test_lead_plus_one_frame_passes(self):
        self.assertEqual(c33(self, 0.12, "auto"), [])

    def test_between_lead_and_half_second_warns(self):
        found = c33(self, 0.4, "auto")
        self.assertEqual(levels(found), ["warn"])
        self.assertIn("자동 받아쓰기 경계 — 확인 필요", found[0][1])

    def test_exactly_half_second_warns(self):
        self.assertEqual(levels(c33(self, 0.5, "auto")), ["warn"])

    def test_over_half_second_blocks(self):
        self.assertEqual(levels(c33(self, 0.6, "auto")), ["error"])


if __name__ == "__main__":
    unittest.main()
