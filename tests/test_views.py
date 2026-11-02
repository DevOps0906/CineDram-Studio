"""server/views.py — 제작 현황·승인함 GET 화면(200·빈 상태·오류·XSS 이스케이프·읽기 전용).

자체 FastAPI 앱 + LocalSecurityMiddleware 로 감싸 실제 요청 경로와 같게 검사한다.
venv(requirements-dev.txt)에서 실행. fastapi·httpx·jinja2가 없으면 skip.
"""
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import migrate as migrate_mod  # noqa: E402

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

    from server import db as server_db
    from server.app import PORT
    from server.security import new_server_secret
    from server.middleware import LocalSecurityMiddleware
    from server.views import get_db_path, router
except ImportError:  # 표준 라이브러리만 있는 환경
    TestClient = None

XSS = "<script>alert(1)</script>"


@unittest.skipIf(TestClient is None, "fastapi 미설치")
class ViewsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="화면_테스트_"))
        self.db = self.tmp / "운영.sqlite3"
        migrate_mod.migrate(self.db, backup=False)
        self.client = self.make_client(self.db)

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_client(self, db_path):
        app = FastAPI(debug=False, docs_url=None, redoc_url=None, openapi_url=None)
        app.state.csrf_secret = new_server_secret()
        app.add_middleware(LocalSecurityMiddleware, port=PORT, csrf_secret=app.state.csrf_secret)
        app.include_router(router)
        app.dependency_overrides[get_db_path] = lambda: str(db_path) if db_path else None
        return TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 50000))

    def seed(self, rows):
        conn = migrate_mod.connect(self.db)
        try:
            for sql, args in rows:
                conn.execute(sql, args)
        finally:
            conn.close()

    def add_job(self, state, rights="own", title="제목", work="달빛 수선소", rule_set='["drama"]', channel=None):
        self.seed([
            ("INSERT INTO works(title) VALUES (?)", (work,)),
            ("INSERT INTO sources(work_id, episode, file_path, origin, rights_status)"
             " VALUES ((SELECT MAX(id) FROM works), 4, 'a.mp4', 'local_file', ?)", (rights,)),
            ("INSERT INTO jobs(source_id, state, rule_set, channel_id) VALUES ((SELECT MAX(id) FROM sources), ?, ?, ?)",
             (state, rule_set, channel)),
            ("INSERT INTO topics(job_id, title, used) VALUES ((SELECT MAX(id) FROM jobs), ?, 1)", (title,)),
            ("INSERT INTO job_events(job_id, to_state, message, render_count)"
             " VALUES ((SELECT MAX(id) FROM jobs), ?, ?, 2)", (state, "단계 기록")),
        ])


