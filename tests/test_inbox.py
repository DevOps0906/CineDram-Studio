"""승인함 R7(GET /inbox)·R8(GET /jobs/{id}/video) — 개발 3 U3."""
import importlib.util
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402

from core.approval import hash_output  # noqa: E402
from server.app import create_app  # noqa: E402
from server.deps import templates  # noqa: E402
from server.routes import inbox  # noqa: E402

BASE = "http://127.0.0.1:8765"
_spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)
MP4 = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 8  # 가짜 mp4


def seed(tmp, rights="own", state="review", title="퇴근 직전 걸려 온 전화", kind="video", hold=None, file_path=None):
    """작업 루트(tmp)에 DB·컷표·산출물을 만든다. job id 를 돌려준다."""
    db = tmp / "var" / "studio.db"
    db.parent.mkdir(exist_ok=True)
    if not db.exists():
        migrate_mod.migrate(db, backup=False)
    conn = migrate_mod.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] + 1
    d = tmp / f"plans{n}"
    d.mkdir()
    (d / "컷표.json").write_text(json.dumps({"meta": {"title": [title, "둘째 줄"], "source_video": "원본.mp4"}}), encoding="utf-8")
    if kind == "video":
        (d / "out.mp4").write_bytes(MP4)
        rel = "out.mp4"
    else:
        (d / "pkg").mkdir()
        (d / "pkg" / "대본.txt").write_text("대본", encoding="utf-8")
        rel = "pkg"
    if conn.execute("SELECT COUNT(*) FROM works").fetchone()[0] == 0:
        conn.execute("INSERT INTO works(title) VALUES ('유리정원 사무소')")
    sid = conn.execute("INSERT INTO sources(work_id, episode, file_path, origin, rights_status) VALUES (1, 7, ?, 'local_file', ?)",
                       (f"원본{n}.mp4", rights)).lastrowid
    jid = conn.execute(
        "INSERT INTO jobs(source_id, rule_set, state, queue_state, plan_path, plan_hash, hold_reason, channel_id) "
        "VALUES (?, '[\"common\",\"drama\"]', ?, 'held_human', ?, 'PH1', ?, 'ch-k3x7Qa')",
        (sid, state, f"plans{n}/컷표.json", hold)).lastrowid
    conn.execute("INSERT INTO outputs(job_id, file_path, sha256, mode, plan_hash) VALUES (?,?,?,?,?)",
                 (jid, file_path or rel, hash_output(d / rel), "auto_render" if kind == "video" else "manual", "PH1"))
    conn.execute("INSERT INTO job_events(job_id, from_state, to_state, message) VALUES (?, 'building', ?, ?)",
                 (jid, state, "승인: 사용자" if state == "approved" else "검수 대기"))
    conn.close()
    return jid


class InboxBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="승인함_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def client(self):
        app = create_app(db_path=self.tmp / "var" / "studio.db")
        app.state.root = self.tmp
        return TestClient(app, base_url=BASE)

    def page(self, url="/inbox"):
        r = self.client().get(url)
        self.assertEqual(r.status_code, 200, r.text[:300])
        return r.text


