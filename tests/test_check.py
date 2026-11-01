# -*- coding: utf-8 -*-
"""② check — 규칙 위반 감지 (C5-10, C7-1, C5-3, A10-6, A10-7, P8-1 …), error/warn 집계와 종료 코드."""
import re
import unittest

from _sf import BASE_SRT, ProjectCase, plan_dict, run_script, sf


def counts(text):
    m = re.search(r"— error (\d+), warn (\d+)", text)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def rules(text, level):
    mark = "✗" if level == "error" else "△"
    return re.findall(re.escape(mark) + r" \[([^\]]+)\]", text)


class CheckBaseline(ProjectCase):
    def test_clean_plan_only_estimate_warnings_exit_0(self):
        self.write_project()
        code, text = self.cli("check", str(self.plan_path))
        self.assertEqual(code, 0, text)
        self.assertEqual(counts(text), (0, 2))
        self.assertEqual(rules(text, "warn"), ["A10-5", "A10-5"])   # 실측 전 추정 길이

    def test_check_writes_nothing(self):
        self.write_project()
        before = sorted(p.name for p in self.dir.iterdir())
        self.cli("check", str(self.plan_path))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), before)

    def test_unverified_rights_warns_r9_1(self):
        p = plan_dict()
        del p["meta"]["rights_status"]
        self.write_project(p)
        code, text = self.cli("check", str(self.plan_path))
        self.assertEqual(code, 0)
        self.assertIn("R9-1", rules(text, "warn"))


class CheckSubtitleC5_10(ProjectCase):
    def test_short_and_fast_subtitle_warned(self):
        # 대사 컷 3(13~18초) 안에 0.5초 동안 8자 → 0.8초 미만 + 16자/초
        self.write_project(srt=BASE_SRT + [(16.5, 17.0, "빨리빨리빨리빨리")])
        code, text = self.cli("check", str(self.plan_path))
        self.assertEqual(code, 0, "C5-10 은 warn 이므로 종료 코드 0")
        self.assertRegex(text, r"△ \[C5-10\] 자막 \d+: 0\.50초 < 0\.8초")
        self.assertRegex(text, r"△ \[C5-10\] 자막 \d+: 16\.0자/초 > 8\.0")
        self.assertEqual(counts(text), (0, 4))

    def test_boundary_exact_values_not_warned(self):
        # 정확히 0.8초 · 정확히 8자/초(6.4자는 불가 → 1.0초 8자) 는 경계 안
        self.write_project()
        plan = self.load()
        resolved, _ = sf.resolve(plan)
        subs = [(0.0, 0.8, "가나다"), (1.0, 2.0, "가나다라마바사아")]
        iss = [i for i in sf.check(plan, resolved, subs) if i[1] == "C5-10"]
        self.assertEqual(iss, [])

    def test_too_long_subtitle_warned(self):
        self.write_project()
        plan = self.load()
        resolved, _ = sf.resolve(plan)
        iss = [i for i in sf.check(plan, resolved, [(0.0, 4.5, "가나다")]) if i[1] == "C5-10"]
        self.assertEqual(len(iss), 1)
        self.assertIn("> 4.0초", iss[0][2])

    def test_fragment_under_0_3s_dropped_c5_11(self):
        # 컷 경계에 0.2초만 걸친 대사는 자막에서 빠진다
        self.write_project(srt=BASE_SRT + [(17.8, 19.0, "잘린조각")])
        plan = self.load()
        _, subs, _ = sf.do_recalc(plan, write=False)
        self.assertNotIn("잘린조각", [s[2] for s in subs])


class CheckErrors(ProjectCase):
    def test_total_length_out_of_range_c7_1_exit_1(self):
        p = plan_dict()
        p["cuts"] = p["cuts"][:3]
        self.write_project(p)
        code, text = self.cli("check", str(self.plan_path))
        self.assertEqual(code, 1)
        self.assertIn("C7-1", rules(text, "error"))
        self.assertIn("총 길이 11.1초", text)

    def test_last_cut_narration_c5_3(self):
        p = plan_dict()
        p["cuts"] = p["cuts"][:4]
        self.write_project(p)
        code, text = self.cli("check", str(self.plan_path))
        self.assertEqual(code, 1)
        self.assertIn("C5-3", rules(text, "error"))

    def test_negative_dialog_cut_p8_1(self):
        p = plan_dict()
        p["cuts"][2]["src_end"] = "00:12.0"   # 시작 13초보다 빠름
        self.write_project(p)
        code, text = self.cli("check", str(self.plan_path))
        self.assertEqual(code, 1)
        self.assertIn("P8-1", rules(text, "error"))

    def test_voice_longer_than_quiet_screen_a10_7(self):
        p = plan_dict()
        p["cuts"][1]["quiet_end"] = "00:05.0"   # 고른 화면 2초 < 음성 3.06초
        self.write_project(p)
        code, text = self.cli("check", str(self.plan_path))
        self.assertEqual(code, 1)
        self.assertIn("A10-7", rules(text, "error"))

    def test_missing_source_srt_p8_1(self):
        self.write_project()
        (self.dir / "원본.srt").unlink()
        code, text = self.cli("check", str(self.plan_path))
        self.assertEqual(code, 1)
        self.assertIn("원본 SRT 가 없다", text)

    def test_gap_between_cuts_a10_6(self):
        self.write_project()
        plan = self.load()
        resolved, _ = sf.resolve(plan)
        resolved[3]["tl_start"] += 0.5
        iss = sf.check(plan, resolved, [])
        self.assertIn(("error", "A10-6", "컷 3~4 사이에 간격이 있다"), iss)

    def test_sensitive_bypass_without_visual_start_s11_6(self):
        p = plan_dict()
        p["cuts"][4]["sensitive"] = {"type": "목조르기", "method": "bypass"}
        self.write_project(p)
        code, text = self.cli("check", str(self.plan_path))
        self.assertEqual(code, 1)
        self.assertIn("S11-6", rules(text, "error"))

    def test_error_and_warn_counts_sorted_errors_first(self):
        p = plan_dict()
        p["cuts"] = p["cuts"][:4]
        self.write_project(p)
        code, text = self.cli("check", str(self.plan_path))
        ne, nw = counts(text)
        self.assertEqual(ne, len(rules(text, "error")))
        self.assertEqual(nw, len(rules(text, "warn")))
        lines = [ln for ln in text.splitlines() if ln[:1] in "✗△"]
        self.assertEqual(lines, sorted(lines, key=lambda ln: ln[0] != "✗"))


class CheckRealProcess(ProjectCase):
    """실제 프로세스 종료 코드 + 한국어·공백 경로."""

    def test_cli_exit_codes(self):
        self.write_project()
        r = run_script("check", self.plan_path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("error 0, warn 2", r.stdout)
        p = plan_dict()
        p["cuts"] = p["cuts"][:3]
        self.write_project(p)
        r = run_script("check", self.plan_path)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("[C7-1]", r.stdout)


if __name__ == "__main__":
    unittest.main()