class StatusPageTest(ViewsTestBase):
    def test_plan_form_only_for_plan_hold(self):
        self.add_job("queued", title="대기 작업")
        self.add_job("queued", title="다른 작업")
        self.seed([("UPDATE jobs SET queue_state = 'held_human', hold_reason = '컷표 작성 대기(사람·스킬)'"
                    " WHERE id = 1", ()),
                   ("UPDATE jobs SET queue_state = 'waiting' WHERE id = 2", ())])
        html = self.client.get("/status?job=1").text
        self.assertIn('action="/queue/1/plan"', html)
        self.assertIn('href="?job=1#plan-form"', html)
        self.assertIn('name="back" value="status"', html)
        form = html[html.index('action="/queue/1/plan"'):]
        form = form[:form.index("</form>")]
        self.assertIn('name="csrf_token"', form)
        self.assertEqual(re.findall(r'<option value="([^"]+)"', form), ["check", "recalc", "render"])
        self.assertNotIn("approve", form)
        # 폼은 3초 갱신 영역(#jobs) 밖(상세 패널)에만 있다
        self.assertEqual(html.count('action="/queue/1/plan"'), 1)
        self.assertGreater(html.index('action="/queue/1/plan"'), html.index('class="aside-head"'))
        html2 = self.client.get("/status?job=2").text
        self.assertNotIn("/queue/2/plan", html2)
        self.assertNotIn("?job=2#plan-form", html2)

    def test_empty(self):
        r = self.client.get("/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("진행 중인 제작이 없습니다", r.text)
        self.assertRegex(r.text, r'<meta name="csrf-token" content="[^"]+">')  # 미들웨어 state 토큰 연결

    def test_groups_badges_progress(self):
        self.add_job("building")
        self.add_job("need_source", work="서쪽 정류장")
        self.add_job("review", rights="unverified", work="파도 위 도서관")
        r = self.client.get("/status")
        self.assertEqual(r.status_code, 200)
        self.assertIn("달빛 수선소 · E04", r.text)
        self.assertIn("한드짜", r.text)  # rules/drama.md 첫 제목에서 읽은 구성 이름
        self.assertIn("원본 없음", r.text)
        self.assertIn("권리 미확인", r.text)
        self.assertIn("업로드 경로 닫힘 (R9-1)", r.text)
        self.assertIn('aria-current="step"', r.text)
        self.assertIn("진행 중 <span class=\"count\">2</span>", r.text)

    def test_done_and_canceled_hidden(self):
        self.add_job("done", work="끝난 작품")
        self.add_job("canceled", work="취소 작품")
        r = self.client.get("/status")
        self.assertNotIn("끝난 작품", r.text)
        self.assertNotIn("취소 작품", r.text)
        self.assertIn("진행 중인 제작이 없습니다", r.text)

    def test_xss_escaped(self):
        self.add_job("building", title=XSS, work=XSS, rule_set=f'["{XSS}"]')
        r = self.client.get("/status")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(XSS, r.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", r.text)

    def test_missing_db_error_state(self):
        for path in (None, self.tmp / "없음.sqlite3"):
            with self.make_client(path) as c:
                r = c.get("/status")
            self.assertEqual(r.status_code, 503)
            self.assertIn("현황을 불러오지 못했습니다", r.text)
            self.assertNotIn("없음.sqlite3", r.text)

    def test_non_loopback_rejected(self):
        app = self.client.app
        with TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=("10.0.0.5", 50000)) as c:
            self.assertEqual(c.get("/status").status_code, 403)


    def test_no_source_treated_as_unverified(self):
        self.seed([("INSERT INTO jobs(state, rule_set) VALUES ('review', '[]')", ())])
        r = self.client.get("/status")
        self.assertIn("작업 #1", r.text)
        self.assertIn("권리 미확인", r.text)
        self.assertIn("업로드 경로 닫힘 (R9-1)", r.text)
        self.assertNotIn("제작 가능", r.text)

    def test_picked_card_has_inline_detail_and_poll_parts(self):
        self.add_job("building")
        self.assertNotIn("narrow-detail", self.client.get("/status").text)
        r = self.client.get("/status?job=1")
        self.assertIn('id="job-1"', r.text)
        self.assertIn("narrow-detail", r.text)
        for part in ("pills", "aside", "nav-status", "nav-inbox", "tab-inbox"):
            self.assertIn(f'data-poll-part="{part}"', r.text)
        self.assertIn('aria-controls="side-panel"', r.text)
        self.assertIn("localStorage.getItem('cd-theme')", r.text)  # 인라인 테마 스크립트가 미들웨어를 통과


class InboxPageTest(ViewsTestBase):
    def test_empty(self):
        r = self.client.get("/inbox")
        self.assertEqual(r.status_code, 200)
        self.assertIn("승인 대기 카드가 없습니다", r.text)
        r = self.client.get("/inbox?tab=approved")
        self.assertIn("승인한 카드가 없습니다", r.text)

    def test_pending_rights_and_approved(self):
        self.add_job("review", rights="unverified", title="반납함의 책", work="파도 위 도서관")
        self.add_job("approved", title="회의실 메모", work="유리정원 사무소", channel="UCabcdefk3x7Qa")
        self.seed([("INSERT INTO job_events(job_id, from_state, to_state, message)"
                    " VALUES (2, 'review', 'approved', '사람 영상 승인 · 채널 k3x7Qa · 요청 0123abcd')", ())])
        r = self.client.get("/inbox")
        self.assertEqual(r.status_code, 200)
        self.assertIn("반납함의 책", r.text)
        self.assertIn("(R9-1)", r.text)
        self.assertNotIn("회의실 메모", r.text)
        r = self.client.get("/inbox?tab=approved")
        self.assertIn("회의실 메모", r.text)
        self.assertIn("영상 승인: 사용자", r.text)
        self.assertIn("…k3x7Qa", r.text)
        self.assertNotIn("UCabcdefk3x7Qa", r.text)

    def test_no_source_unverified_in_inbox(self):
        self.seed([("INSERT INTO jobs(state, rule_set) VALUES ('review', '[]')", ())])
        r = self.client.get("/inbox?job=1")
        self.assertIn("권리 미확인", r.text)
        self.assertIn("(R9-1)", r.text)
        self.assertIn("narrow-detail", r.text)

    def test_bad_tab_falls_back(self):
        r = self.client.get("/inbox?tab=<x>")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("<x>", r.text)

    def test_xss_escaped(self):
        self.add_job("review", title=XSS, work=XSS)
        r = self.client.get("/inbox")
        self.assertNotIn(XSS, r.text)
        self.assertIn("&lt;script&gt;", r.text)

    def test_no_post_on_list(self):
        r = self.client.post("/inbox", headers={"Origin": f"http://127.0.0.1:{PORT}"})
        self.assertEqual(r.status_code, 405)

    def test_missing_db_error_state(self):
        with self.make_client(None) as c:
            r = c.get("/inbox")
        self.assertEqual(r.status_code, 503)
        self.assertIn("승인함을 불러오지 못했습니다", r.text)


ORIGIN = {"Origin": f"http://127.0.0.1:{PORT}"}


class ApproveTest(ViewsTestBase):
    def token(self):
        r = self.client.get("/inbox")
        return re.search(r'<meta name="csrf-token" content="([^"]+)">', r.text).group(1)

    def approve(self, job_id, token=None):
        data = {"csrf_token": token if token is not None else self.token()}
        return self.client.post(f"/inbox/{job_id}/approve", data=data, headers=ORIGIN, follow_redirects=False)

    def row(self, sql, args=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, args).fetchone()
        finally:
            conn.close()

    def test_success(self):
        self.add_job("review", channel="UCabcdefk3x7Qa")
        self.assertIn('action="/inbox/1/approve"', self.client.get("/inbox").text)
        with mock.patch("subprocess.Popen") as popen, mock.patch("subprocess.run") as run:
            r = self.approve(1)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/inbox")
        popen.assert_not_called()  # 엔진 approve(기획승인.json) 서브프로세스를 부르지 않는다
        run.assert_not_called()
        self.assertEqual(self.row("SELECT state FROM jobs WHERE id = 1")[0], "approved")
        # approvals 는 기획 승인 사본: 영상 승인은 넣지 않는다
        self.assertEqual(self.row("SELECT COUNT(*) FROM approvals")[0], 0)
        self.assertIn("영상 승인: 사용자", self.client.get("/inbox?tab=approved").text)
        msg = self.row("SELECT message FROM job_events WHERE job_id = 1 AND to_state = 'approved'")[0]
        self.assertRegex(msg, r"^사람 영상 승인 · 채널 k3x7Qa · 요청 [0-9a-f]{8}$")
        self.assertNotIn("UCabcdefk3x7Qa", msg)

    def test_success_finishes_queue(self):
        # 렌더 성공 뒤 승인함 대기(held_human/review) → 승인하면 큐에서 빠진다(워커가 다시 집지 않게)
        self.add_job("review")
        self.seed([("UPDATE jobs SET queue_state = 'held_human', hold_reason = '승인함 영상 확인 대기',"
                    " engine_cmd = 'render', plan_path = 'x.json' WHERE id = 1", ())])
        self.assertEqual(self.approve(1).status_code, 303)
        self.assertEqual(self.row("SELECT state, queue_state, hold_reason, slot_id FROM jobs WHERE id = 1"),
                         ("approved", "finished", None, None))

    def test_rights_unverified_rejected(self):
        self.add_job("review", rights="unverified")
        self.seed([("INSERT INTO jobs(state, rule_set) VALUES ('review', '[]')", ())])  # 소스 없음(NULL)
        self.assertNotIn("/approve", self.client.get("/inbox").text)
        for job_id in (1, 2):
            r = self.approve(job_id)
            self.assertEqual(r.status_code, 409)
            self.assertIn("(R9-1)", r.text)
            self.assertEqual(self.row("SELECT state FROM jobs WHERE id = ?", (job_id,))[0], "review")
        self.assertEqual(self.row("SELECT COUNT(*) FROM approvals")[0], 0)

    def test_second_post_conflicts(self):
        self.add_job("review")
        self.assertEqual(self.approve(1).status_code, 303)
        r = self.approve(1)
        self.assertEqual(r.status_code, 409)
        self.assertIn("이미 처리됐거나", r.text)
        self.assertEqual(self.row("SELECT COUNT(*) FROM approvals")[0], 0)
        self.assertEqual(self.row("SELECT COUNT(*) FROM job_events WHERE to_state = 'approved'")[0], 1)

    def test_running_rejected(self):
        self.add_job("review")
        self.seed([("UPDATE jobs SET queue_state = 'running' WHERE id = 1", ())])
        self.assertEqual(self.approve(1).status_code, 409)
        self.assertEqual(self.row("SELECT state FROM jobs WHERE id = 1")[0], "review")
        self.assertEqual(self.row("SELECT COUNT(*) FROM approvals")[0], 0)

    def test_missing_token_forbidden(self):
        self.add_job("review")
        self.client.get("/inbox")  # 쿠키는 있으나 제출 토큰 없음
        r = self.client.post("/inbox/1/approve", data={}, headers=ORIGIN, follow_redirects=False)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.approve(1, token="위조").status_code, 403)
        self.assertEqual(self.row("SELECT state FROM jobs WHERE id = 1")[0], "review")

    def test_unknown_job(self):
        self.add_job("review")
        r = self.approve(99)
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("narrow-detail", r.text)  # 거부 응답은 카드를 고르지 않는다(job=None)

    def test_event_insert_failure_rolls_back(self):
        self.add_job("review")
        self.seed([("CREATE TRIGGER fail_approve BEFORE INSERT ON job_events WHEN NEW.to_state = 'approved'"
                    " BEGIN SELECT RAISE(ABORT, '주입된 실패'); END", ())])
        r = self.approve(1)
        self.assertEqual(r.status_code, 503)
        self.assertIn("승인을 저장하지 못했습니다", r.text)
        self.assertNotIn("주입된 실패", r.text)
        self.assertEqual(self.row("SELECT state FROM jobs WHERE id = 1")[0], "review")  # UPDATE 도 되돌려짐

    def test_db_unavailable_503(self):
        self.client.close()
        self.client = self.make_client(None)
        r = self.approve(1)
        self.assertEqual(r.status_code, 503)
        self.assertIn("승인을 저장하지 못했습니다", r.text)


class StaticAndReadOnlyTest(ViewsTestBase):
    def test_static_whitelist(self):
        for name in ("_tokens.css", "_app.css", "app.css", "app.js"):
            r = self.client.get(f"/static/{name}")
            self.assertEqual(r.status_code, 200, name)
        self.assertEqual(self.client.get("/static/views.py").status_code, 404)
        self.assertEqual(self.client.get("/static/..%2Fviews.py").status_code, 404)

    def test_connection_is_read_only(self):
        conn = server_db.connect_ro(str(self.db))
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO works(title) VALUES ('x')")
        finally:
            conn.close()


    def test_connection_settings_match_migrate(self):
        for connect in (server_db.connect_ro, server_db.connect_rw):
            conn = connect(str(self.db))
            try:
                self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], migrate_mod.BUSY_TIMEOUT_MS)
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            finally:
                conn.close()
        conn = server_db.connect_rw(str(self.db))
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO sources(work_id, file_path, origin) VALUES (999, 'a.mp4', 'local_file')")
        finally:
            conn.close()
        with self.assertRaises(server_db.DBUnavailable):
            server_db.connect_rw(str(self.tmp / "없음.sqlite3"))
        self.assertFalse((self.tmp / "없음.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
