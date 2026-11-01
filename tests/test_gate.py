# -*- coding: utf-8 -*-
"""① 기획 승인 게이트 — P0-1 · P0-2 (rules/common.md), docs/01 상태머신의 '기획 승인' 단계."""
import datetime
import json
import unittest

from _sf import ProjectCase, plan_dict, sf


class GateBeforeApprove(ProjectCase):
    def test_render_handoff_delegate_rejected_before_approve(self):
        self.write_project()
        for cmd, out in (("render", "숏폼.mp4"), ("handoff", "캡컷패키지"), ("delegate", "위임패키지")):
            with self.subTest(cmd=cmd):
                code, text = self.cli(cmd, str(self.plan_path), "--out", out)
                self.assertEqual(code, 1)
                self.assertIn("[P0-1]", text)
                self.assertFalse((self.dir / out).exists(), "게이트 거부 시 산출물이 생기면 안 된다")
        self.assertFalse((self.dir / "_work").exists())
        self.assertFalse((self.dir / "자막.srt").exists(), "게이트가 recalc 보다 먼저 막아야 한다")

    def test_approve_rejected_when_plan_incomplete(self):
        p = plan_dict()
        p["meta"]["plan"]["flow"] = p["meta"]["plan"]["flow"][:3]   # 흐름 3줄
        del p["meta"]["sensitive_review"]
        self.write_project(p)
        code, text = self.cli("approve", str(self.plan_path), "--by", "홍길동")
        self.assertEqual(code, 1)
        self.assertIn("[P0-1] 기획: 흐름 4줄", text)
        self.assertIn("sensitive_review", text)
        self.assertFalse((self.dir / "기획승인.json").exists())

    def test_approve_rejected_when_check_has_error(self):
        p = plan_dict()
        p["cuts"] = p["cuts"][:2]   # 마지막 컷 나레이션(C5-3) + 총 길이 20초 미만(C7-1)
        self.write_project(p)
        code, text = self.cli("approve", str(self.plan_path))
        self.assertEqual(code, 1)
        self.assertIn("[C5-3]", text)
        self.assertIn("[C7-1]", text)
        self.assertFalse((self.dir / "기획승인.json").exists())


class GateAfterApprove(ProjectCase):
    def setUp(self):
        super().setUp()
        self.write_project()
        code, self.approve_out = self.cli("approve", str(self.plan_path), "--by", "홍길동")
        self.assertEqual(code, 0, self.approve_out)

    def test_approval_record_contents(self):
        ap = json.loads((self.dir / "기획승인.json").read_text(encoding="utf-8"))
        self.assertEqual(set(ap), {"plan_hash", "tts_hash", "approved_by", "approved_at", "total_sec", "cuts", "rights_status"})
        self.assertEqual(ap["tts_hash"], sf.NO_TTS)   # 음성 실측 전 승인 → 고정 값
        self.assertEqual(ap["plan_hash"], self.load()["_hash"])
        self.assertEqual(len(ap["plan_hash"]), 16)
        self.assertEqual(ap["approved_by"], "홍길동")
        datetime.datetime.fromisoformat(ap["approved_at"])
        self.assertEqual(ap["total_sec"], 26.8)
        self.assertEqual(ap["cuts"], 6)
        self.assertEqual(ap["rights_status"], "own")
        doc = (self.dir / "기획서.md").read_text(encoding="utf-8")
        self.assertIn("신입 무시하던 부장 / 3초 만에 굳은 이유", doc)
        self.assertIn("4. 부장이 굳는다", doc)

    def test_gate_passes_with_unchanged_plan(self):
        self.assertTrue(self.gate())

    def test_gate_passes_after_whitespace_only_reformat(self):
        # plan_hash 는 JSON 내용 기준 → 들여쓰기만 바뀐 것은 같은 기획
        data = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.plan_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(self.gate())

    def test_plan_change_after_approve_rejected_p0_2(self):
        data = json.loads(self.plan_path.read_text(encoding="utf-8"))
        data["cuts"][4]["src_end"] = "00:31.5"
        self.plan_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        for cmd in ("render", "handoff", "delegate"):
            with self.subTest(cmd=cmd):
                code, text = self.cli(cmd, str(self.plan_path))
                self.assertEqual(code, 1)
                self.assertIn("[P0-2]", text)
        self.assertFalse((self.dir / "위임패키지").exists())

    def test_tts_text_change_after_approve_rejected(self):
        data = json.loads(self.plan_path.read_text(encoding="utf-8"))
        data["cuts"][1]["tts_text"] += " 정말로"
        self.plan_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertFalse(self.gate())

    def test_tampered_approval_hash_rejected(self):
        f = self.dir / "기획승인.json"
        ap = json.loads(f.read_text(encoding="utf-8"))
        ap["plan_hash"] = "0" * 16
        f.write_text(json.dumps(ap), encoding="utf-8")
        self.assertFalse(self.gate())

    def test_gate_should_reject_when_measured_tts_changes_after_approve(self):
        """P0-2·P0-3: 승인 뒤 tts_clips.json(실측 길이)만 바뀌어도 타임라인이 달라지므로 거부한다."""
        clips = {"settings": {}, "cps_mean": 4.0, "clips": {
            "1": {"file": "tts/01_시작.mp3", "duration": 5.0, "chars": 20, "cps": 4.0, "text_hash": sf.tts_hash(plan_dict()["cuts"][1]["tts_text"])}}}
        (self.dir / "tts_clips.json").write_text(json.dumps(clips, ensure_ascii=False), encoding="utf-8")
        plan = self.load()
        resolved, _ = sf.resolve(plan, sf.load_clips(plan))
        ap = json.loads((self.dir / "기획승인.json").read_text(encoding="utf-8"))
        self.assertNotEqual(round(sf.total_len(resolved), 1), ap["total_sec"])   # 타임라인은 실제로 바뀜
        self.assertFalse(self.gate(), "승인 후 타임라인이 바뀌었는데 게이트가 통과시킨다")
        for cmd in ("render", "handoff", "delegate"):
            with self.subTest(cmd=cmd):
                code, text = self.cli(cmd, str(self.plan_path))
                self.assertEqual(code, 1)
                self.assertIn("[P0-2]", text)
                self.assertIn("tts_clips.json", text)
        self.assertFalse((self.dir / "위임패키지").exists())

    def test_legacy_approval_without_tts_hash_requires_reapproval(self):
        f = self.dir / "기획승인.json"
        ap = json.loads(f.read_text(encoding="utf-8"))
        del ap["tts_hash"]
        f.write_text(json.dumps(ap, ensure_ascii=False), encoding="utf-8")
        code, text = self.cli("delegate", str(self.plan_path))
        self.assertEqual(code, 1)
        self.assertIn("[P0-2]", text)
        self.assertIn("재승인 필요", text)
        self.assertFalse((self.dir / "위임패키지").exists())


