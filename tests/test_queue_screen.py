"""R16~R20 대기 큐 화면과 쓰기 라우트 + R14 running 취소 (plan-dev5 Q-B)."""
import importlib.util
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402

from server.app import create_app  # noqa: E402

_spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

BASE = "http://127.0.0.1:8765"
ACTION = re.compile(r'action="(/jobs/\d+/[a-z-]+)"')


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "t.db"
        migrate_mod.migrate(self.db, backup=False)
        self.conn = migrate_mod.connect(self.db)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("INSERT INTO works(id, title) VALUES (1, '달빛 수선소'), (2, '파도 위 도서관')")
        for i, w in ((1, 1), (2, 2)):
            self.conn.execute("INSERT INTO sources(id, work_id, episode, file_path, origin, rights_status) "
                              "VALUES (?, ?, 4, ?, 'local_file', 'own')", (i, w, f"원본/{i}.mp4"))
        self.conn.commit()
        app = create_app(db_path=self.db)
        app.state.root = self.dir
        self.app = app
        self.client = TestClient(app, base_url=BASE, client=("127.0.0.1", 50000))
        self.addCleanup(self.client.close)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)

    def job(self, state="building", qs="waiting", src=1, plan_hash=None, plan_path=None, priority=0, hold=None):
        cur = self.conn.execute(
            "INSERT INTO jobs(source_id, rule_set, state, queue_state, plan_hash, plan_path, priority, hold_reason, slot_id) "
            "VALUES (?, '[\"common\",\"한드짜\"]', ?, ?, ?, ?, ?, ?, ?)",
            (src, state, qs, plan_hash, plan_path, priority, hold, "gpu0" if qs == "running" else None))
        self.conn.commit()
        return cur.lastrowid

    def row(self, jid):
        return dict(self.conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())

    def events(self, jid):
        return self.conn.execute("SELECT COUNT(*) FROM job_events WHERE job_id=?", (jid,)).fetchone()[0]

    def post(self, path, data=None, csrf=True, origin=True, client=None):
        c = client or TestClient(self.app, base_url=BASE, client=("127.0.0.1", 50000))
        c.get("/queue")
        d = dict(data or {})
        if csrf:
            d["csrf_token"] = c.cookies.get("csrf")
        return c.post(path, data=d, headers={"origin": BASE} if origin else {}, follow_redirects=False)

    # ---- R16 ----
    def test_empty_state_and_slots(self):
        r = self.client.get("/queue")
        self.assertEqual(r.status_code, 200)
        self.assertIn("대기 중인 작업이 없습니다", r.text)
        self.assertIn("/make/oneclick", r.text)
        self.assertIn("GPU 0/1", r.text)
        self.assertIn("LLM 0/1", r.text)
        self.assertIn("미사용", r.text)

    def test_groups_and_gpu_usage(self):
        self.job(qs="running")
        self.job(qs="waiting")
        self.job(state="topic", qs="held_human")
        self.job(qs="held_resource", hold="디스크 부족")
        self.job(qs="paused")
        self.job(state="failed", qs="finished")
        self.job(state="done", qs="finished")
        html = self.client.get("/queue").text
        self.assertIn("GPU 1/1", html)
        for label in ("실행 중", "대기", "사람 대기", "자원 대기", "일시정지", "실패"):
            self.assertRegex(html, rf'<h2 class="label muted">{label} ')
        self.assertEqual(len(re.findall(r'<article class="card" id="job-', html)), 6)  # done 은 안 보인다
        self.assertIn("대기 순번 1", html)
        self.assertIn("디스크 부족", html)

    def test_filter_search_and_more(self):
        self.job(qs="waiting", src=1)
        self.job(qs="paused", src=2)
        html = self.client.get("/queue?state=pause").text
        self.assertEqual(len(re.findall(r'<article class="card" id="job-', html)), 1)
        self.assertIn('aria-pressed="true"', html)
        html = self.client.get("/queue?q=" + "도서관").text
        self.assertEqual(len(re.findall(r'<article class="card" id="job-', html)), 1)
        self.assertIn("파도 위 도서관", html)
        self.assertIn("조건에 맞는 작업이 없습니다", self.client.get("/queue?q=없는작품").text)
        for _ in range(22):
            self.job(qs="waiting")
        html = self.client.get("/queue").text
        self.assertEqual(len(re.findall(r'<article class="card" id="job-', html)), 20)
        self.assertIn("page=2", html)
        html = self.client.get("/queue?page=2").text
        self.assertNotIn("더 보기", html)

    def test_q_like_wildcard_is_literal(self):
        self.job()
        self.assertIn("조건에 맞는 작업이 없습니다", self.client.get("/queue?q=%25").text)

    def test_r16_422(self):
        for url in ("/queue?state=zzz", "/queue?page=0", "/queue?page=x", "/queue?q=" + "a" * 51,
                    "/queue/fragment?state=zzz"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 422)

    def test_fragment_is_list_only_and_keeps_query(self):
        self.job()
        f = self.client.get("/queue/fragment?state=wait").text
        self.assertNotIn("<html", f)
        self.assertNotIn("<main", f)
        self.assertIn("job-", f)
        page = self.client.get("/queue?state=wait&q=%EB%8B%AC").text
        self.assertIn('data-fragment="/queue/fragment?state=wait&amp;q=', page)

    def test_polling_area_a11y_and_no_inline(self):
        self.job()
        html = self.client.get("/queue").text
        box = html[html.index('<div id="jobs"'):html.index('<script src="/static/queue.js"')]
        self.assertNotIn("aria-live", box)
        self.assertNotIn("aria-pressed", box)
        self.assertNotIn("<script", html.replace('<script src="/static/queue.js"', "").replace('<script src="/static/app.js"', ""))
        self.assertNotRegex(html, r'\sstyle=')
        js = (ROOT / "server" / "static" / "queue.js").read_text(encoding="utf-8")
        self.assertIn("box.contains(document.activeElement)", js)
        self.assertNotIn("aria-live", js)

    def test_forms_target_contract_and_csrf_and_buttons_by_state(self):
        w = self.job(qs="waiting")
        p = self.job(qs="paused")
        f = self.job(state="failed", qs="finished")
        run = self.job(qs="running")
        html = self.client.get("/queue").text
        for path in ACTION.findall(html):
            self.assertRegex(path, r"^/jobs/\d+/(pause|resume|priority|retry|cancel)$")
        self.assertEqual(html.count('name="csrf_token"'), len(ACTION.findall(html)))
        art = lambda j: re.search(rf'<article class="card" id="job-{j}".*?</article>', html, re.S).group(0)
        self.assertIn(f"/jobs/{w}/pause", art(w))
        self.assertNotIn("resume", art(w))
        self.assertIn(f"/jobs/{p}/resume", art(p))
        self.assertNotIn("/pause", art(p))
        self.assertIn(f"/jobs/{f}/retry", art(f))
        self.assertNotIn("priority", art(f))
        self.assertIn(f"/jobs/{run}/cancel", art(run))  # 실행 중도 취소 가능
        self.assertNotIn("priority", art(run))
        self.assertNotIn("/pause", art(run))

    def test_xss_escaped(self):
        self.conn.execute("UPDATE works SET title='<script>alert(1)</script>' WHERE id=1")
        self.conn.commit()
        self.job()
        html = self.client.get("/queue").text
        self.assertNotIn("<script>alert(1)", html)

    def test_failed_reason_masked_tail(self):
        j = self.job(state="failed", qs="finished")
        self.conn.execute("INSERT INTO job_events(job_id, from_state, to_state, error) VALUES (?, 'building', 'failed', ?)",
                          (j, "앞줄\napi_key=sk-ABCDEFGHIJKLMNOPQRSTUV\n마지막 줄 사유"))
        self.conn.commit()
        html = self.client.get("/queue").text
        self.assertIn("마지막 줄 사유", html)
        self.assertNotIn("앞줄", html)

    # ---- R17 / R18 ----
    def test_pause_resume(self):
        j = self.job(qs="waiting")
        self.assertEqual(self.post(f"/jobs/{j}/pause", {}).status_code, 422)
        self.assertEqual(self.row(j)["queue_state"], "waiting")
        r = self.post(f"/jobs/{j}/pause", {"confirm": "yes"})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/queue"))
        row = self.row(j)
        self.assertEqual((row["queue_state"], row["state"]), ("paused", "building"))
        self.assertEqual(self.events(j), 1)
        self.assertEqual(self.post(f"/jobs/{j}/pause", {"confirm": "yes"}).status_code, 409)  # 이중 클릭
        self.assertEqual(self.post(f"/jobs/{j}/resume", {}).status_code, 422)
        self.assertEqual(self.post(f"/jobs/{j}/resume", {"confirm": "yes"}).status_code, 303)
        self.assertEqual(self.row(j)["queue_state"], "waiting")
        self.assertEqual(self.post(f"/jobs/{j}/resume", {"confirm": "yes"}).status_code, 409)

    def test_pause_resume_409_by_state_and_404(self):
        for qs, st in (("running", "building"), ("held_human", "topic"), ("held_resource", "building"),
                       ("finished", "failed")):
            j = self.job(state=st, qs=qs)
            with self.subTest(qs=qs):
                self.assertEqual(self.post(f"/jobs/{j}/pause", {"confirm": "yes"}).status_code, 409)
                self.assertEqual(self.post(f"/jobs/{j}/resume", {"confirm": "yes"}).status_code, 409)
                self.assertEqual(self.row(j)["queue_state"], qs)
        self.assertEqual(self.post("/jobs/9999/pause", {"confirm": "yes"}).status_code, 404)

    # ---- R19 ----
    def test_priority_up_down_and_422_409(self):
        j = self.job(qs="waiting", priority=5)
        self.assertEqual(self.post(f"/jobs/{j}/priority", {"confirm": "yes", "dir": "up"}).status_code, 303)
        self.assertEqual(self.row(j)["priority"], 6)
        self.assertEqual(self.post(f"/jobs/{j}/priority", {"confirm": "yes", "dir": "down"}).status_code, 303)
        self.assertEqual(self.post(f"/jobs/{j}/priority", {"confirm": "yes", "dir": "down"}).status_code, 303)
        self.assertEqual(self.row(j)["priority"], 4)
        for bad in ({"confirm": "yes", "dir": "sideways"}, {"confirm": "yes"}, {"dir": "up"}):
            self.assertEqual(self.post(f"/jobs/{j}/priority", bad).status_code, 422)
        self.assertEqual(self.row(j)["priority"], 4)
        p = self.job(qs="paused")
        self.assertEqual(self.post(f"/jobs/{p}/priority", {"confirm": "yes", "dir": "up"}).status_code, 303)
        for qs, st in (("running", "building"), ("held_human", "topic"), ("finished", "failed")):
            x = self.job(state=st, qs=qs, priority=3)
            with self.subTest(qs=qs):
                self.assertEqual(self.post(f"/jobs/{x}/priority", {"confirm": "yes", "dir": "up"}).status_code, 409)
                self.assertEqual(self.row(x)["priority"], 3)

    def test_priority_concurrent_exact(self):
        for direction, delta in (("up", 2), ("down", -2)):
            j = self.job(qs="waiting", priority=10)
            clients = [TestClient(self.app, base_url=BASE, client=("127.0.0.1", 50000)) for _ in range(2)]
            for c in clients:
                c.get("/queue")
            out = []

            def go(c, j=j, direction=direction):
                r = c.post(f"/jobs/{j}/priority", data={"csrf_token": c.cookies.get("csrf"), "confirm": "yes",
                           "dir": direction}, headers={"origin": BASE}, follow_redirects=False)
                out.append(r.status_code)

            ts = [threading.Thread(target=go, args=(c,)) for c in clients]
            [t.start() for t in ts]
            [t.join(30) for t in ts]
            [c.close() for c in clients]
            with self.subTest(direction=direction):
                self.assertEqual(out, [303, 303])
                self.assertEqual(self.row(j)["priority"], 10 + delta)

    def test_waiting_order_follows_priority(self):
        a = self.job(qs="waiting", src=1)
        b = self.job(qs="waiting", src=2)
        self.post(f"/jobs/{b}/priority", {"confirm": "yes", "dir": "up"})
        html = self.client.get("/queue?state=wait").text
        self.assertLess(html.index(f'id="job-{b}"'), html.index(f'id="job-{a}"'))

    # ---- R20 ----
    def test_retry_with_hash_goes_to_building_waiting(self):
        j = self.job(state="failed", qs="finished", plan_hash="h" * 64)
        self.assertEqual(self.post(f"/jobs/{j}/retry", {}).status_code, 422)
        self.assertEqual(self.post(f"/jobs/{j}/retry", {"confirm": "yes"}).status_code, 303)
        row = self.row(j)
        self.assertEqual((row["state"], row["queue_state"]), ("building", "waiting"))
        self.assertEqual(self.post(f"/jobs/{j}/retry", {"confirm": "yes"}).status_code, 409)

    def test_retry_without_hash_goes_to_topic_held_human(self):
        j = self.job(state="failed", qs="finished")
        self.assertEqual(self.post(f"/jobs/{j}/retry", {"confirm": "yes"}).status_code, 303)
        row = self.row(j)
        self.assertEqual((row["state"], row["queue_state"]), ("topic", "held_human"))
        self.assertTrue(row["hold_reason"])

    def test_retry_409_by_state_and_404(self):
        for st, qs in (("building", "waiting"), ("canceled", "finished"), ("done", "finished"), ("review", "held_human")):
            j = self.job(state=st, qs=qs, plan_hash="h" * 64)
            with self.subTest(state=st):
                self.assertEqual(self.post(f"/jobs/{j}/retry", {"confirm": "yes"}).status_code, 409)
                self.assertEqual(self.row(j)["state"], st)
        self.assertEqual(self.post("/jobs/9999/retry", {"confirm": "yes"}).status_code, 404)

    def test_retry_plan_path_conflict_409(self):
        self.job(state="building", qs="waiting", plan_path="p/컷표.json")
        j = self.job(state="failed", qs="finished", plan_path="p/컷표.json", plan_hash="h" * 64)
        self.assertEqual(self.post(f"/jobs/{j}/retry", {"confirm": "yes"}).status_code, 409)
        self.assertEqual(self.row(j)["state"], "failed")

    # ---- 리뷰 반영: R14 next · R20 재시도 ----
    def test_cancel_next_allowlist(self):
        for nxt, want in (("queue", "/queue"), ("status", "/status"), (None, "/status"), ("", "/status"),
                          ("https://evil.example/", "/status"), ("//evil.example", "/status"), ("/queue", "/status")):
            j = self.job(qs="waiting")
            with self.subTest(next=nxt):
                data = {"confirm": "yes"}
                if nxt is not None:
                    data["next"] = nxt
                r = self.post(f"/jobs/{j}/cancel", data)
                self.assertEqual((r.status_code, r.headers["location"]), (303, want))

    def test_queue_cancel_form_sends_next_and_error_back_link(self):
        self.job(qs="waiting")
        html = self.client.get("/queue").text
        self.assertRegex(html, r'name="next" value="queue"')
        d = self.job(state="done", qs="finished")
        r = self.post(f"/jobs/{d}/cancel", {"confirm": "yes"})
        self.assertEqual(r.status_code, 409)  # decisions 쪽 기본 뒤로가기는 제작 현황
        self.assertIn('href="/status"', r.text)
        r = self.post(f"/jobs/{d}/retry", {"confirm": "yes"})
        self.assertEqual(r.status_code, 409)
        self.assertIn('href="/queue"', r.text)
        self.assertIn("대기 큐", r.text)

    def test_retry_clears_run_columns(self):
        j = self.job(state="failed", qs="finished", plan_hash="h" * 64, hold="이전 사유")
        self.conn.execute("UPDATE jobs SET slot_id='gpu0', started_at='2026-01-01T00:00:00.000Z', "
                          "finished_at='2026-01-01T00:01:00.000Z' WHERE id=?", (j,))
        self.conn.commit()
        self.assertEqual(self.post(f"/jobs/{j}/retry", {"confirm": "yes"}).status_code, 303)
        row = self.row(j)
        self.assertEqual((row["state"], row["queue_state"]), ("building", "waiting"))
        for col in ("slot_id", "started_at", "finished_at", "hold_reason"):
            self.assertIsNone(row[col], col)
        self.assertEqual(self.events(j), 1)

    def ai_job(self, rights):
        sid = self.conn.execute("INSERT INTO sources(work_id, episode, file_path, origin, rights_status) "
                                "VALUES (1, 9, 'p/원본.mp4', 'local_file', ?)", (rights,)).lastrowid
        d = self.dir / "p"
        d.mkdir(exist_ok=True)
        (d / "원본.mp4").write_bytes(b"x")
        (d / "컷표.json").write_text('{"meta": {"source_video": "원본.mp4"}, "cuts": []}', encoding="utf-8")
        j = self.job(state="failed", qs="finished", src=sid, plan_hash="h" * 64, plan_path="p/컷표.json")
        self.conn.execute("UPDATE jobs SET exec_mode='ai_assistant' WHERE id=?", (j,))
        self.conn.commit()
        return j

    def test_retry_ai_assistant_needs_rights(self):
        j = self.ai_job("unverified")
        r = self.post(f"/jobs/{j}/retry", {"confirm": "yes"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.row(j)["state"], "failed")
        self.conn.execute("UPDATE sources SET rights_status='own' WHERE file_path='p/원본.mp4'")
        self.conn.commit()
        self.assertEqual(self.post(f"/jobs/{j}/retry", {"confirm": "yes"}).status_code, 303)
        self.assertEqual(self.row(j)["state"], "building")

    def test_retry_ai_assistant_missing_plan_409(self):
        j = self.ai_job("own")
        (self.dir / "p" / "컷표.json").unlink()
        self.assertEqual(self.post(f"/jobs/{j}/retry", {"confirm": "yes"}).status_code, 409)
        self.assertEqual(self.row(j)["state"], "failed")

    # ---- R14+ ----
    def test_cancel_running_to_canceled_finished(self):
        j = self.job(qs="running")
        self.assertEqual(self.post(f"/jobs/{j}/cancel", {}).status_code, 422)
        self.assertEqual(self.post(f"/jobs/{j}/cancel", {"confirm": "yes"}).status_code, 303)
        row = self.row(j)
        self.assertEqual((row["state"], row["queue_state"]), ("canceled", "finished"))
        self.assertEqual(self.post(f"/jobs/{j}/cancel", {"confirm": "yes"}).status_code, 409)
        d = self.job(state="done", qs="finished")
        self.assertEqual(self.post(f"/jobs/{d}/cancel", {"confirm": "yes"}).status_code, 409)

    # ---- CSRF / Origin ----
    def test_post_csrf_and_origin_403(self):
        j = self.job(qs="waiting", priority=1)
        f = self.job(state="failed", qs="finished", plan_hash="h" * 64)
        for path in (f"/jobs/{j}/pause", f"/jobs/{j}/resume", f"/jobs/{j}/priority", f"/jobs/{f}/retry"):
            with self.subTest(path=path):
                data = {"confirm": "yes", "dir": "up"}
                self.assertEqual(self.post(path, data, csrf=False).status_code, 403)
                self.assertEqual(self.post(path, data, origin=False).status_code, 403)
        self.assertEqual((self.row(j)["queue_state"], self.row(j)["priority"]), ("waiting", 1))
        self.assertEqual(self.row(f)["state"], "failed")


if __name__ == "__main__":
    unittest.main()
