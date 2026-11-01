# -*- coding: utf-8 -*-
"""③ recalc — 컷 길이(A10-5 꼬리 0.2초)·타임라인 시작(A10-6 간격 0)·자막.srt 의 값과 결정성."""
import json
import shutil
import unittest
from pathlib import Path

from _sf import ProjectCase, sf

OUTPUTS = ("자막.srt", "컷표.resolved.json", "편집지시서_표.md")

# 비트1 20자/7 + 0.2 = 3.057, 비트2 18자/7 + 0.2 = 2.771
EXPECTED_DUR = [3.0, 3.057, 5.0, 2.771, 6.0, 7.0]
EXPECTED_TL_START = [0.0, 3.0, 6.057, 11.057, 13.828, 19.828]


class RecalcValues(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write_project()
        code, self.out = self.cli("recalc", str(self.plan_path))
        self.assertEqual(code, 0, self.out)

    def resolved(self):
        return json.loads((self.dir / "컷표.resolved.json").read_text(encoding="utf-8"))

    def test_cut_durations_and_timeline(self):
        r = self.resolved()
        self.assertEqual([c["dur"] for c in r], EXPECTED_DUR)
        self.assertEqual([c["tl_start"] for c in r], EXPECTED_TL_START)
        self.assertEqual(r[-1]["tl_end"], 26.828)
        self.assertAlmostEqual(sum(c["dur"] for c in r), r[-1]["tl_end"], places=6)   # 컷 길이 합 = 총 길이
        for a, b in zip(r, r[1:]):
            self.assertEqual(a["tl_end"], b["tl_start"])
        self.assertIn("총 26.8초, 컷 6개, 자막 8줄", self.out)

    def test_narration_fields(self):
        n = self.resolved()[1]
        self.assertFalse(n["measured"])
        self.assertEqual(n["voice_sec"], 2.857)
        self.assertAlmostEqual(n["src_end"], 3.0 + 3.057, places=6)

    def test_subtitle_srt(self):
        subs = sf.parse_srt(self.dir / "자막.srt")
        self.assertEqual(len(subs), 8)
        self.assertEqual([round(s, 3) for s, _, _ in subs],
                         [0.5, 3.0, 4.286, 6.557, 11.057, 12.343, 14.328, 20.328])
        texts = [t for _, _, t in subs]
        self.assertEqual(texts[1], "신입 사원이 첫 출근날")
        self.assertEqual(texts[-1], "오늘부터 대표입니다")
        # 나레이션 자막은 음성 길이(꼬리 제외)를 글자 비율로 나눈다: 9자 : 11자
        self.assertAlmostEqual(subs[2][1] - subs[1][0], 2.857, delta=0.03)
        for (s1, e1, _), (s2, _, _) in zip(subs, subs[1:]):
            self.assertLessEqual(e1, s2, "자막이 겹치면 안 된다")

    def test_edit_table_markdown(self):
        md = (self.dir / "편집지시서_표.md").read_text(encoding="utf-8")
        self.assertIn("총 길이 **26.8초**, 컷 6개", md)
        self.assertIn("[나레이션 1] " + "신입 사원이 첫 출근날 부장에게 무시당하고 있다", md)
        self.assertEqual(md.count("(추정)"), 2)


class RecalcDeterminism(ProjectCase):
    def snapshot(self, d):
        return {n: (Path(d) / n).read_bytes() for n in OUTPUTS}

    def test_same_input_twice_same_bytes(self):
        self.write_project()
        self.cli("recalc", str(self.plan_path))
        first = self.snapshot(self.dir)
        self.cli("recalc", str(self.plan_path))
        self.assertEqual(self.snapshot(self.dir), first)

    def test_copy_in_other_folder_same_bytes(self):
        self.write_project()
        other = self.dir.parent / "복사본"
        shutil.copytree(self.dir, other)
        self.cli("recalc", str(self.plan_path))
        self.cli("recalc", str(other / "컷표.json"))
        self.assertEqual(self.snapshot(self.dir), self.snapshot(other))

    def test_recalc_does_not_change_plan_hash(self):
        self.write_project()
        h = self.load()["_hash"]
        self.cli("recalc", str(self.plan_path))
        self.assertEqual(self.load()["_hash"], h)

    def test_time_formats_equivalent(self):
        # '00:13.0' 과 13 / 13.0 은 같은 결과 (parse_time)
        self.assertEqual(sf.parse_time("00:13.0"), 13.0)
        self.assertEqual(sf.parse_time("0:00:13,5"), 13.5)
        self.assertEqual(sf.parse_time(13), 13.0)


if __name__ == "__main__":
    unittest.main()
