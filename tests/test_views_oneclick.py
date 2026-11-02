"""server/views_oneclick.py — 원클릭 제작 GET·POST /make/oneclick(성공 303·관문 자동 통과 없음·권리 미선택 거부·CSRF 403·XSS).

자체 FastAPI 앱 + LocalSecurityMiddleware 로 감싸 실제 요청 경로와 같게 검사한다.
venv(requirements-dev.txt)에서 실행. fastapi·httpx·jinja2가 없으면 skip.
"""
import json
import re
import shutil
import sqlite3
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
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

    from server.app import PORT
    from server.middleware import LocalSecurityMiddleware
    from server.security import new_server_secret
    from server.views import get_db_path
    from server.views import router as views_router
    from server.views_create import get_roots
    from server.views_oneclick import router
except ImportError:  # 표준 라이브러리만 있는 환경
    TestClient = None

XSS = "<script>alert(1)</script>"
ORIGIN = {"Origin": "http://127.0.0.1:8765"}
TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@unittest.skipIf(TestClient is None, "fastapi 미설치")
class OneclickTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="원클릭_테스트_"))
        self.db = self.tmp / "운영.sqlite3"
        migrate_mod.migrate(self.db, backup=False)
        self.root = self.tmp / "작업"
        (self.root / "원본").mkdir(parents=True)
        (self.root / "원본" / "e04.mp4").write_bytes(b"\x00")
        (self.root / "원본" / "e04.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n대사\n", encoding="utf-8")
        self.roots = [str(self.root)]
        self.client = self.make_client()

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_client(self):
        app = FastAPI(debug=False, docs_url=None, redoc_url=None, openapi_url=None)
        app.state.csrf_secret = new_server_secret()
        app.add_middleware(LocalSecurityMiddleware, port=PORT, csrf_secret=app.state.csrf_secret)
        app.include_router(views_router)
        app.include_router(router)
        app.dependency_overrides[get_db_path] = lambda: str(self.db)
        app.dependency_overrides[get_roots] = lambda: self.roots
        return TestClient(app, base_url=f"http://127.0.0.1:{PORT}", client=("127.0.0.1", 50000),
                          follow_redirects=False)

    def token(self):
        r = self.client.get("/make/oneclick")
        self.assertEqual(r.status_code, 200)
        return TOKEN_RE.search(r.text).group(1)

    def form(self, **over):
        data = {"comp": "drama", "work_title": "달빛 수선소", "episode": "4", "video_path": "원본/e04.mp4",
                "srt_path": "원본/e04.srt", "rights_status": "own", "exec_mode": "auto_render"}
        data.update(over)
        return {k: v for k, v in data.items() if v is not None}

    def post(self, with_token=True, **over):
        data = self.form(**over)
        if with_token:
            data["csrf_token"] = self.token()
        return self.client.post("/make/oneclick", data=data, headers=ORIGIN)

    def rows(self, sql):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql)]
        finally:
            conn.close()


class OneclickGetTest(OneclickTestBase):
    def test_form_renders(self):
        r = self.client.get("/make/oneclick")
        self.assertEqual(r.status_code, 200)
        self.assertIn('name="csrf_token"', r.text)
        self.assertIn('value="drama"', r.text)
        self.assertRegex(r.text, r'value="indie" disabled')  # 미확정 규칙은 고를 수 없다
        for v in ("auto_render", "manual", "ai_assistant"):
            self.assertIn(f'name="exec_mode" value="{v}"', r.text)
        self.assertRegex(r.text, r'value="auto_render" checked')  # 기본 = 자동제작(§16-1)
        for v in ("own", "licensed", "unverified"):
            self.assertIn(f'name="rights_status" value="{v}">', r.text)  # 권리 기본 선택 없음
        self.assertIn("사람·스킬 입력 대기", r.text)
        self.assertIn("검수 후 업로드", r.text)

    def test_missing_roots_blocks_submit(self):
        self.roots = []
        r = self.client.get("/make/oneclick")
        self.assertIn("CINEDRAM_ROOTS", r.text)
        self.assertRegex(r.text, r'type="submit" disabled')


