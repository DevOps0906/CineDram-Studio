"""worker/steps.py — AI 단계(topic·flow), 관문 ① 선택, 권리·상한 보류 (실제 API 호출 0: core.llm.call 모킹)."""
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import llm  # noqa: E402
from worker import __main__ as wmain  # noqa: E402
from worker import queue, steps  # noqa: E402

_spec = importlib.util.spec_from_file_location("db_migrate_steps", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

SRT = """1
00:00:01,000 --> 00:00:04,000
안녕하세요

2
00:00:05,000 --> 00:00:09,500
이건 정말 중요한 이야기예요

3
00:00:20,000 --> 00:00:25,000
마지막 반전입니다
"""
TOPIC_OUT = {"candidates": [
    {"start": 1.0, "end": 9.5, "hook_line": "이건 정말", "tone_primary": "감동", "reason": "r"},
    {"start": 20.0, "end": 25.0, "hook_line": "반전", "tone_primary": "반전", "reason": "r"},
]}
FLOW_OUT = {"flow": ["a", "b", "c", "d"], "hook_line": "h", "titles": ["t1", "t2", "t3"]}


class StepsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="단계_테스트_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "원본").mkdir()
        (self.tmp / "원본" / "a.srt").write_text(SRT, encoding="utf-8")
        db = self.tmp / "studio.db"
        migrate_mod.migrate(db)
        self.db = db
        self.conn = migrate_mod.connect(db)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO works(title) VALUES ('작품')")

    def add_job(self, rights="own", srt="원본/a.srt", **kw):
        cur = self.conn.execute("INSERT INTO sources(work_id, file_path, srt_path, origin, rights_status) "
                                "VALUES (1, 'a.mp4', ?, 'local_file', ?)", (srt, rights))
        cols = {"source_id": cur.lastrowid, "state": "topic", "queue_state": "waiting",
                "rule_set": json.dumps(["common", "drama"]), **kw}
        return self.conn.execute(f"INSERT INTO jobs({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                 list(cols.values())).lastrowid

    def job(self, j):
        return self.conn.execute("SELECT state, queue_state, hold_reason, slot_id FROM jobs WHERE id=?",
                                 (j,)).fetchone()

    def step_once(self, side_effect):
        with mock.patch.object(llm, "call", side_effect=side_effect) as m:
            j = wmain.run_once(self.conn, self.tmp)
        return j, m


class ParseTest(unittest.TestCase):
    def test_parse_srt(self):
        cues = steps.parse_srt(SRT)
        self.assertEqual(len(cues), 3)
        self.assertEqual(cues[1], (5.0, 9.5, "이건 정말 중요한 이야기예요"))

    def test_rules_text_ignores_bad_names_and_common(self):
        t = steps._rules_text(json.dumps(["common", "../evil", "drama"]))
        self.assertNotIn("evil", t)
        self.assertTrue(len(t) > 20)


class TopicFlowTest(StepsBase):
    def test_topic_creates_candidates_and_holds_gate1(self):
        j = self.add_job()
        got, m = self.step_once(lambda *a, **k: TOPIC_OUT)
        self.assertEqual(got, j)
        self.assertEqual(self.job(j)[:3], ("topic", "held_human", steps.GATE1_REASON))
        self.assertIsNone(self.job(j)[3])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM topics WHERE job_id=?", (j,)).fetchone()[0], 2)
        self.assertEqual(m.call_args.args[2], "topic")
        self.assertIn("안녕하세요", "".join(m.call_args.args[3]))  # SRT 는 source 에서 읽어 전달

    def test_held_job_is_not_claimed_again(self):
        self.add_job()
        self.step_once(lambda *a, **k: TOPIC_OUT)
        j, m = self.step_once(lambda *a, **k: TOPIC_OUT)
        self.assertIsNone(j)
        m.assert_not_called()

    def test_gate1_choose_then_flow(self):
        j = self.add_job()
        self.step_once(lambda *a, **k: TOPIC_OUT)
        tid = self.conn.execute("SELECT id FROM topics WHERE job_id=? ORDER BY id LIMIT 1 OFFSET 1", (j,)).fetchone()[0]
        self.assertTrue(steps.choose_topic(self.conn, j, tid))
        self.assertEqual(self.job(j)[:2], ("topic", "waiting"))
        got, m = self.step_once(lambda *a, **k: FLOW_OUT)
        self.assertEqual(got, j)
        self.assertEqual(m.call_args.args[2], "flow")
        prompt = "".join(m.call_args.args[3])
        self.assertIn("마지막 반전", prompt)       # 선택 구간(20~25초) 자막만
        self.assertNotIn("안녕하세요", prompt)
        self.assertEqual(self.job(j)[:3], ("topic", "held_human", steps.GATE2_REASON))
        saved = json.loads((self.tmp / "var" / "jobs" / str(j) / steps.FLOW_FILE).read_text(encoding="utf-8"))
        self.assertEqual(saved["titles"], ["t1", "t2", "t3"])

    def test_choose_topic_rejects_wrong_state_or_foreign_topic(self):
        j = self.add_job()
        self.assertFalse(steps.choose_topic(self.conn, j, 1))  # 아직 관문 ① 아님
        self.step_once(lambda *a, **k: TOPIC_OUT)
        j2 = self.add_job()
        self.step_once(lambda *a, **k: TOPIC_OUT)
        other = self.conn.execute("SELECT id FROM topics WHERE job_id=?", (j2,)).fetchone()[0]
        self.assertFalse(steps.choose_topic(self.conn, j, other))  # 다른 작업의 후보

    def test_candidates_out_of_srt_range_hold_human(self):
        j = self.add_job()
        bad = {"candidates": [{"start": 500, "end": 600, "hook_line": "x", "tone_primary": "t", "reason": "r"}]}
        self.step_once(lambda *a, **k: bad)
        self.assertEqual(self.job(j)[:2], ("topic", "held_human"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0], 0)

    def test_llm_hold_maps_queue_state(self):
        j = self.add_job()
        self.step_once(llm.LlmHold("held_resource", "상한 초과"))
        self.assertEqual(self.job(j)[:3], ("topic", "held_resource", "상한 초과"))

    def test_unexpected_error_fails_job(self):
        j = self.add_job()
        self.step_once(RuntimeError("boom"))
        self.assertEqual(self.job(j)[:2], ("failed", "finished"))

    def test_missing_or_escaping_srt_holds_without_api(self):
        for srt in (None, "원본/없음.srt", "../a.srt"):
            j = self.add_job(srt=srt)
            _, m = self.step_once(lambda *a, **k: TOPIC_OUT)
            m.assert_not_called()
            self.assertEqual(self.job(j)[:2], ("topic", "held_human"))

    def test_unverified_rights_never_reach_api(self):
        """실제 llm.call 을 쓰고 SDK 클라이언트 생성 여부를 본다: 권리 미확인이면 만들지 않는다."""
        j = self.add_job(rights="unverified")
        with mock.patch.object(llm, "make_client") as mk:
            wmain.run_once(self.conn, self.tmp)
        mk.assert_not_called()
        self.assertEqual(self.job(j)[:2], ("topic", "held_human"))
        self.assertIn("권리", self.job(j)[2])


PLAN_OUT = {
    "topic": "소재 한 줄", "conflict_type": "들킬 위기", "ending": "끝 장면", "hook_time": 5.0,
    "hook_line": "이건 정말", "title_lines": ["첫째 줄", "둘째 줄"],
    "cuts": [
        {"kind": "dialog", "src_start": 5.0, "src_end": 9.5, "label": "", "tts_text": "", "sub_lines": []},
        {"kind": "narration", "src_start": 10.0, "src_end": 18.0, "label": "시작", "tts_text": "문장입니다",
         "sub_lines": ["문장", "입니다"]},
        {"kind": "dialog", "src_start": 20.0, "src_end": 25.0, "label": "", "tts_text": "", "sub_lines": []},
    ],
}


class PlanTest(StepsBase):
    def to_gate2(self, **kw):
        j = self.add_job(**kw)
        self.step_once(lambda *a, **k: TOPIC_OUT)
        tid = self.conn.execute("SELECT id FROM topics WHERE job_id=? ORDER BY id", (j,)).fetchone()[0]
        steps.choose_topic(self.conn, j, tid)
        self.step_once(lambda *a, **k: FLOW_OUT)
        return j

    def test_choose_flow_writes_choice_and_waits(self):
        j = self.to_gate2()
        self.assertFalse(steps.choose_flow(self.conn, j, self.tmp, 3))   # 범위 밖
        self.assertFalse(steps.choose_flow(self.conn, j, self.tmp, -1))
        self.assertTrue(steps.choose_flow(self.conn, j, self.tmp, 1))
        self.assertEqual(self.job(j)[:2], ("topic", "waiting"))
        c = json.loads((self.tmp / "var" / "jobs" / str(j) / steps.CHOICE_FILE).read_text(encoding="utf-8"))
        self.assertEqual((c["title"], c["start"], c["end"]), ("t2", 1.0, 9.5))
        self.assertFalse(steps.choose_flow(self.conn, j, self.tmp, 1))   # 이미 선택함

    def test_plan_builds_cuts_from_db_facts_and_awaits_approval(self):
        j = self.to_gate2()
        steps.choose_flow(self.conn, j, self.tmp, 0)
        evil = dict(PLAN_OUT)  # 권리·원본 값은 AI 출력으로 못 바꾼다(스키마가 금지 필드를 막고, 빌더는 DB 값만)
        with mock.patch("worker.steps.subproc.run", return_value=mock.Mock(returncode=0, output="")):
            got, m = self.step_once(lambda *a, **k: evil)
        self.assertEqual(m.call_args.args[2], "plan")
        self.assertEqual(self.job(j)[:3], ("topic", "held_human", steps.PLAN_REASON))
        plan = json.loads((self.tmp / "컷표.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["meta"]["rights_status"], "own")
        self.assertEqual(plan["meta"]["source_video"], "a.mp4")
        self.assertNotIn("sensitive_review", plan["meta"])  # 사람이 채워야 기획 승인 가능
        self.assertEqual([c["kind"] for c in plan["cuts"]], ["dialog", "narration", "dialog"])
        self.assertEqual(plan["cuts"][1]["beat"], 1)
        self.assertEqual(self.conn.execute("SELECT plan_path FROM jobs WHERE id=?", (j,)).fetchone()[0], "컷표.json")

    def test_plan_check_failure_noted(self):
        j = self.to_gate2()
        steps.choose_flow(self.conn, j, self.tmp, 0)
        with mock.patch("worker.steps.subproc.run", return_value=mock.Mock(returncode=1, output="")):
            self.step_once(lambda *a, **k: PLAN_OUT)
        self.assertIn("check 오류", self.job(j)[2])

    def test_plan_does_not_overwrite_existing_file(self):
        j = self.to_gate2()
        steps.choose_flow(self.conn, j, self.tmp, 0)
        (self.tmp / "컷표.json").write_text("{}", encoding="utf-8")
        _, m = self.step_once(lambda *a, **k: PLAN_OUT)
        m.assert_not_called()
        self.assertEqual((self.tmp / "컷표.json").read_text(encoding="utf-8"), "{}")
        self.assertEqual(self.job(j)[:2], ("topic", "held_human"))

    def test_plan_bad_title_lines_hold(self):
        j = self.to_gate2()
        steps.choose_flow(self.conn, j, self.tmp, 0)
        self.step_once(lambda *a, **k: {**PLAN_OUT, "title_lines": ["한 줄"]})
        self.assertFalse((self.tmp / "컷표.json").exists())
        self.assertIsNone(self.conn.execute("SELECT plan_path FROM jobs WHERE id=?", (j,)).fetchone()[0])


class CoexistTest(StepsBase):
    def test_jobs_with_plan_path_are_not_taken_by_llm_slot(self):
        self.add_job(plan_path="plans/컷표.json")
        self.assertIsNone(steps.claim_next_llm(self.conn))

    def test_llm_slot_is_separate_from_gpu_slot(self):
        self.add_job(state="building", plan_path="plans/c.json")
        queue.claim_next(self.conn)  # gpu0 점유
        j = self.add_job()
        self.assertEqual(steps.claim_next_llm(self.conn), j)


if __name__ == "__main__":
    unittest.main()
