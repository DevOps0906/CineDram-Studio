"""server/views_queue.py — 대기 큐 화면 GET /queue 와 사람 조작 POST(docs/14 §2 S-QU1·§4).

실물 create_app 에 router 를 붙이고, 화면에서 받은 CSRF 토큰(쿠키 + hidden)으로 조작한다.
venv(requirements-dev.txt)에서 실행. fastapi·httpx·jinja2가 없으면 skip.
"""
import re
import shutil
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import migrate as migrate_mod  # noqa: E402

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.routing import APIRoute
        from fastapi.testclient import TestClient

    from server.app import PORT, create_app
    from server.csrf import CSRF_FAILED, require_csrf
    from server.views import ICONS, get_db_path
    from server.views_queue import REJECTED, NOT_FOUND, BAD_MOVE, router
except ImportError:  # 표준 라이브러리만 있는 환경
    TestClient = None

BASE = f"http://127.0.0.1:{PORT}"
XSS = "<script>alert(1)</script>"


@unittest.skipIf(TestClient is None, "fastapi 미설치")
class QueueViewTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="큐_화면_"))
        self.db = self.tmp / "운영.sqlite3"
        migrate_mod.migrate(self.db, backup=False)
        self.client = self.make_client(str(self.db))

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_client(self, db_path):
        app = create_app()
        app.include_router(router)
        app.dependency_overrides[get_db_path] = lambda: db_path
        return TestClient(app, base_url=BASE, client=("127.0.0.1", 50000), headers={"Origin": BASE},
                          follow_redirects=False)

    def add_job(self, queue_state="waiting", state="queued", priority=0, work="달빛 수선소", slot_kind="gpu",
                hold_reason=None, error=None, rights="own"):
        conn = migrate_mod.connect(self.db)
        try:
            conn.execute("INSERT INTO works(title) VALUES (?)", (work,))
            conn.execute("INSERT INTO sources(work_id, episode, file_path, origin, rights_status)"
                         " VALUES ((SELECT MAX(id) FROM works), 4, 'a.mp4', 'local_file', ?)", (rights,))
            cur = conn.execute(
                "INSERT INTO jobs(source_id, state, queue_state, priority, slot_kind, hold_reason, rule_set)"
                " VALUES ((SELECT MAX(id) FROM sources), ?, ?, ?, ?, ?, '[\"drama\"]')",
                (state, queue_state, priority, slot_kind, hold_reason))
            job_id = cur.lastrowid
            if error:
                conn.execute("INSERT INTO job_events(job_id, from_state, to_state, message, error)"
                             " VALUES (?, 'running', 'finished', '종료', ?)", (job_id, error))
            return job_id
        finally:
            conn.close()

    def row(self, job_id):
        conn = migrate_mod.connect(self.db)
        try:
            return conn.execute("SELECT queue_state, state, priority FROM jobs WHERE id=?", (job_id,)).fetchone()
        finally:
            conn.close()

    def token(self):
        r = self.client.get("/queue")
        m = re.search(r'name="csrf_token" value="([^"]+)"', r.text) or \
            re.search(r'<meta name="csrf-token" content="([^"]+)">', r.text)
        self.assertIsNotNone(m)
        return m.group(1)

    def post(self, job_id, op, token=True, **data):
        if token:
            data["csrf_token"] = self.token()
        return self.client.post(f"/queue/{job_id}/{op}", data=data)

    def assertDone(self, r):
        self.assertEqual(r.status_code, 303, r.text[:300])
        self.assertEqual(r.headers["location"], "/queue")


