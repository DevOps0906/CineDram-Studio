import asyncio
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi import APIRouter, Depends, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from jinja2 import ChoiceLoader, DictLoader  # noqa: E402

from server import deps, jobs_repo  # noqa: E402
from server.app import create_app  # noqa: E402
from server.deps import Form, parse_form_body, read_form  # noqa: E402
from server.security import CSRF_FIELD, MAX_BODY_BYTES  # noqa: E402

BASE = "http://127.0.0.1:8765"
FORM_HEADERS = {"origin": BASE, "content-type": "application/x-www-form-urlencoded"}
SHELL_PAGE = """{% extends "base.html" %}{% set page = "make" %}{% set has_aside = true %}
{% block content %}<h1>빈 셸</h1>{% endblock %}{% block aside %}<p>옆</p>{% endblock %}"""
MACRO_PAGE = """{% extends "base.html" %}{% from "_macros.html" import status_badge, progress, card, card_head, inspect_line, post_form, hidden, text_field, select_field, textarea_field, radio_card, check_box, button, banner, empty_state, skeleton %}
{% block content %}
{% for k in ["ok","review","nosrc","rights","disk"] %}{{ status_badge(k) }}{% endfor %}
{{ progress(2, "nosrc") }}{{ progress(5) }}
{% call card("x") %}{{ card_head("<b>작품</b>", "한드짜", "3분") }}{% endcall %}
{{ inspect_line() }}{{ inspect_line(3, 1, 0) }}
{% call post_form("/x", csrf) %}{{ hidden("plan_hash", "h1") }}
{{ text_field("title", "제목", value='"><script>', error="필수입니다", hint="힌트") }}
{{ select_field("rule_set", "방식", [("a", "A"), ("b", "B")], value="b") }}
{{ textarea_field("note", "메모", value="<i>") }}{{ radio_card("exec_mode", "manual", "수동", "설명", checked=true) }}{{ radio_card("rq", "a", "필수", required=true) }}
{{ check_box("chk_1", "yes", "확인") }}{{ button("승인", "primary", disabled=true, reason="점검 실패") }}{{ button("가기", href="/status") }}
{% endcall %}
{{ banner("오류") }}{{ empty_state("없음", "설명") }}{{ skeleton() }}
{% endblock %}"""


def make_client(db_path="nonexistent.db", pages=None):
    """테스트 전용 템플릿·라우트를 얹은 앱. 반환: (client, 원래 로더 — 정리 때 복원)."""
    app = create_app(db_path=db_path)
    orig = deps.templates.env.loader
    deps.templates.env.loader = ChoiceLoader([DictLoader(pages or {}), orig])
    router = APIRouter()

    @router.get("/_t/shell")
    def shell(request: Request):
        return deps.templates.TemplateResponse(request, "shell_page.html", {})

    @router.get("/_t/macros")
    def macros(request: Request):
        return deps.templates.TemplateResponse(request, "macro_page.html", {"csrf": request.state.csrf_token})

    @router.post("/_t/echo")
    async def echo(request: Request):
        f = await read_form(request)
        return {"title": f.get("title"), "tags": f.getlist("tags")}

    @router.get("/_t/db")
    def db(conn=Depends(deps.get_db)):
        return {"n": conn.execute("SELECT 1").fetchone()[0]}

    app.include_router(router)
    return TestClient(app, base_url=BASE), orig


