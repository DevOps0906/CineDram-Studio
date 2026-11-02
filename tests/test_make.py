"""U1: R1~R4 만들기·원클릭 (정상, plan_path 탈출, 잘못된 enum, CSRF, need_source, XSS)."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402

from db import migrate  # noqa: E402
from server import deps  # noqa: E402
from server.app import create_app  # noqa: E402

BASE = "http://127.0.0.1:8765"


class MakeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="make_테스트_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = self.tmp / "studio.db"
        migrate.migrate(str(self.db), backup=False)
        (self.tmp / "원본").mkdir()
        (self.tmp / "원본" / "a.mp4").write_bytes(b"x")
        (self.tmp / "plan").mkdir()
        (self.tmp / "plan" / "컷표.json").write_text("{}", encoding="utf-8")
        (self.tmp / "plan" / "컷표2.json").write_text("{}", encoding="utf-8")
        conn = deps.open_db(self.db)
        conn.execute("INSERT INTO works (id, title) VALUES (1, '달빛 <b>수선소</b>')")
        conn.execute("INSERT INTO sources (id, work_id, episode, file_path, origin, rights_status) "
                     "VALUES (1, 1, 4, '원본/a.mp4', 'local_file', 'own')")
        conn.execute("INSERT INTO sources (id, work_id, episode, file_path, origin) "
                     "VALUES (2, 1, 5, '원본/없음.mp4', 'local_file')")
        conn.close()
        app = create_app(db_path=self.db)
        app.state.root = self.tmp
        self.client = TestClient(app, base_url=BASE, client=("127.0.0.1", 50000))

    def token(self, path="/make"):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200)
        return self.client.cookies.get("csrf")

    def post(self, path="/make", token=True, **over):
        data = {"source_id": "1", "rule_set": "drama", "plan_path": "plan/컷표.json", "exec_mode": "auto_render",
                "topic_mode": "new", "speed": "review", "channel_id": ""}
        data.update(over)
        data = {k: v for k, v in data.items() if v is not None}
        if token:
            data["csrf_token"] = self.token(path)
        return self.client.post(path, data=data, headers={"origin": BASE}, follow_redirects=False)

    def jobs(self):
        conn = deps.open_db(self.db)
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM jobs")]
        finally:
            conn.close()

    def events(self, job_id):
        conn = deps.open_db(self.db)
        try:
            return [r["to_state"] for r in conn.execute("SELECT to_state FROM job_events WHERE job_id=? ORDER BY id", (job_id,))]
        finally:
            conn.close()


class FormTest(MakeBase):
    def test_get_forms_have_csrf_and_fields(self):
        for path in ("/make", "/make/oneclick"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200)
                for s in ('name="csrf_token"', 'name="source_id"', 'name="rule_set"', 'name="plan_path"', 'name="exec_mode"'):
                    self.assertIn(s, r.text)
                self.assertIn(f'action="{path}"', r.text)
                self.assertNotIn("<script", r.text.replace('<script src="/static/app.js">', ""))

    def test_empty_state_without_sources(self):
        conn = deps.open_db(self.db)
        conn.execute("DELETE FROM sources")
        conn.close()
        r = self.client.get("/make")
        self.assertIn("원본 폴더를 먼저 등록", r.text)
        self.assertNotIn('name="plan_path"', r.text)

    def test_xss_escaped_in_title(self):
        r = self.client.get("/make")
        self.assertNotIn("<b>수선소</b>", r.text)
        self.assertIn("&lt;b&gt;수선소", r.text)


class SubmitTest(MakeBase):
    def test_make_ok_goes_to_topic(self):
        r = self.post()
        self.assertEqual(r.status_code, 303)
        job = self.jobs()[0]
        self.assertEqual(r.headers["location"], f"/status#job-{job['id']}")
        self.assertEqual((job["state"], job["queue_state"], job["source_id"]), ("topic", "held_human", 1))
        self.assertEqual(job["plan_path"], "plan/컷표.json")
        self.assertEqual(job["exec_mode"], "auto_render")
        self.assertEqual(self.events(job["id"]), ["queued", "matching", "topic"])

    def test_oneclick_same_handler(self):
        r = self.post("/make/oneclick", exec_mode="manual", speed="fast")
        self.assertEqual(r.status_code, 303)
        job = self.jobs()[0]
        self.assertEqual((job["state"], job["exec_mode"], job["speed"]), ("topic", "manual", "fast"))

    def test_exec_mode_default_auto_render(self):
        self.assertEqual(self.post(exec_mode=None).status_code, 303)
        self.assertEqual(self.jobs()[0]["exec_mode"], "auto_render")

    def test_need_source_when_file_missing(self):
        r = self.post(source_id="2")
        self.assertEqual(r.status_code, 303)
        job = self.jobs()[0]
        self.assertEqual(job["state"], "need_source")
        self.assertEqual(self.events(job["id"]), ["queued", "matching", "need_source"])

    def test_plan_path_escape_rejected(self):
        for bad in ("../x.json", "/etc/a.json", "C:\a.json", "plan/../../a.json", "\\srv\a.json", "plan/없음.json", "plan"):
            with self.subTest(bad=bad):
                r = self.post(plan_path=bad)
                self.assertEqual(r.status_code, 422)
                self.assertIn("plan_path-err", r.text)
        self.assertEqual(self.jobs(), [])

    def test_bad_enums_rejected(self):
        for field, bad in (("rule_set", "common"), ("rule_set", "person-fantube"), ("rule_set", "../x"),
                           ("exec_mode", "rm"), ("speed", "turbo"), ("topic_mode", "zzz"), ("source_id", "abc"),
                           ("source_id", "99"), ("channel_id", "<x>")):
            with self.subTest(field=field, bad=bad):
                r = self.post(**{field: bad})
                self.assertEqual(r.status_code, 422)
                self.assertIn(f"{field}-err", r.text)
        self.assertEqual(self.jobs(), [])

    def test_a11y_error_and_required(self):
        r = self.post(rule_set="zzz")
        self.assertIn('tabindex="-1" autofocus', r.text)
        self.assertIn('aria-describedby="rule_set-err"', r.text)
        self.assertIn('aria-required="true"', r.text)
        self.assertIn("표시는 필수", r.text)
        self.assertNotIn('id="rule_set-err" role="alert"', r.text)
        self.assertNotIn('aria-current="step"', self.client.get("/make/oneclick").text.split('작성 순서')[1].split("</ol>")[0])

    def test_422_keeps_values_and_escapes(self):
        r = self.post(plan_path='"><script>alert(1)</script>.json')
        self.assertEqual(r.status_code, 422)
        self.assertNotIn("<script>alert(1)", r.text)

    def test_csrf_missing_403(self):
        self.client.get("/make")
        r = self.post(token=False)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.jobs(), [])

    def test_wrong_origin_403(self):
        data = {"csrf_token": self.token(), "source_id": "1", "rule_set": "drama", "plan_path": "plan/컷표.json"}
        r = self.client.post("/make", data=data, headers={"origin": "http://evil.example"}, follow_redirects=False)
        self.assertEqual(r.status_code, 403)

    def test_duplicate_plan_path_422(self):
        self.assertEqual(self.post().status_code, 303)
        r = self.post()
        self.assertEqual(r.status_code, 422)
        self.assertIn("이미 진행 중인 작업이 이 컷표를 씁니다", r.text)
        self.assertIn('role="alert"', r.text)
        self.assertEqual(len(self.jobs()), 1)

    def test_topic_is_always_held_human(self):
        for path, plan in (("/make", "plan/컷표.json"), ("/make/oneclick", "plan/컷표2.json")):
            self.assertEqual(self.post(path, plan_path=plan).status_code, 303)
        jobs = self.jobs()
        self.assertEqual({j["state"] for j in jobs}, {"topic"})
        self.assertEqual({j["queue_state"] for j in jobs}, {"held_human"})

    def test_rights_unverified_allowed_to_submit(self):
        # 원본 2(권리 미확인)도 접수 가능, 영상 승인·업로드 차단은 R10(U4) 책임.
        self.assertEqual(self.post(source_id="2", plan_path="plan/컷표2.json").status_code, 303)


if __name__ == "__main__":
    unittest.main()
