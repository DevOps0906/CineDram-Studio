"""core/llm.py — 단계별 1호출·비용 상한·중복·권리·거절·검증 (실제 API 호출 0: SDK 모킹)."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import anthropic
import httpx2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import llm, subproc  # noqa: E402

_spec = importlib.util.spec_from_file_location("db_migrate_llm", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

FAKE_KEY = "sk-ant-test-" + "x" * 24
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "score": {"type": "number"},
        "meta": {"type": "object", "properties": {"note": {"type": "string"}},
                 "additionalProperties": False},
    },
    "required": ["title", "score"],
    "additionalProperties": False,
}
ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "CINEDRAM_LLM_MODEL", "CINEDRAM_LLM_EFFORT", "CINEDRAM_LLM_MAX_TOKENS",
            "CINEDRAM_LLM_MONTHLY_USD", "CINEDRAM_LLM_DAILY_USD", "CINEDRAM_LLM_JOB_USD")


def response(data=None, stop_reason="end_turn", text=None, iterations=None, model="claude-opus-5-5",
             category=None):
    content = [] if stop_reason == "refusal" else [
        SimpleNamespace(type="text", text=text if text is not None else json.dumps(data))]
    usage = SimpleNamespace(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0,
                            cache_read_input_tokens=0, iterations=iterations)
    return SimpleNamespace(content=content, stop_reason=stop_reason, model=model, usage=usage,
                           stop_details=SimpleNamespace(category=category), _request_id="req_test")


class LlmTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="llm_테스트_"))
        db = self.tmp / "studio.sqlite3"
        migrate_mod.migrate(db)
        self.conn = migrate_mod.connect(db)
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.addCleanup(self.conn.close)
        env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
        env["ANTHROPIC_API_KEY"] = FAKE_KEY
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.conn.execute("INSERT INTO works(title) VALUES ('작품')")
        self.job = self.add_job("own")
        self.client = mock.MagicMock()
        p = mock.patch.object(llm, "make_client", return_value=self.client)
        self.make_client = p.start()
        self.addCleanup(p.stop)

    def add_job(self, rights):
        cur = self.conn.execute("INSERT INTO sources(work_id, file_path, origin, rights_status) "
                                "VALUES (1, 'a.mp4', 'local_file', ?)", (rights,))
        return self.conn.execute("INSERT INTO jobs(source_id) VALUES (?)", (cur.lastrowid,)).lastrowid

    def rows(self):
        return self.conn.execute("SELECT status, cost_usd, usage_in, usage_out, error, served_model "
                                 "FROM llm_calls ORDER BY id").fetchall()

    def hold(self, *a, **kw):
        with self.assertRaises(llm.LlmHold) as ctx:
            llm.call(self.conn, *a, **kw)
        return ctx.exception


class CallTest(LlmTestBase):
    def test_ok_records_tokens_and_cost(self):
        self.client.beta.messages.create.return_value = response({"title": "제목", "score": 0.9})
        out = llm.call(self.conn, self.job, "topic", ["자막 본문"], SCHEMA)
        self.assertEqual(out, {"title": "제목", "score": 0.9})
        self.make_client.assert_called_once_with(FAKE_KEY)
        kw = self.client.beta.messages.create.call_args.kwargs
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["fallbacks"], "default")
        self.assertEqual(kw["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(kw["output_config"]["effort"], "medium")
        self.assertEqual(kw["output_config"]["format"], {"type": "json_schema", "schema": SCHEMA})
        status, cost, tin, tout, err, served = self.rows()[0]
        self.assertEqual((status, tin, tout, err, served), ("ok", 1000, 500, None, "claude-opus-5-5"))
        self.assertAlmostEqual(cost, (1000 * 4 + 500 * 20) / 1e6)

    def test_model_and_effort_from_env(self):
        os.environ.update(CINEDRAM_LLM_MODEL="claude-opus-5", CINEDRAM_LLM_EFFORT="low")
        self.client.beta.messages.create.return_value = response({"title": "t", "score": 1})
        llm.call(self.conn, self.job, "flow", ["x"], SCHEMA)
        kw = self.client.beta.messages.create.call_args.kwargs
        self.assertEqual((kw["model"], kw["output_config"]["effort"]), ("claude-opus-5", "low"))

    def test_unknown_model_held_resource(self):
        os.environ["CINEDRAM_LLM_MODEL"] = "claude-unknown"
        e = self.hold(self.job, "topic", ["x"], SCHEMA)
        self.assertEqual(e.queue_state, "held_resource")
        self.client.beta.messages.create.assert_not_called()

    def test_fallback_iterations_cost(self):
        it = [SimpleNamespace(type="message", input_tokens=1000, output_tokens=0),
              SimpleNamespace(type="fallback_message", input_tokens=1000, output_tokens=500)]
        self.client.beta.messages.create.return_value = response(
            {"title": "t", "score": 1}, iterations=it, model="claude-opus-4-8")
        llm.call(self.conn, self.job, "topic", ["x"], SCHEMA)
        self.assertAlmostEqual(self.rows()[0][1], (1000 * 4 + 1000 * 5 + 500 * 25) / 1e6)


class HoldTest(LlmTestBase):
    def test_rights_unverified_no_call(self):
        job = self.add_job("unverified")
        e = self.hold(job, "topic", ["x"], SCHEMA)
        self.assertEqual(e.queue_state, "held_human")
        self.client.beta.messages.create.assert_not_called()
        self.assertEqual(self.rows(), [])

    def test_no_key_held_resource(self):
        del os.environ["ANTHROPIC_API_KEY"]
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "other"  # 다른 자격 증명 경로는 쓰지 않는다
        e = self.hold(self.job, "topic", ["x"], SCHEMA)
        self.assertEqual(e.queue_state, "held_resource")
        self.make_client.assert_not_called()
        self.assertEqual(self.rows(), [])

    def test_refusal_held_human(self):
        self.client.beta.messages.create.return_value = response(stop_reason="refusal", category="cyber")
        e = self.hold(self.job, "topic", ["x"], SCHEMA)
        self.assertEqual(e.queue_state, "held_human")
        self.assertEqual(self.rows()[0][0], "refused")

    def test_max_tokens_held_human(self):
        self.client.beta.messages.create.return_value = response(text='{"title":', stop_reason="max_tokens")
        self.assertEqual(self.hold(self.job, "topic", ["x"], SCHEMA).queue_state, "held_human")
        self.assertEqual(self.rows()[0][0], "invalid")

    def test_validation_failures_held_human(self):
        cases = ({"title": 1, "score": 1}, {"title": "t"}, {"title": "t", "score": 1, "extra": 1},
                 {"title": "t", "score": 1, "meta": {"rights_status": "own"}}, "not json")
        for i, data in enumerate(cases):
            with self.subTest(data=data):
                text = data if isinstance(data, str) else json.dumps(data)
                self.client.beta.messages.create.return_value = response(text=text)
                e = self.hold(self.job, "topic", [f"x{i}"], SCHEMA)
                self.assertEqual(e.queue_state, "held_human")
        self.assertEqual({r[0] for r in self.rows()}, {"invalid"})

    def test_api_error_masked_and_clipped(self):
        req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        self.client.beta.messages.create.side_effect = anthropic.APIConnectionError(
            message=f"boom api_key={FAKE_KEY} " + "y" * 1000, request=req)
        e = self.hold(self.job, "topic", ["x"], SCHEMA)
        self.assertEqual(e.queue_state, "held_resource")
        status, cost, *_rest = self.rows()[0]
        err = _rest[2]
        self.assertEqual(status, "error")
        self.assertGreater(cost, 0)  # 비용 미상 → 예약값 유지
        self.assertNotIn(FAKE_KEY, err)
        self.assertLessEqual(len(err), llm.ERROR_MAX)
        self.assertNotIn(FAKE_KEY, e.reason)

    def test_status_error_cost_zero(self):
        req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        resp = httpx2.Response(400, request=req, json={"error": {"message": "bad"}})
        self.client.beta.messages.create.side_effect = anthropic.BadRequestError(
            "bad", response=resp, body=None)
        self.assertEqual(self.hold(self.job, "topic", ["x"], SCHEMA).queue_state, "held_resource")
        self.assertEqual(self.rows()[0][:2], ("error", 0))

    def test_interrupt_closes_row_and_reraises(self):
        self.client.beta.messages.create.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            llm.call(self.conn, self.job, "topic", ["x"], SCHEMA)
        self.assertEqual(self.rows()[0][0], "error")

    def test_unexpected_response_closes_row(self):
        bad = response({"title": "t", "score": 1})
        bad.content = 42  # 순회 불가
        self.client.beta.messages.create.return_value = bad
        with self.assertRaises(TypeError):
            llm.call(self.conn, self.job, "topic", ["x"], SCHEMA)
        self.assertEqual(self.rows()[0][0], "error")

    def test_custom_headers_env_blocks(self):
        os.environ["ANTHROPIC_CUSTOM_HEADERS"] = "X-Leak: 1"
        self.assertEqual(self.hold(self.job, "topic", ["x"], SCHEMA).queue_state, "held_resource")
        self.make_client.assert_not_called()

    def test_nested_forbidden_output(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["items"],
                  "properties": {"items": {"type": "array", "items": {
                      "type": "object", "additionalProperties": False,
                      "properties": {"note": {"type": "string"}}}}}}
        self.client.beta.messages.create.return_value = response(
            {"items": [{"note": "a", "source_video": "b.mp4"}]})
        e = self.hold(self.job, "topic", ["x"], schema)
        self.assertEqual(e.queue_state, "held_human")
        self.assertIn("source_video", self.rows()[0][4])


class LimitTest(LlmTestBase):
    def seed_cost(self, usd, job=None):
        self.conn.execute("INSERT INTO llm_calls(job_id, step, model, input_sha256, status, cost_usd) "
                          "VALUES (?, 'topic', 'm', ?, 'ok', ?)", (job or self.job, os.urandom(8).hex(), usd))

    def assert_resource(self, *a):
        e = self.hold(*a)
        self.assertEqual(e.queue_state, "held_resource")
        self.client.beta.messages.create.assert_not_called()

    def test_monthly_cap(self):
        other = self.add_job("own")
        for _ in range(4):
            self.seed_cost(4.99, other)
        os.environ.update(CINEDRAM_LLM_DAILY_USD="100", CINEDRAM_LLM_JOB_USD="100")
        self.assert_resource(self.job, "topic", ["x"], SCHEMA)

    def test_daily_cap(self):
        os.environ["CINEDRAM_LLM_DAILY_USD"] = "0.01"
        self.assert_resource(self.job, "topic", ["x"], SCHEMA)

    def test_job_cap(self):
        self.seed_cost(1.99)
        self.assert_resource(self.job, "topic", ["x"], SCHEMA)
        other = self.add_job("own")
        self.client.beta.messages.create.return_value = response({"title": "t", "score": 1})
        llm.call(self.conn, other, "topic", ["x"], SCHEMA)  # 다른 작업은 통과

    def test_previous_month_not_counted(self):
        self.conn.execute("INSERT INTO llm_calls(job_id, step, model, input_sha256, status, cost_usd, created_at) "
                          "VALUES (?, 'topic', 'm', 'old', 'ok', 50, '2000-01-01T00:00:00.000Z')",
                          (self.add_job("own"),))
        self.client.beta.messages.create.return_value = response({"title": "t", "score": 1})
        llm.call(self.conn, self.job, "topic", ["x"], SCHEMA)

    def test_duplicate_input_blocked(self):
        self.client.beta.messages.create.return_value = response({"title": "t", "score": 1})
        llm.call(self.conn, self.job, "topic", ["x"], SCHEMA)
        e = self.hold(self.job, "topic", ["x"], SCHEMA)
        self.assertEqual(e.queue_state, "held_human")
        self.assertEqual(self.client.beta.messages.create.call_count, 1)
        llm.call(self.conn, self.job, "flow", ["x"], SCHEMA)  # 다른 단계는 별개

    def test_retry_after_invalid_allowed(self):
        self.client.beta.messages.create.return_value = response(text="nope")
        self.hold(self.job, "topic", ["x"], SCHEMA)
        self.client.beta.messages.create.return_value = response({"title": "t", "score": 1})
        llm.call(self.conn, self.job, "topic", ["x"], SCHEMA)
        self.assertEqual([r[0] for r in self.rows()], ["invalid", "ok"])

    def test_db_unique_dedup(self):
        import sqlite3
        self.seed_cost(0)
        h = self.conn.execute("SELECT input_sha256 FROM llm_calls").fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO llm_calls(job_id, step, model, input_sha256, status) "
                              "VALUES (?, 'topic', 'm', ?, 'started')", (self.job, h))

    def test_reserve_is_worst_case(self):
        once = llm.reserve_cost("claude-opus-5-5", ["x"], SCHEMA, 1000) / ((1 + llm.MAX_RETRIES) * 2)
        n = len((llm.SYSTEM + "x" + json.dumps(SCHEMA)).encode("utf-8"))
        self.assertAlmostEqual(once, (n * 5 + 1000 * 25) / 1e6)

    def test_kst_day_boundary(self):
        os.environ.update(CINEDRAM_LLM_DAILY_USD="3", CINEDRAM_LLM_MONTHLY_USD="1000",
                          CINEDRAM_LLM_JOB_USD="1000")
        other = self.add_job("own")
        now = datetime(2026, 11, 2, 15, 30, tzinfo=timezone.utc)  # KST 11-03 00:30
        self.client.beta.messages.create.return_value = response({"title": "t", "score": 1})
        with mock.patch.object(llm, "_now", return_value=now):
            self.seed_at(other, 10, "2026-11-02T14:59:59.999Z")  # KST 전날
            llm.call(self.conn, self.job, "topic", ["a"], SCHEMA)
            self.seed_at(other, 10, "2026-11-02T15:00:00.000Z")  # KST 당일 0시
            self.assertEqual(self.hold(self.job, "topic", ["b"], SCHEMA).queue_state, "held_resource")

    def test_kst_month_boundary(self):
        os.environ.update(CINEDRAM_LLM_DAILY_USD="1000", CINEDRAM_LLM_JOB_USD="1000")
        other = self.add_job("own")
        now = datetime(2026, 11, 30, 15, 10, tzinfo=timezone.utc)  # KST 12-01 00:10
        self.client.beta.messages.create.return_value = response({"title": "t", "score": 1})
        with mock.patch.object(llm, "_now", return_value=now):
            self.seed_at(other, 50, "2026-11-30T14:00:00.000Z")  # KST 11월
            llm.call(self.conn, self.job, "topic", ["a"], SCHEMA)
            self.seed_at(other, 50, "2026-11-30T15:05:00.000Z")  # KST 12월
            self.assertEqual(self.hold(self.job, "topic", ["b"], SCHEMA).queue_state, "held_resource")

    def test_kst_bounds(self):
        day, month = llm.kst_bounds(datetime(2026, 11, 2, 15, 30, tzinfo=timezone.utc))
        self.assertEqual((day, month), ("2026-11-02T15:00:00.000Z", "2026-10-31T15:00:00.000Z"))

    def test_recover_stale(self):
        now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
        for h, created in (("old", "2026-11-03T10:00:00.000Z"), ("new", "2026-11-03T11:30:00.000Z")):
            self.conn.execute("INSERT INTO llm_calls(job_id, step, model, input_sha256, status, cost_usd, "
                              "created_at) VALUES (?, 'topic', 'm', ?, 'started', 1, ?)", (self.job, h, created))
        with mock.patch.object(llm, "_now", return_value=now):
            self.assertEqual(llm.recover_stale(self.conn), 1)
        rows = dict(self.conn.execute("SELECT input_sha256, status FROM llm_calls"))
        self.assertEqual(rows, {"old": "error", "new": "started"})
        self.assertEqual(self.conn.execute("SELECT SUM(cost_usd) FROM llm_calls").fetchone()[0], 2)

    def seed_at(self, job, usd, created_at):
        self.conn.execute("INSERT INTO llm_calls(job_id, step, model, input_sha256, status, cost_usd, created_at) "
                          "VALUES (?, 'topic', 'm', ?, 'ok', ?, ?)", (job, os.urandom(8).hex(), usd, created_at))


class SchemaTest(unittest.TestCase):
    def test_forbidden_meta_fields(self):
        for k in llm.FORBIDDEN_META:
            bad = json.loads(json.dumps(SCHEMA))
            bad["properties"]["meta"]["properties"][k] = {"type": "string"}
            with self.subTest(k=k), self.assertRaises(ValueError):
                llm.check_schema(bad)

    def test_meta_must_be_closed(self):
        bad = json.loads(json.dumps(SCHEMA))
        bad["properties"]["meta"]["additionalProperties"] = True
        with self.assertRaises(ValueError):
            llm.check_schema(bad)

    def test_forbidden_anywhere_in_schema(self):
        bad = {"type": "object", "additionalProperties": False, "properties": {
            "items": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                  "properties": {"origin": {"type": "string"}}}}}}
        with self.assertRaises(ValueError):
            llm.check_schema(bad)

    def test_nested_object_must_be_closed(self):
        bad = {"type": "object", "additionalProperties": False, "properties": {
            "x": {"type": "object", "properties": {}}}}
        with self.assertRaises(ValueError):
            llm.check_schema(bad)

    def test_top_level_must_be_closed(self):
        with self.assertRaises(ValueError):
            llm.check_schema({"type": "object", "properties": {}})


class SdkWireTest(LlmTestBase):
    """실제 SDK 직렬화 확인 — 네트워크 대신 httpx2.MockTransport."""

    def test_request_shape_through_sdk(self):
        seen = {}

        def handler(request):
            seen["headers"] = request.headers
            seen["body"] = json.loads(request.content)
            seen["host"] = request.url.host
            return httpx2.Response(200, json={
                "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": json.dumps({"title": "t", "score": 2})}],
                "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5}})

        os.environ["ANTHROPIC_BASE_URL"] = "https://evil.example"  # 무시돼야 한다
        client = anthropic.Anthropic(api_key=FAKE_KEY, base_url=llm.API_BASE, max_retries=0,
                                     http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
        self.make_client.return_value = client
        self.assertEqual(llm.call(self.conn, self.job, "subtitle", ["x"], SCHEMA), {"title": "t", "score": 2})
        body = seen["body"]
        self.assertEqual(body["fallbacks"], "default")
        self.assertEqual(body["output_config"]["effort"], "medium")
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("thinking", body)
        self.assertEqual(seen["host"], "api.anthropic.com")
        self.assertIn("server-side-fallback-2026-07-01", seen["headers"]["anthropic-beta"])
        self.assertEqual(seen["headers"]["authorization"], f"Bearer {FAKE_KEY}")  # SDK 2.x 인증 헤더


class EnvIsolationTest(unittest.TestCase):
    def test_key_not_passed_to_engine_subprocess(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": FAKE_KEY}):
            env = subproc.build_env()
        self.assertNotIn("ANTHROPIC_API_KEY", {k.upper() for k in env})
        self.assertNotIn(FAKE_KEY, env.values())


class MakeClientTest(unittest.TestCase):
    def test_explicit_key_and_sdk_retries(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "https://evil.example"}):
            c = llm.make_client(FAKE_KEY)
        self.assertEqual(c.api_key, FAKE_KEY)
        self.assertEqual(c.base_url.host, "api.anthropic.com")
        self.assertEqual(c.max_retries, llm.MAX_RETRIES)


if __name__ == "__main__":
    unittest.main()