class OneclickPostTest(OneclickTestBase):
    def test_success_creates_held_job(self):
        r = self.post(exec_mode="manual")
        self.assertEqual(r.status_code, 303)
        job = self.rows("SELECT * FROM jobs")
        self.assertEqual(len(job), 1)
        self.assertIn(f"/status?job={job[0]['id']}", r.headers["location"])
        self.assertEqual(job[0]["exec_mode"], "manual")
        self.assertEqual(json.loads(job[0]["rule_set"]), ["common", "drama"])
        self.assertEqual(self.rows("SELECT rights_status FROM sources")[0]["rights_status"], "own")
        msgs = [e["message"] for e in self.rows("SELECT message FROM job_events ORDER BY id")]
        self.assertEqual(len(msgs), 1)  # 같은 트랜잭션의 첫 이벤트 하나
        self.assertIn("원클릭 제작 · 모드 manual", msgs[0])

    def test_no_gate_auto_pass(self):
        self.post()
        job = self.rows("SELECT * FROM jobs")[0]
        self.assertEqual(job["state"], "queued")
        self.assertEqual(job["queue_state"], "held_human")  # 워커가 집지 않는다
        self.assertIsNone(job["plan_hash"])  # 기획 승인 없음
        self.assertIsNone(job["tts_hash"])
        self.assertEqual(job["upload_mode"], "review_first")
        self.assertEqual(self.rows("SELECT * FROM topics"), [])  # 소재(관문 ①) 자동 선정 없음
        states = {e["to_state"] for e in self.rows("SELECT to_state FROM job_events")}
        self.assertEqual(states, {"queued"})

    def test_rights_not_selected_rejected(self):
        for bad in (None, "unknown"):
            with self.subTest(bad=bad):
                r = self.post(rights_status=bad, exec_mode="manual")
                self.assertEqual(r.status_code, 422)
                self.assertIn("권리 상태를 고르세요", r.text)
                self.assertIn('value="달빛 수선소"', r.text)  # 입력값 보존
                self.assertRegex(r.text, r'value="manual" checked')
        self.assertEqual(self.rows("SELECT * FROM jobs"), [])

    def test_bad_mode_and_composition_rejected(self):
        r = self.post(exec_mode="upload_now")
        self.assertEqual(r.status_code, 422)
        self.assertIn("제작 모드를 고르세요", r.text)
        r = self.post(comp="indie")
        self.assertEqual(r.status_code, 422)
        self.assertIn("구성을 고르세요", r.text)
        self.assertEqual(self.rows("SELECT * FROM jobs"), [])

    def test_unverified_allowed_but_held(self):
        self.assertEqual(self.post(rights_status="unverified").status_code, 303)
        self.assertEqual(self.rows("SELECT queue_state FROM jobs")[0]["queue_state"], "held_human")

    def test_duplicate_same_mode_redirects(self):
        first = self.post(exec_mode="manual")
        again = self.post(exec_mode="manual")
        self.assertEqual(again.status_code, 303)
        self.assertEqual(again.headers["location"], first.headers["location"])
        self.assertEqual(len(self.rows("SELECT * FROM jobs")), 1)

    def test_duplicate_other_mode_409(self):
        self.post(exec_mode="manual")
        job_id = self.rows("SELECT id FROM jobs")[0]["id"]
        r = self.post(exec_mode="ai_assistant")
        self.assertEqual(r.status_code, 409)
        self.assertIn(f"같은 원본의 작업 #{job_id}이 이미 있어 제작 모드를 바꾸지 않았습니다(현재 캡컷으로 편집)", r.text)
        self.assertIn(f'href="/status?job={job_id}"', r.text)
        self.assertRegex(r.text, r'value="ai_assistant" checked')  # 입력값 보존
        job = self.rows("SELECT * FROM jobs")
        self.assertEqual(len(job), 1)
        self.assertEqual(job[0]["exec_mode"], "manual")
        self.assertEqual(len(self.rows("SELECT * FROM job_events")), 1)

    def test_recent_newest_first(self):
        self.post(work_title="가 먼저 만든 작품")
        self.post(work_title="나중 만든 작품")
        text = self.client.get("/make/oneclick").text.split("지금 제작 상황", 1)[1]  # 사이드 패널만(datalist 제외)
        self.assertLess(text.index("나중 만든 작품"), text.index("가 먼저 만든 작품"))

    def test_no_token_403(self):
        self.client.get("/make/oneclick")
        self.assertEqual(self.post(with_token=False).status_code, 403)
        r = self.client.post("/make/oneclick", data={**self.form(), "csrf_token": "x" * 40}, headers=ORIGIN)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.rows("SELECT * FROM jobs"), [])

    def test_xss_escaped(self):
        r = self.post(work_title=XSS, video_path=XSS, rights_status=None)
        self.assertEqual(r.status_code, 422)
        self.assertNotIn(XSS, r.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", r.text)
        self.post(work_title=XSS)  # 저장된 작품명은 datalist·최근 기록에서 이스케이프
        self.assertNotIn(XSS, self.client.get("/make/oneclick").text)

    def test_db_missing_503(self):
        data = {**self.form(), "csrf_token": self.token()}
        self.db.unlink()
        r = self.client.post("/make/oneclick", data=data, headers=ORIGIN)
        self.assertEqual(r.status_code, 503)


if __name__ == "__main__":
    unittest.main()