class ShellTest(unittest.TestCase):
    def setUp(self):
        self.client, orig = make_client(pages={"shell_page.html": SHELL_PAGE, "macro_page.html": MACRO_PAGE})
        self.addCleanup(setattr, deps.templates.env, "loader", orig)

    def test_empty_shell_renders_base(self):
        r = self.client.get("/_t/shell")
        self.assertEqual(r.status_code, 200)
        for needle in ("<h1>빈 셸</h1>", 'class="sidebar"', 'class="tabs"', 'class="aside"', "/static/_tokens.css",
                       "/static/_app.css", "/static/app.js", 'lang="ko"', "<p>옆</p>"):
            self.assertIn(needle, r.text)
        self.assertRegex(r.text, r'href="/make"[^>]*aria-current="page"')

    def test_no_aside_class_when_page_has_no_aside(self):
        self.assertNotIn("no-aside", self.client.get("/_t/shell").text)
        self.assertIn('class="app no-aside"', self.client.get("/_t/macros").text)
        css = (ROOT / "server" / "static" / "_app.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.app\.no-aside\s*\{\s*grid-template-columns:\s*var\(--size-sidebar\)\s+minmax\(0, 1fr\)")

    def test_card_css_rules_present(self):
        css = (ROOT / "server" / "static" / "_app.css").read_text(encoding="utf-8")
        for rule in (".card-title a { text-decoration: none", "details.vid", ".video video { width: 100%; display: block; }"):
            self.assertIn(rule, css)

    def test_a11y_progress_drawer_css_js(self):
        t = self.client.get("/_t/macros").text
        self.assertIn('<span class="sr-only"> (완료)</span>', t)
        css = (ROOT / "server" / "static" / "_app.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.btn-link-sm\s*\{[^}]*min-height: var\(--size-touch\)")
        self.assertIn(".pick:has(input:disabled)", css)
        js = (ROOT / "server" / "static" / "app.js").read_text(encoding="utf-8")
        for needle in ("function setDrawer", ".focus()", "aria-expanded"):
            self.assertIn(needle, js)

    def test_no_inline_script_or_style(self):
        for p in ("/_t/shell", "/_t/macros"):
            t = self.client.get(p).text
            self.assertNotRegex(t, r"<script(?![^>]*\bsrc=)")
            self.assertNotRegex(t, r"<style\b")
            self.assertNotRegex(t, r"\sstyle=")
            self.assertNotRegex(t, r"\son\w+=")

    def test_macros_render(self):
        t = self.client.get("/_t/macros").text
        for text in ("제작 가능", "업로드 검토 필요", "원본 없음", "권리 미확인", "디스크 부족"):
            self.assertIn(text, t)
        self.assertEqual(t.count('aria-current="step"'), 1)  # progress(5)는 진행 중 칸 없음
        self.assertIn("자동 점검 미실행", t)
        self.assertIn("통과 3", t)
        self.assertIn("&lt;b&gt;작품&lt;/b&gt;", t)  # 자동 이스케이프
        self.assertNotIn("<script>", t)
        self.assertIn("필수입니다", t)
        self.assertNotIn('role="alert"', t.split("필수입니다")[0].rsplit("field-error", 1)[-1])  # 필드 오류 span 에 role 없음
        self.assertRegex(t, r'<span class="field-error" id="title-err">')
        self.assertRegex(t, r'name="rq"[^>]*required')
        self.assertNotRegex(t, r'name="exec_mode"[^>]*required')
        self.assertIn('aria-invalid="true"', t)
        self.assertIn('<option value="b" selected>', t)
        self.assertRegex(t, r'<button[^>]*disabled[^>]*title="점검 실패"')
        self.assertIn('role="list"', t)

    def test_post_form_has_csrf_field(self):
        r = self.client.get("/_t/macros")
        token = self.client.cookies.get("csrf")
        self.assertIn(f'<input type="hidden" name="{CSRF_FIELD}" value="{token}">', r.text)
        self.assertIn("data-lock", r.text)

    def test_docs_still_404(self):
        for p in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(p).status_code, 404)

    def test_form_end_to_end_through_middleware(self):
        self.client.get("/healthz")
        tok = self.client.cookies.get("csrf")
        r = self.client.post("/_t/echo", content=f"csrf_token={tok}&title=%ED%95%9C&tags=a&tags=b", headers=FORM_HEADERS)
        self.assertEqual(r.json(), {"title": "한", "tags": ["a", "b"]})
        r = self.client.post("/_t/echo", content=f"csrf_token={tok}&title=%FF", headers=FORM_HEADERS)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/_t/echo", content=f"csrf_token={tok}&t=" + "x" * (MAX_BODY_BYTES + 1), headers=FORM_HEADERS)
        self.assertEqual(r.status_code, 413)

    def test_db_dependency_503_when_missing(self):
        self.assertEqual(self.client.get("/_t/db").status_code, 503)


class StaticTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(), base_url=BASE)

    def test_serves_assets(self):
        for f in ("_app.css", "_tokens.css", "app.js"):
            self.assertEqual(self.client.get(f"/static/{f}").status_code, 200, f)

    def test_directory_listing_and_traversal_rejected(self):
        for p in ("/static/", "/static", "/static/../app.py", "/static/%2e%2e/app.py", "/static/..%5capp.py",
                  "/static/%2e%2e%2fsecurity.py", "/static/..%2fapp.py", "/static/nope.js"):
            with self.subTest(p=p):
                r = self.client.get(p, follow_redirects=False)
                self.assertIn(r.status_code, (307, 404), p)
                self.assertNotIn("create_app", r.text)

    def test_static_js_has_no_external_url(self):
        js = (ROOT / "server" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotRegex(js, r"https?://")


class FormParserTest(unittest.TestCase):
    def test_parse_basic_repeat_blank(self):
        f = parse_form_body("a=1&a=2&b=&c=%ED%95%9C".encode())
        self.assertIsInstance(f, Form)
        self.assertEqual(f.getlist("a"), ["1", "2"])
        self.assertEqual(f.get("a"), "1")
        self.assertEqual(f.get("b", "x"), "")
        self.assertEqual(f.get("c"), "한")
        self.assertEqual(f.get("zzz", "d"), "d")
        self.assertIn("a", f)

    def test_invalid_encoding_rejected(self):
        for body in (b"a=\xff\xfe", b"a=%FF%FE"):
            with self.subTest(body=body):
                with self.assertRaises(HTTPException) as cm:
                    parse_form_body(body)
                self.assertEqual(cm.exception.status_code, 400)

    def test_too_many_fields_rejected(self):
        body = "&".join(f"k{i}=1" for i in range(deps.MAX_FORM_FIELDS + 1)).encode()
        with self.assertRaises(HTTPException) as cm:
            parse_form_body(body)
        self.assertEqual(cm.exception.status_code, 400)

    @staticmethod
    def _request(chunks, ctype="application/x-www-form-urlencoded"):
        chunks = list(chunks)

        async def receive():
            if chunks:
                return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}
            return {"type": "http.disconnect"}
        scope = {"type": "http", "method": "POST", "headers": [(b"content-type", ctype.encode())], "path": "/"}
        return Request(scope, receive)

    def test_read_form_limit_and_type(self):
        f = asyncio.run(read_form(self._request([b"a=1&", b"b=2"])))
        self.assertEqual((f.get("a"), f.get("b")), ("1", "2"))
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(read_form(self._request([b"a=" + b"x" * 20]), max_body=10))
        self.assertEqual(cm.exception.status_code, 413)
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(read_form(self._request([b"a=1"], ctype="multipart/form-data; boundary=x")))
        self.assertEqual(cm.exception.status_code, 415)


class RepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "t.db"
        spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.migrate(self.db, backup=False)
        self.conn = deps.open_db(self.db)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO jobs (state, queue_state) VALUES ('review', 'held_human')")
        self.jid = self.conn.execute("SELECT id FROM jobs").fetchone()["id"]

    def test_open_db_does_not_create_file(self):
        missing = Path(self.tmp.name) / "missing.db"
        with self.assertRaises(sqlite3.Error):
            deps.open_db(missing)
        self.assertFalse(missing.exists())

    def test_transition_conditional_and_event(self):
        c, jid = self.conn, self.jid
        self.assertTrue(jobs_repo.transition_from(c, jid, "review", "topic", queue_state="held_human", message="수정 요청"))
        self.assertFalse(jobs_repo.transition_from(c, jid, "review", "topic"))  # 이중 클릭
        self.assertEqual(jobs_repo.get_job(c, jid)["state"], "topic")
        ev = c.execute("SELECT from_state, to_state FROM job_events WHERE job_id=?", (jid,)).fetchall()
        self.assertEqual([(e["from_state"], e["to_state"]) for e in ev], [("review", "topic")])
        with self.assertRaises(ValueError):
            jobs_repo.transition_from(c, jid, "topic", "building", state="x")
        self.assertTrue(jobs_repo.transition_from(c, jid, "topic", "canceled", queue_state="finished"))
        self.assertIsNotNone(jobs_repo.get_job(c, jid)["finished_at"])
        self.assertEqual(len(jobs_repo.list_jobs(c, ["canceled"])), 1)

    def test_expect_queue_state_mismatch_no_change_no_event(self):
        c, jid = self.conn, self.jid  # review / held_human
        self.assertFalse(jobs_repo.transition_from(c, jid, "review", "topic", expect_queue_state="running"))
        self.assertFalse(jobs_repo.transition_from(c, jid, "review", "topic", expect_queue_state=("running", "waiting")))
        self.assertEqual(jobs_repo.get_job(c, jid)["state"], "review")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM job_events").fetchone()[0], 0)
        self.assertTrue(jobs_repo.transition_from(c, jid, "review", "topic", queue_state=jobs_repo.TOPIC_QUEUE_STATE,
                                                  expect_queue_state=("held_human", "waiting")))
        self.assertEqual(jobs_repo.get_job(c, jid)["queue_state"], "held_human")
        with self.assertRaises(ValueError):
            jobs_repo.transition_from(c, jid, "topic", "building", slot_id="approve")


if __name__ == "__main__":
    sys.exit(unittest.main())