class QueuePageTest(QueueViewTestBase):
    def test_empty(self):
        r = self.client.get("/queue")
        self.assertEqual(r.status_code, 200)
        self.assertIn("대기 중인 작업이 없습니다", r.text)
        self.assertIn('<meta name="csrf-token" content="', r.text)

    def test_groups_slots_and_forms(self):
        self.add_job("running", "building", work="실행작품")
        self.add_job("waiting", work="대기작품")
        self.add_job("paused", work="정지작품")
        self.add_job("held_human", "review", work="검수작품", hold_reason="재승인 대기")
        self.add_job("held_resource", "blocked_disk", work="자원작품", hold_reason="디스크 부족")
        self.add_job("finished", "failed", work="실패작품", error="AI 응답 형식 오류\n자세한 로그")
        self.add_job("finished", "done", work="완료작품")
        r = self.client.get("/queue")
        self.assertEqual(r.status_code, 200)
        for text in ("실행 중", "대기", "사람 대기", "자원 대기", "실패작품", "AI 응답 형식 오류", "재승인 대기",
                     "디스크 부족", 'action="/queue/2/pause"', 'action="/queue/3/resume"',
                     'action="/queue/6/retry"', 'action="/queue/1/cancel"'):
            self.assertIn(text, r.text)
        self.assertNotIn("완료작품", r.text)  # 완료는 제작 현황으로
        self.assertNotIn("자세한 로그", r.text)  # 실패 사유는 첫 줄만
        self.assertRegex(r.text, r'class="score num">0/\?<')  # LLM 전체 수는 미확인
        self.assertRegex(r.text, r'class="score num">1/1<')  # GPU 1 고정, 1개 사용
        self.assertRegex(r.text, r'name="csrf_token" value="[^"]+"')

    def test_cancel_blocked_states_have_no_cancel_form(self):
        job = self.add_job("held_human", "uploading")
        r = self.client.get("/queue")
        self.assertNotIn(f'action="/queue/{job}/cancel"', r.text)

    def test_escapes_title(self):
        self.add_job(work=XSS)
        r = self.client.get("/queue")
        self.assertNotIn(XSS, r.text)
        self.assertIn("&lt;script&gt;", r.text)

    def test_db_missing_503(self):
        client = self.make_client(str(self.tmp / "없음.sqlite3"))
        try:
            r = client.get("/queue")
        finally:
            client.close()
        self.assertEqual(r.status_code, 503)
        self.assertIn("큐 상태를 불러오지 못했습니다", r.text)

    def test_new_icons_render_paths(self):
        self.add_job("running", "building")
        self.add_job("waiting")
        self.add_job("waiting")
        self.add_job("paused")
        self.add_job("held_resource", "blocked_disk")
        r = self.client.get("/queue")
        for name in ("up", "down", "top", "pause", "gauge", "loader"):
            with self.subTest(icon=name):
                path = ICONS[name]
                self.assertIn("<path", path)
                self.assertIn(f'viewBox="0 0 24 24" aria-hidden="true">{path}</svg>', r.text)

    def test_controls_group_labels_and_order(self):
        a = self.add_job(work="첫작품")
        self.add_job(work="둘작품")
        r = self.client.get("/queue")
        self.assertIn('role="group" aria-label="첫작품 · E04 조작"', r.text)
        self.assertIn('aria-disabled="true" disabled aria-label="첫작품 · E04 위로"', r.text)
        self.assertIn('aria-disabled="true" disabled aria-label="첫작품 · E04 맨 앞으로"', r.text)
        self.assertIn(f'href="/queue?job={a}#job-{a}"', r.text)
        block = r.text.split('aria-label="둘작품 · E04 조작"')[1].split("</div>")[0]
        pos = [block.index(f'aria-label="둘작품 · E04 {x}"') for x in ("위로", "아래로", "맨 앞으로")]
        self.assertEqual(pos, sorted(pos))

    def test_picked_job_inline_detail_for_mobile(self):
        self.add_job()
        b = self.add_job(work="고른작품")
        self.assertNotIn("narrow-detail", self.client.get("/queue").text)
        r = self.client.get(f"/queue?job={b}")
        part = r.text.split(f'id="job-{b}"')[1].split("</article>")[0]
        self.assertIn('class="only-mobile narrow-detail"', part)
        self.assertIn("대기 순번", part)

    def test_all_post_routes_require_csrf(self):
        posts = [r for r in router.routes if isinstance(r, APIRoute) and "POST" in r.methods]
        self.assertTrue(posts)
        for route in posts:
            self.assertTrue(any(d.call is require_csrf for d in route.dependant.dependencies), route.path)


