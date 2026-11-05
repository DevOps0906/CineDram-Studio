"""AI 초안 접수(/make ai_draft)와 관문 ①②(/gate) 화면 — 실제 API 호출 0."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import llm  # noqa: E402
from server import deps  # noqa: E402
from test_make import BASE, MakeBase  # noqa: E402
from test_worker_steps import FLOW_OUT, SRT, TOPIC_OUT  # noqa: E402
from worker import steps  # noqa: E402


class GateBase(MakeBase):
    def setUp(self):
        super().setUp()
        (self.tmp / "원본" / "a.srt").write_text(SRT, encoding="utf-8")
        conn = deps.open_db(self.db)
        conn.execute("UPDATE sources SET srt_path='원본/a.srt' WHERE id=1")
        conn.close()

    def draft(self, **over):
        return self.post(plan_path="", ai_draft="1", **over)

    def worker_step(self, out):
        conn = deps.open_db(self.db)
        try:
            job = steps.claim_next_llm(conn)
            with mock.patch.object(llm, "call", return_value=out):
                steps.run_step(conn, job, self.tmp)
        finally:
            conn.close()

    def state(self, job_id=1):
        return [(j["state"], j["queue_state"], j["hold_reason"]) for j in self.jobs() if j["id"] == job_id][0]

    def token_post(self, path, **data):
        data["csrf_token"] = self.token("/gate/1")
        return self.client.post(path, data=data, headers={"origin": BASE}, follow_redirects=False)


class DraftIntakeTest(GateBase):
    def test_form_has_ai_draft_checkbox(self):
        for path in ("/make", "/make/oneclick"):
            self.assertIn('name="ai_draft"', self.client.get(path).text)

    def test_draft_creates_waiting_topic_job_without_plan(self):
        r = self.draft()
        self.assertEqual(r.status_code, 303)
        j = self.jobs()[0]
        self.assertEqual((j["state"], j["queue_state"], j["plan_path"]), ("topic", "waiting", None))

    def test_draft_requires_srt_rights_and_file(self):
        conn = deps.open_db(self.db)
        conn.execute("UPDATE sources SET srt_path=NULL WHERE id=1")
        conn.close()
        self.assertEqual(self.draft().status_code, 422)
        conn = deps.open_db(self.db)
        conn.execute("UPDATE sources SET srt_path='원본/a.srt', rights_status='unverified' WHERE id=1")
        conn.close()
        self.assertEqual(self.draft().status_code, 422)
        conn = deps.open_db(self.db)
        conn.execute("UPDATE sources SET rights_status='own' WHERE id=1")
        conn.close()
        self.assertEqual(self.draft(source_id="2").status_code, 422)
        self.assertEqual(self.jobs(), [])

    def test_without_draft_plan_path_still_required(self):
        self.assertEqual(self.post(plan_path="").status_code, 422)


class GateFlowTest(GateBase):
    def test_full_gate_flow(self):
        self.draft()
        self.worker_step(TOPIC_OUT)
        self.assertEqual(self.state(), ("topic", "held_human", steps.GATE1_REASON))
        page = self.client.get("/gate/1")
        self.assertIn("관문 ① 소재 선택", page.text)
        self.assertIn('name="topic_id"', page.text)
        self.assertIn("관문", self.client.get("/status").text)
        self.assertIn('href="/gate/1"', self.client.get("/status").text)

        conn = deps.open_db(self.db)
        tid = conn.execute("SELECT id FROM topics ORDER BY id").fetchone()[0]
        conn.close()
        r = self.token_post("/gate/1/topic", topic_id=str(tid))
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.state()[:2], ("topic", "waiting"))

        self.worker_step(FLOW_OUT)
        self.assertEqual(self.state(), ("topic", "held_human", steps.GATE2_REASON))
        self.assertIn("관문 ② 흐름·제목 선택", self.client.get("/gate/1").text)
        r = self.token_post("/gate/1/flow", title_index="0")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.state()[:2], ("topic", "waiting"))
        self.assertTrue((self.tmp / "var" / "jobs" / "1" / steps.CHOICE_FILE).exists())

    def test_wrong_state_and_bad_input_rejected(self):
        self.draft()
        self.assertIn("고를 항목이 없습니다", self.client.get("/gate/1").text)  # 아직 관문 아님
        self.assertEqual(self.token_post("/gate/1/topic", topic_id="1").status_code, 409)
        self.assertEqual(self.token_post("/gate/1/topic", topic_id="abc").status_code, 409)
        self.assertEqual(self.token_post("/gate/1/flow", title_index="-1").status_code, 409)
        self.assertIn("고를 항목이 없습니다", self.client.get("/gate/999").text)

    def test_status_shows_ai_usage_and_no_approve_button_before_plan(self):
        self.draft()
        self.worker_step(TOPIC_OUT)
        conn = deps.open_db(self.db)
        conn.execute("INSERT INTO llm_calls(job_id, step, model, input_sha256, status, cost_usd) "
                     "VALUES (1, 'topic', 'm', 'h', 'ok', 1.5)")
        conn.close()
        text = self.client.get("/status").text
        self.assertIn("AI 사용량", text)
        self.assertIn("$1.50", text)
        self.assertNotIn("기획 승인 열기", text)  # 컷표가 생기기 전에는 승인 폼 없음

    def test_post_needs_csrf(self):
        self.draft()
        self.token("/gate/1")
        r = self.client.post("/gate/1/topic", data={"topic_id": "1"}, headers={"origin": BASE},
                             follow_redirects=False)
        self.assertIn(r.status_code, (400, 403))


if __name__ == "__main__":
    unittest.main()
