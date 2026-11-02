"""R5·R6 제작 현황 + R9 폼 렌더 + R15 버튼 (plan-dev3 §5 U2)."""
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402
from server.routes import status as status_mod  # noqa: E402

_spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

BASE = "http://127.0.0.1:8765"
PLAN = {"meta": {"title": ["제목 하나", "제목 둘"], "source_video": "원본.mp4"},
        "cuts": [{"no": 1, "kind": "dialog", "src_start": "01:40.0", "src_end": "01:43.0"},
                 {"no": 2, "kind": "narration", "beat": 1, "src_start": "01:43.0", "quiet_end": "01:52.0",
                  "tts_text": "나레이션"}]}
FORM_ACTION = re.compile(r'action="(/jobs/\d+/[a-z-]+)"')


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "t.db"
        migrate_mod.migrate(self.db, backup=False)
        self.conn = migrate_mod.connect(self.db)
        self.conn.execute("INSERT INTO works(id, title) VALUES (1, '달빛 수선소')")
        self.sid = {}
        for k, rights in (("own", "own"), ("unv", "unverified")):
            cur = self.conn.execute("INSERT INTO sources(work_id, episode, file_path, origin, rights_status) "
                                    "VALUES (1, 4, ?, 'local_file', ?)", (f"원본/{k}.mp4", rights))
            self.sid[k] = cur.lastrowid
        self.conn.commit()
        app = create_app(db_path=self.db)
        app.state.root = self.dir
        self.client = TestClient(app, base_url=BASE, client=("127.0.0.1", 50000))

    def tearDown(self):
        self.client.close()
        self.conn.close()
        self.tmp.cleanup()

    def job(self, state, src="own", queue_state="waiting", plan_path=None, **kw):
        cur = self.conn.execute(
            "INSERT INTO jobs(source_id, rule_set, state, queue_state, plan_path, hold_reason) VALUES (?,?,?,?,?,?)",
            (self.sid[src] if src else None, '["common", "한드짜"]', state, queue_state, plan_path, kw.get("hold")))
        self.conn.commit()
        return cur.lastrowid

    def write_plan(self, name="p/컷표.json"):
        f = self.dir / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
        return name

    def card(self, html, jid):
        m = re.search(rf'<article class="card" id="job-{jid}".*?</article>', html, re.S)
        self.assertIsNotNone(m, f"job {jid} 카드 없음")
        return m.group(0)

    # ---- R5·R6 ----
    def test_state_to_progress_mapping(self):
        # 전 상태: 진행 중 칸(aria-current)이 표와 같다
        expected = {"queued": "소재", "topic": "소재", "matching": "원본", "need_source": "원본", "building": "제작",
                    "blocked_disk": "제작", "review": "검수", "approved": "업로드", "scheduled": "업로드",
                    "uploading": "업로드", "blocked_auth": "업로드"}
        self.assertEqual(set(expected), set(status_mod.ACTIVE_STATES))
        ids = {s: self.job(s) for s in expected}
        html = self.client.get("/status").text
        for s, step in expected.items():
            c = self.card(html, ids[s])
            now = re.search(r'<li class="now" aria-current="step"><span class="step-name">([^<{]*)', c).group(1).strip()
            self.assertEqual(now, step, s)
            self.assertEqual(c.count('class="done"'), status_mod.STATE_VIEW[s][0], s)

    def test_finished_states_hidden(self):
        for s in ("done", "canceled"):
            self.job(s, queue_state="finished")
        self.assertIn("진행 중인 제작이 없습니다", self.client.get("/status").text)
        self.assertNotIn("최근 실패", self.client.get("/status").text)

    def fail_job(self, error, n=1):
        ids = []
        for _ in range(n):
            jid = self.job("failed", queue_state="finished")
            self.conn.execute("UPDATE jobs SET finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?", (jid,))
            self.conn.execute("INSERT INTO job_events(job_id, from_state, to_state, error) VALUES (?, 'building', 'failed', ?)",
                              (jid, error))
            ids.append(jid)
        self.conn.commit()
        return ids

    def test_recent_failures_group(self):
        (jid,) = self.fail_job("앞줄" + chr(10) + "렌더 실패: ffmpeg 종료 코드 1")
        html = self.client.get("/status").text
        self.assertIn("최근 실패", html)
        c = self.card(html, jid)
        self.assertIn("렌더 실패: ffmpeg 종료 코드 1", c)
        self.assertNotIn("앞줄", c)
        self.assertIn("최근 실패", self.client.get("/status/fragment").text)

    def test_recent_failures_masked_and_limited(self):
        self.fail_job("키 sk-ABCDEF1234567890ABCDEF1234567890 노출", n=12)
        html = self.client.get("/status").text
        self.assertEqual(html.count('data-state="failed"'), 10)
        self.assertNotIn("sk-ABCDEF1234567890ABCDEF1234567890", html)

    def test_polling_area_a11y(self):
        self.job("building")
        html = self.client.get("/status").text
        main = html[html.index('<main'):html.index('</main>')]
        self.assertNotIn("aria-live", main)
        self.assertNotIn("aria-pressed", main)
        self.assertNotIn('class="pill"', main)
        js = (ROOT / "server" / "static" / "status.js").read_text(encoding="utf-8")
        self.assertIn("box.contains(document.activeElement)", js)
        self.assertNotIn("갱신 실패 · 마지막", js)

    def test_empty_state(self):
        r = self.client.get("/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("진행 중인 제작이 없습니다", r.text)
        self.assertIn('href="/make"', r.text)

    def test_sections_and_counts(self):
        self.job("building")
        self.job("review")
        self.job("topic", queue_state="held_human", plan_path=self.write_plan())
        html = self.client.get("/status").text
        self.assertIn("진행 중 1", html)
        self.assertIn("승인 대기 2", html)

    def test_badges(self):
        a = self.job("need_source")
        b = self.job("blocked_disk")
        c = self.job("review", src="unv")
        d = self.job("building")
        html = self.client.get("/status").text
        self.assertIn("원본 없음", self.card(html, a))
        self.assertIn("디스크 부족", self.card(html, b))
        cc = self.card(html, c)
        self.assertIn("권리 미확인", cc)
        self.assertIn("업로드 검토 필요", cc)
        dd = self.card(html, d)
        self.assertIn("제작 가능", dd)
        self.assertNotIn("권리 미확인", dd)

    def test_card_title_and_elapsed(self):
        jid = self.job("building")
        c = self.card(self.client.get("/status").text, jid)
        self.assertIn("달빛 수선소 · E04", c)
        self.assertIn("한드짜", c)
        self.assertNotIn("common", c)
        self.assertRegex(c, r"\d+(초|분)")

    def test_fragment_is_list_only(self):
        jid = self.job("building")
        r = self.client.get("/status/fragment")
        self.assertEqual(r.status_code, 200)
        self.assertIn(f'id="job-{jid}"', r.text)
        self.assertNotIn("<html", r.text)
        self.assertNotIn("<script", r.text)

    def test_fragment_empty(self):
        self.assertIn("진행 중인 제작이 없습니다", self.client.get("/status/fragment").text)

    def test_page_has_no_inline_script_or_style(self):
        self.job("building")
        html = self.client.get("/status").text
        self.assertIn('src="/static/status.js"', html)
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
        self.assertNotRegex(html, r"<style|style=")
        self.assertEqual(self.client.get("/static/status.js").status_code, 200)

    def test_xss_escaped(self):
        self.conn.execute("UPDATE works SET title='<img src=x onerror=1>'")
        self.conn.commit()
        jid = self.job("building")
        c = self.card(self.client.get("/status").text, jid)
        self.assertNotIn("<img", c)
        self.assertIn("&lt;img", c)

    # ---- R9 폼 ----
    def test_r9_form_rendered_for_topic(self):
        jid = self.job("topic", queue_state="held_human", plan_path=self.write_plan())
        c = self.card(self.client.get(f"/status?approve={jid}").text, jid)
        self.assertIn(f'action="/jobs/{jid}/approve-plan"', c)
        for needle in ('name="csrf_token"', 'name="gate_ok" value="yes"', 'name="confirm" value="yes"'):
            self.assertIn(needle, c)
        h = re.search(r'name="plan_hash" value="([0-9a-f]{16})"', c)
        self.assertIsNotNone(h)
        self.assertIn("2컷", c)
        self.assertIn("제목 하나 / 제목 둘", c)
        self.assertIn("약 12.0초", c)  # 3 + 9
        # 같은 컷표는 같은 해시, 내용이 바뀌면 다른 해시
        again = re.search(r'name="plan_hash" value="(\w+)"',
                          self.card(self.client.get(f"/status?approve={jid}").text, jid)).group(1)
        self.assertEqual(again, h.group(1))
        p = self.dir / "p" / "컷표.json"
        p.write_text(p.read_text(encoding="utf-8").replace("제목 둘", "제목 셋"), encoding="utf-8")
        changed = re.search(r'name="plan_hash" value="(\w+)"',
                            self.card(self.client.get(f"/status?approve={jid}").text, jid)).group(1)
        self.assertNotEqual(changed, h.group(1))

    def test_r9_csrf_token_matches_cookie(self):
        jid = self.job("topic", queue_state="held_human", plan_path=self.write_plan())
        r = self.client.get(f"/status?approve={jid}")
        tok = re.search(r'name="csrf_token" value="([^"]+)"', self.card(r.text, jid)).group(1)
        self.assertEqual(tok, self.client.cookies.get("csrf"))

    def test_r9_unreadable_plan_disables_approve(self):
        a = self.job("topic", queue_state="held_human", plan_path="없음/컷표.json")
        b = self.job("topic", queue_state="held_human", plan_path="../밖.json")
        c = self.job("topic", queue_state="held_human")
        for jid in (a, b, c):
            card = self.card(self.client.get(f"/status?approve={jid}").text, jid)
            self.assertNotIn("approve-plan", card)
            self.assertIn("승인할 수 없습니다", card)

    def test_r9_form_only_in_topic(self):
        a = self.job("building")
        self.job("review")
        self.assertNotIn("approve-plan", self.client.get(f"/status?approve={a}").text)

    def test_r9_hash_not_computed_in_list_or_fragment(self):
        jid = self.job("topic", queue_state="held_human", plan_path=self.write_plan())
        calls = []
        orig = status_mod.approval.run_gate
        status_mod.approval.run_gate = lambda *a, **k: calls.append(a) or (False, "x" * 16, "")
        try:
            for url in ("/status", "/status/fragment"):
                c = self.card(self.client.get(url).text, jid)
                self.assertIn(f"/status?approve={jid}", c)
                self.assertNotIn("approve-plan", c)
            self.assertEqual(calls, [])
            self.assertIn("approve-plan", self.card(self.client.get(f"/status?approve={jid}").text, jid))
            self.assertEqual(len(calls), 1)
        finally:
            status_mod.approval.run_gate = orig

    def test_r9_only_topic_held_human(self):
        w = self.job("topic", queue_state="waiting", plan_path=self.write_plan())
        c = self.card(self.client.get(f"/status?approve={w}").text, w)
        self.assertNotIn("approve-plan", c)
        self.assertNotIn("approve=", c)

    # ---- R15 ----
    def test_r15_button_only_when_building_held(self):
        held = self.job("building", queue_state="held_human", hold="컷표 변경")
        running = self.job("building", queue_state="running")
        waiting = self.job("building", queue_state="waiting")
        topic = self.job("topic", queue_state="held_human", plan_path=self.write_plan())
        html = self.client.get("/status").text
        c = self.card(html, held)
        self.assertIn(f'action="/jobs/{held}/revert-plan"', c)
        self.assertIn('name="confirm" value="yes"', c)
        self.assertIn('name="csrf_token"', c)
        self.assertIn("기획 수정으로 되돌리기", c)
        self.assertIn("컷표 변경", c)
        for other in (running, waiting, topic):
            self.assertNotIn("revert-plan", self.card(html, other))

    def test_forms_only_target_contract_routes(self):
        self.job("building", queue_state="held_human")
        self.job("topic", queue_state="held_human", plan_path=self.write_plan())
        html = self.client.get("/status").text
        for path in FORM_ACTION.findall(html):
            self.assertRegex(path, r"^/jobs/\d+/(approve-plan|revert-plan)$")


if __name__ == "__main__":
    unittest.main()