class QueueActionTest(QueueViewTestBase):
    def test_pause_and_resume(self):
        job = self.add_job("waiting")
        self.assertDone(self.post(job, "pause"))
        self.assertEqual(self.row(job)[0], "paused")
        self.assertDone(self.post(job, "resume"))
        self.assertEqual(self.row(job)[0], "waiting")

    def test_pause_rejected_409(self):
        job = self.add_job("running", "building")
        r = self.post(job, "pause")
        self.assertEqual(r.status_code, 409)
        self.assertIn(REJECTED["pause"], r.text)
        self.assertEqual(self.row(job)[0], "running")

    def test_resume_rejected_409(self):
        job = self.add_job("waiting")
        r = self.post(job, "resume")
        self.assertEqual(r.status_code, 409)
        self.assertIn(REJECTED["resume"], r.text)

    def test_cancel_waiting_and_running(self):
        waiting = self.add_job("waiting")
        running = self.add_job("running", "building")
        self.assertDone(self.post(waiting, "cancel"))
        self.assertDone(self.post(running, "cancel"))
        self.assertEqual(tuple(self.row(waiting)[:2]), ("finished", "canceled"))
        self.assertEqual(tuple(self.row(running)[:2]), ("finished", "canceled"))

    def test_cancel_uploading_rejected_409(self):
        job = self.add_job("held_human", "uploading")
        r = self.post(job, "cancel")
        self.assertEqual(r.status_code, 409)
        self.assertIn(REJECTED["cancel"], r.text)
        self.assertEqual(tuple(self.row(job)[:2]), ("held_human", "uploading"))

    def test_retry(self):
        job = self.add_job("finished", "failed", error="실패")
        self.assertDone(self.post(job, "retry"))
        self.assertEqual(tuple(self.row(job)[:2]), ("waiting", "queued"))

    def test_retry_rejected_409(self):
        job = self.add_job("waiting")
        r = self.post(job, "retry")
        self.assertEqual(r.status_code, 409)
        self.assertIn(REJECTED["retry"], r.text)

    def order(self):
        conn = migrate_mod.connect(self.db)
        try:
            return [r[0] for r in conn.execute(
                "SELECT id FROM jobs WHERE queue_state IN ('waiting','paused') ORDER BY priority DESC, id")]
        finally:
            conn.close()

    def test_reorder_up_down_top_one_step(self):
        a, b, c = self.add_job(), self.add_job(), self.add_job()  # 동률 [A0,B0,C0]
        self.assertDone(self.post(c, "reorder", move="up"))
        self.assertEqual(self.order(), [a, c, b])  # 한 칸만
        self.assertDone(self.post(a, "reorder", move="down"))
        self.assertEqual(self.order(), [c, a, b])
        self.assertDone(self.post(b, "reorder", move="top"))
        self.assertEqual(self.order(), [b, c, a])

    def test_reorder_at_edge_no_change(self):
        a = self.add_job(priority=5)
        self.assertDone(self.post(a, "reorder", move="top"))
        self.assertEqual(self.row(a)[2], 5)

    def test_reorder_bad_move_400(self):
        a = self.add_job()
        r = self.post(a, "reorder", move="sideways")
        self.assertEqual(r.status_code, 400)
        self.assertIn(BAD_MOVE, r.text)

    def test_reorder_outside_wait_group_409(self):
        job = self.add_job("running", "building")
        r = self.post(job, "reorder", move="top")
        self.assertEqual(r.status_code, 409)
        self.assertIn(REJECTED["reorder"], r.text)

    def test_missing_job_404(self):
        for op, data in (("pause", {}), ("cancel", {}), ("retry", {}), ("resume", {}), ("reorder", {"move": "up"})):
            with self.subTest(op=op):
                r = self.post(999, op, **data)
                self.assertEqual(r.status_code, 404)
                self.assertIn(NOT_FOUND, r.text)

    def test_no_token_403(self):
        job = self.add_job("waiting")
        failed = self.add_job("finished", "failed")
        for target, op, data in ((job, "pause", {}), (job, "resume", {}), (job, "cancel", {}),
                                 (failed, "retry", {}), (job, "reorder", {"move": "top"})):
            with self.subTest(op=op):
                self.client.get("/queue")  # 쿠키는 있으나 제출 토큰 없음
                r = self.post(target, op, token=False, **data)
                self.assertEqual(r.status_code, 403)
                self.assertIn(CSRF_FAILED, r.text)
        self.assertEqual(tuple(self.row(job)), ("waiting", "queued", 0))
        self.assertEqual(tuple(self.row(failed)[:2]), ("finished", "failed"))

    def test_wrong_token_403(self):
        job = self.add_job("waiting")
        self.client.get("/queue")
        r = self.client.post(f"/queue/{job}/pause", data={"csrf_token": "x" * 40})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.row(job)[0], "waiting")


if __name__ == "__main__":
    unittest.main()
