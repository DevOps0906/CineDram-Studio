"""server/views_create.py — 만들기 GET·POST(성공 303·경로 탈출 거부·권리 미선택 거부·CSRF 403·XSS·값 보존).

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
    from server.views_create import get_roots, router
except ImportError:  # 표준 라이브러리만 있는 환경
    TestClient = None

XSS = "<script>alert(1)</script>"
ORIGIN = {"Origin": "http://127.0.0.1:8765"}
TOKEN_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@unittest.skipIf(TestClient is None, "fastapi 미설치")
class CreateTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="만들기_테스트_"))
        self.db = self.tmp / "운영.sqlite3"
        migrate_mod.migrate(self.db, backup=False)
        self.root = self.tmp / "작업"
        (self.root / "원본").mkdir(parents=True)
        (self.root / "원본" / "e04.mp4").write_bytes(b"\x00")
        (self.root / "원본" / "e04.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n대사\n", encoding="utf-8")
        (self.tmp / "밖.srt").write_text("", encoding="utf-8")
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
        r = self.client.get("/create")
        self.assertEqual(r.status_code, 200)
        return TOKEN_RE.search(r.text).group(1)

    def form(self, **over):
        data = {"work_title": "달빛 수선소", "episode": "4", "video_path": "원본/e04.mp4",
                "srt_path": "원본/e04.srt", "rights_status": "own", "comp": "drama"}
        data.update(over)
        return {k: v for k, v in data.items() if v is not None}

    def post(self, with_token=True, **over):
        data = self.form(**over)
        if with_token:
            data["csrf_token"] = self.token()
        return self.client.post("/create", data=data, headers=ORIGIN)

    def rows(self, sql):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql)]
        finally:
            conn.close()


class CreateGetTest(CreateTestBase):
    def test_form_renders_compositions_and_rights(self):
        r = self.client.get("/create")
        self.assertEqual(r.status_code, 200)
        self.assertIn('name="csrf_token"', r.text)
        self.assertIn('value="drama"', r.text)
        self.assertNotIn('name="comp" value="common"', r.text)  # 공통 규칙은 구성 목록에 없다
        self.assertRegex(r.text, r'value="indie" disabled')  # 미확정 규칙은 고를 수 없다
        for v in ("own", "licensed", "unverified"):
            self.assertIn(f'name="rights_status" value="{v}">', r.text)  # 기본 선택 없음
        self.assertNotIn("checked", r.text)

    def test_missing_roots_blocks_submit(self):
        self.roots = []
        r = self.client.get("/create")
        self.assertIn("CINEDRAM_ROOTS", r.text)
        self.assertRegex(r.text, r'type="submit" disabled')


class CreatePostTest(CreateTestBase):
    def test_success_creates_rows_and_redirects(self):
        r = self.post()
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/status"))
        work = self.rows("SELECT * FROM works")
        src = self.rows("SELECT * FROM sources")
        job = self.rows("SELECT * FROM jobs")
        self.assertEqual([w["title"] for w in work], ["달빛 수선소"])
        self.assertEqual(src[0]["episode"], 4)
        self.assertEqual(src[0]["rights_status"], "own")
        self.assertEqual(Path(src[0]["file_path"]), (self.root / "원본" / "e04.mp4").resolve())
        self.assertEqual(job[0]["engine_cmd"], "check")
        self.assertEqual(job[0]["slot_kind"], "gpu")
        self.assertEqual(job[0]["state"], "queued")
        self.assertEqual(job[0]["queue_state"], "held_human")  # 컷표 없이 워커가 집지 않게
        self.assertEqual(json.loads(job[0]["rule_set"]), ["common", "drama"])
        self.assertEqual(len(self.rows("SELECT * FROM job_events")), 1)
        self.assertIn(f"job={job[0]['id']}", r.headers["location"])
        status = self.client.get(r.headers["location"])
        self.assertIn("달빛 수선소 · E04", status.text)

    def test_same_title_reuses_work(self):
        self.post()
        self.post(episode="5")
        self.assertEqual(len(self.rows("SELECT * FROM works")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM sources")), 2)

    def test_path_escape_rejected(self):
        for bad in ("../밖.srt", str(self.tmp / "밖.srt"), "\\\\server\\share\\a.srt"):
            with self.subTest(bad=bad):
                r = self.post(srt_path=bad)
                self.assertEqual(r.status_code, 422)
                self.assertIn("작업 루트 안의 경로만", r.text)
                self.assertIn('aria-invalid="true"', r.text)
        self.assertEqual(self.rows("SELECT * FROM jobs"), [])

    def test_missing_file_and_wrong_suffix(self):
        r = self.post(video_path="원본/없음.mp4", srt_path="원본/e04.mp4")
        self.assertEqual(r.status_code, 422)
        self.assertIn("파일이 없습니다", r.text)
        self.assertIn(".srt 파일만", r.text)

    def test_rights_not_selected_rejected_and_values_kept(self):
        r = self.post(rights_status=None)
        self.assertEqual(r.status_code, 422)
        self.assertIn("권리 상태를 고르세요", r.text)
        self.assertIn('value="달빛 수선소"', r.text)  # 입력값 보존
        self.assertIn('value="원본/e04.mp4"', r.text)
        self.assertRegex(r.text, r'value="drama" checked')
        self.assertEqual(self.rows("SELECT * FROM sources"), [])

    def test_bad_rights_value_rejected(self):
        r = self.post(rights_status="unknown")
        self.assertEqual(r.status_code, 422)
        self.assertIn("권리 상태를 고르세요", r.text)

    def test_unverified_allowed(self):
        self.assertEqual(self.post(rights_status="unverified").status_code, 303)
        self.assertEqual(self.rows("SELECT rights_status FROM sources")[0]["rights_status"], "unverified")

    def test_disabled_or_unknown_composition_rejected(self):
        for comp in ("indie", "common", "", "../common"):
            with self.subTest(comp=comp):
                r = self.post(comp=comp)
                self.assertEqual(r.status_code, 422)
                self.assertIn("구성을 고르세요", r.text)

    def test_bad_episode_rejected(self):
        for ep in ("0", "-1", "abc", "٣"):
            with self.subTest(ep=ep):
                self.assertEqual(self.post(episode=ep).status_code, 422)

    def test_episode_upper_bound(self):
        self.assertEqual(self.post(episode="99999").status_code, 303)
        for ep in ("100000", "9" * 400):  # 큰 값은 OverflowError(500) 대신 422
            with self.subTest(ep=ep[:10]):
                r = self.post(episode=ep)
                self.assertEqual(r.status_code, 422)
                self.assertIn("회차는 1~99999", r.text)

    def test_duplicate_submit_returns_existing_job(self):
        first = self.post()
        again = self.post(video_path=str(self.root / "원본" / "e04.mp4"))  # 같은 파일, 다른 표기
        self.assertEqual(again.status_code, 303)
        self.assertEqual(again.headers["location"], first.headers["location"])
        self.assertEqual(len(self.rows("SELECT * FROM jobs")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM sources")), 1)
        # 회차가 없을 때도 같은 규칙(NULL 비교)
        a, b = self.post(episode=""), self.post(episode="")
        self.assertEqual(a.headers["location"], b.headers["location"])
        self.assertEqual(len(self.rows("SELECT * FROM jobs")), 2)

    def test_video_suffix_allowlist(self):
        for name in ("e04.MOV", "e04.mkv", "e04.webm", "e04.m4v"):
            (self.root / "원본" / name).write_bytes(b"x")
            with self.subTest(name=name):
                self.assertEqual(self.post(video_path=f"원본/{name}").status_code, 303)
        for name in ("e04.exe", "e04.srt", "e04"):
            (self.root / "원본" / name).write_bytes(b"x")
            with self.subTest(name=name):
                r = self.post(video_path=f"원본/{name}")
                self.assertEqual(r.status_code, 422)
                self.assertIn(".mp4 .mov .mkv .webm .m4v 파일만", r.text)

    def test_channel_layer_not_a_composition(self):
        self.assertNotIn('value="channel-hookingtime"', self.client.get("/create").text)
        r = self.post(comp="channel-hookingtime")
        self.assertEqual(r.status_code, 422)
        self.assertIn("구성을 고르세요", r.text)

    def test_nt_device_path_rejected(self):
        r = self.post(video_path=r"\??\C:\Windows\win.ini")
        self.assertEqual(r.status_code, 422)
        self.assertIn("작업 루트 안의 경로만", r.text)

    def test_no_token_403(self):
        self.client.get("/create")  # 쿠키는 있으나 토큰 미제출
        r = self.post(with_token=False)
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/create", data={**self.form(), "csrf_token": "x" * 40}, headers=ORIGIN)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.rows("SELECT * FROM jobs"), [])

    def test_no_origin_rejected(self):
        data = {**self.form(), "csrf_token": self.token()}
        r = self.client.post("/create", data=data)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.rows("SELECT * FROM jobs"), [])

    def test_xss_escaped(self):
        r = self.post(work_title=XSS, video_path=XSS, rights_status=None)
        self.assertEqual(r.status_code, 422)
        self.assertNotIn(XSS, r.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", r.text)
        # 저장된 작품명도 목록(datalist)·현황에서 이스케이프된다
        self.post(work_title=XSS)
        for page in ("/create", "/status"):
            with self.subTest(page=page):
                self.assertNotIn(XSS, self.client.get(page).text)

    def test_db_missing_503(self):
        data = {**self.form(), "csrf_token": self.token()}
        self.db.unlink()
        r = self.client.post("/create", data=data, headers=ORIGIN)
        self.assertEqual(r.status_code, 503)
        self.assertIn("저장하지 못했습니다", r.text)


if __name__ == "__main__":
    unittest.main()