class GateWithMeasuredTts(ProjectCase):
    """정상 순서 tts-measure → recalc → approve 뒤에는 게이트가 통과해야 한다."""

    CLIPS = {"settings": {}, "cps_mean": 4.0, "clips": {
        "1": {"file": "tts/01_시작.mp3", "duration": 5.0, "chars": 20, "cps": 4.0},
        "2": {"file": "tts/02_근거.mp3", "duration": 4.5, "chars": 18, "cps": 4.0}}}

    def setUp(self):
        super().setUp()
        self.write_project()
        clips = json.loads(json.dumps(self.CLIPS))
        clips["clips"]["1"]["text_hash"] = sf.tts_hash(plan_dict()["cuts"][1]["tts_text"])
        clips["clips"]["2"]["text_hash"] = sf.tts_hash(plan_dict()["cuts"][3]["tts_text"])
        self.clips = clips
        self.clips_path = self.dir / "tts_clips.json"
        self.clips_path.write_text(json.dumps(clips, ensure_ascii=False, indent=2), encoding="utf-8")
        code, text = self.cli("recalc", str(self.plan_path))
        self.assertEqual(code, 0, text)
        code, text = self.cli("approve", str(self.plan_path), "--by", "홍길동")
        self.assertEqual(code, 0, text)

    def test_gate_passes_in_normal_order(self):
        ap = json.loads((self.dir / "기획승인.json").read_text(encoding="utf-8"))
        self.assertNotEqual(ap["tts_hash"], sf.NO_TTS)
        self.assertTrue(self.gate())
        code, text = self.cli("recalc", str(self.plan_path))   # 승인 뒤 recalc 를 다시 돌려도 영향 없음
        self.assertEqual(code, 0, text)
        self.assertTrue(self.gate())

    def test_gate_passes_after_clips_reformat(self):
        # 들여쓰기·키 순서만 바뀐 tts_clips.json 은 같은 실측
        c = self.clips["clips"]
        reordered = {"clips": {"2": dict(reversed(list(c["2"].items()))), "1": c["1"]}, "cps_mean": 4.0, "settings": {}}
        self.clips_path.write_text(json.dumps(reordered, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(self.gate())

    def test_gate_rejects_when_clips_removed(self):
        self.clips_path.unlink()
        self.assertFalse(self.gate())


if __name__ == "__main__":
    unittest.main()