class VariantTest(InboxBase):
    def test_normal_card_has_approve_and_forms(self):
        jid = seed(self.tmp)
        t = self.page()
        self.assertIn("퇴근 직전 걸려 온 전화 둘째 줄", t)
        self.assertIn("유리정원 사무소", t)
        self.assertIn("E07", t)
        self.assertIn(f'action="/jobs/{jid}/approve-video"', t)
        for name in ("plan_hash", "output_id", "sha256", "confirm", "csrf_token", "note"):
            self.assertIn(f'name="{name}"', t, name)
        for i in range(1, 7):
            self.assertIn(f'name="chk_{i}"', t)
        self.assertIn(f'name="targets" value="{inbox.CHANGE_TARGETS[0][0]}"', t)
        self.assertIn(f'formaction="/jobs/{jid}/request-change"', t)
        self.assertIn(f'formaction="/jobs/{jid}/rebuild"', t)
        self.assertIn('name="plan_hash" value="PH1"', t)
        self.assertRegex(t, r'name="sha256" value="[0-9a-f]{64}"')
        self.assertRegex(t, '<button class="btn btn-primary" type="submit" aria-describedby="chk-hint-\d+" aria-disabled="true" data-needs-checks>(<svg.*?</svg>)?승인')
        self.assertIn("체크 6칸 남음", t)
        self.assertIn("<span class=\"sr-only\">미리보기 불러오기</span>", t)
        self.assertNotRegex(t, r"class=\"pill\"[^>]*aria-pressed")
        self.assertIn("산출물 영상", t)
        self.assertIn("자동 점검 미실행", t)
        self.assertIn(f'src="/jobs/{jid}/video"', t)

    def test_unverified_rights_has_hold_not_approve(self):
        jid = seed(self.tmp, rights="unverified")
        t = self.page()
        self.assertIn("권리 미확인", t)
        self.assertIn(f'formaction="/jobs/{jid}/hold"', t)
        self.assertNotRegex(t, r"btn-primary[^>]*>(<svg.*?</svg>)?승인")
        self.assertNotIn('name="chk_1"', t)

    def test_held_card_shows_label_not_button(self):
        seed(self.tmp, rights="unverified", hold=inbox.HOLD_REASON)
        t = self.page("/inbox?tab=held")
        self.assertIn("보관됨", t)
        self.assertNotIn("/hold", t)
        self.assertIn("카드가 없습니다", self.page("/inbox"))

    def test_inspect_fail_disables_approve_with_reason(self):
        seed(self.tmp)
        ins = {"ok": 12, "warn": 3, "fail": 1, "fail_reason": "자막 0.6초 어긋남"}
        with mock.patch.object(inbox, "inspect_for", return_value=ins):
            t = self.page()
        self.assertRegex(t, r'<button class="btn btn-primary" type="submit" disabled aria-disabled="true" title="점검 실패 1건: 자막 0.6초 어긋남"')
        self.assertIn("승인 불가", t)
        self.assertIn("통과 12", t)

    def test_busy_variant_disables_all(self):
        jid = seed(self.tmp)
        conn = migrate_mod.connect(self.tmp / "var" / "studio.db")
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute(inbox._SELECT).fetchone())
        card = inbox.build_card(conn, self.tmp, row)
        card["busy"] = True
        conn.close()
        req = SimpleNamespace(state=SimpleNamespace(csrf_token="tok"))
        html = templates.env.get_template("_approval_card.html").render(
            c=card, selected=None, targets=inbox.CHANGE_TARGETS, steps=inbox.CAPCUT_STEPS, request=req)
        self.assertIn('aria-busy="true"', html)
        self.assertIn("승인 중", html)
        self.assertEqual(len(re.findall(r"<button[^>]*disabled", html)), 3)
        self.assertEqual(card["id"], jid)

    def test_approved_card_shows_log_and_disabled_upload(self):
        seed(self.tmp, state="approved")
        t = self.page("/inbox?tab=approved")
        self.assertIn("승인됨", t)
        self.assertIn("승인: 사용자", t)
        self.assertIn("ch-k3x7Qa", t)
        self.assertRegex(t, r"<button[^>]*disabled[^>]*>(<svg.*?</svg>)?업로드 예약")
        self.assertNotIn("approve-video", t)

    def test_folder_output_lists_files(self):
        seed(self.tmp, kind="folder")
        t = self.page()
        self.assertIn("대본.txt", t)
        self.assertNotIn("/video", t)

    def test_empty_and_counts(self):
        seed(self.tmp, state="approved")
        t = self.page()
        self.assertIn("승인 대기 카드가 없습니다", t)
        self.assertIn("승인 대기 0", t)
        self.assertIn("승인됨 1", t)

    def test_xss_title_escaped(self):
        seed(self.tmp, title="<img src=x onerror=alert(1)>")
        t = self.page()
        self.assertNotIn("<img src=x", t)
        self.assertIn("&lt;img src=x", t)

    def test_no_inline_script_or_handlers(self):
        seed(self.tmp)
        t = self.page()
        self.assertNotRegex(t, r"<script(?![^>]*\bsrc=)")
        self.assertNotRegex(t, r"\son\w+=")
        self.assertNotIn(" style=", t)

    def test_bad_params_ignored(self):
        seed(self.tmp)
        self.assertEqual(self.client().get("/inbox?job=abc").status_code, 200)
        self.assertEqual(self.client().get("/inbox?job=999&tab=zzz").status_code, 200)


class VideoTest(InboxBase):
    def test_full_and_range(self):
        jid = seed(self.tmp)
        c = self.client()
        r = c.get(f"/jobs/{jid}/video")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "video/mp4")
        self.assertEqual(r.content, MP4)
        r = c.get(f"/jobs/{jid}/video", headers={"Range": "bytes=10-19"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.content, MP4[10:20])
        self.assertEqual(r.headers["content-range"], f"bytes 10-19/{len(MP4)}")

    def test_missing_job_and_folder_404(self):
        jid = seed(self.tmp, kind="folder")
        c = self.client()
        self.assertEqual(c.get(f"/jobs/{jid}/video").status_code, 404)
        self.assertEqual(c.get("/jobs/999/video").status_code, 404)

    def test_path_escape_rejected(self):
        (self.tmp / "secret.mp4").write_bytes(b"SECRET")
        for bad in ("../secret.mp4", "..\\secret.mp4", str(self.tmp / "secret.mp4"), "C:/Windows/x.mp4", "a/../../secret.mp4"):
            with self.subTest(bad=bad):
                jid = seed(self.tmp, file_path=bad)
                r = self.client().get(f"/jobs/{jid}/video")
                self.assertEqual(r.status_code, 404)
                self.assertNotIn("SECRET", r.text)

    def test_non_int_id(self):
        seed(self.tmp)
        self.assertIn(self.client().get("/jobs/abc/video").status_code, (404, 422))


if __name__ == "__main__":
    unittest.main()
