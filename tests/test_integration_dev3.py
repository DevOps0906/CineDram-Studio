"""개발 3 U5 통합: 만들기(R3) → 기획 승인(R5 화면 → R9, 실제 엔진 approve 서브프로세스) → 워커 --once(가짜 render)
→ 승인함(R7) → 영상 승인(R10). 모든 단계는 TestClient 로 실제 HTML 을 파싱해 진행한다.
엔진 approve 는 서버 핸들러가 core.subproc 로 부른다(셸 직접 실행 없음). 원본은 더미 파일(ffmpeg 불필요)."""
import html
import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402

from _sf import BASE_SRT, plan_dict, srt_text  # noqa: E402
from server.app import create_app  # noqa: E402
from server.approval_service import CHANGE_TARGETS  # noqa: E402
from worker import __main__ as wmain  # noqa: E402
from worker import runner  # noqa: E402

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
_spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)

OUT_NAME = "숏폼_상하템플릿.mp4"
FAKE_RENDER = ("import sys\nfrom pathlib import Path\n"
               f"Path(sys.argv[2]).parent.joinpath('{OUT_NAME}').write_bytes(b'mp4-fake')\n")
CHKS = {f"chk_{i}": "yes" for i in range(1, 7)}


def hidden(page, name):
    """페이지의 첫 hidden input(name) 값. 없으면 None."""
    for m in re.finditer(r"<input[^>]*>", page):
        tag = m.group(0)
        if re.search(rf'name="{name}"', tag) and 'type="hidden"' in tag:
            v = re.search(r'value="([^"]*)"', tag)
            return html.unescape(v.group(1)) if v else ""
    return None


class FlowBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="통합3_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "var").mkdir()
        self.db = self.tmp / "var" / "studio.db"
        migrate_mod.migrate(self.db, backup=False)
        self.conn = migrate_mod.connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO works(title) VALUES ('작품')")
        app = create_app(port=PORT, db_path=self.db)
        app.state.root = self.tmp
        self.c = TestClient(app, base_url=BASE, client=("127.0.0.1", 50000))
        self.fake = self.tmp / "fake_engine.py"
        self.fake.write_text(FAKE_RENDER, encoding="utf-8")

    # ── 1. 컷표·원본 배치 ──
    def seed(self, rights="own"):
        d = self.tmp / "plans" / "p1"
        d.mkdir(parents=True)
        (d / "컷표.json").write_text(json.dumps(plan_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "원본.srt").write_text(srt_text(BASE_SRT), encoding="utf-8")
        (d / "원본.mp4").write_bytes(b"fake-video")  # 더미 원본
        self.conn.execute(
            "INSERT INTO sources(work_id, file_path, origin, rights_status) VALUES (1, 'plans/p1/원본.mp4', 'local_file', ?)",
            (rights,))
        self.plan_rel = "plans/p1/컷표.json"
        self.out_file = d / OUT_NAME

    def csrf(self):
        self.c.get("/status")
        return self.c.cookies.get("csrf")

    def post(self, path, data):
        return self.c.post(path, data={**data, "csrf_token": self.csrf()}, headers={"origin": BASE},
                           follow_redirects=False)

    def row(self, jid):
        return tuple(self.conn.execute("SELECT state, queue_state FROM jobs WHERE id=?", (jid,)).fetchone())

    # ── 2~5 단계 ──
    def submit_make(self):
        page = self.c.get("/make")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(hidden(page.text, "csrf_token"), self.csrf())
        r = self.post("/make", {"source_id": "1", "rule_set": "drama", "plan_path": self.plan_rel,
                                "exec_mode": "auto_render", "topic_mode": "new", "speed": "review"})
        self.assertEqual(r.status_code, 303, r.text[:300])
        jid = self.conn.execute("SELECT MAX(id) FROM jobs").fetchone()[0]
        self.assertEqual(r.headers["location"], f"/status#job-{jid}")
        self.assertEqual(self.row(jid), ("topic", "held_human"))
        return jid

    def approve_plan_via_screen(self, jid):
        page = self.c.get(f"/status?approve={jid}")
        self.assertEqual(page.status_code, 200)
        ph = hidden(page.text, "plan_hash")
        self.assertTrue(ph, "R5 화면에 hidden plan_hash 가 있어야 한다")
        r = self.post(f"/jobs/{jid}/approve-plan", {"plan_hash": ph, "gate_ok": "yes", "confirm": "yes"})
        self.assertEqual(r.status_code, 303, r.text[:300])
        self.assertEqual(self.row(jid), ("building", "waiting"))
        self.assertEqual(self.conn.execute("SELECT plan_hash FROM jobs WHERE id=?", (jid,)).fetchone()[0], ph)
        return ph

    def run_worker_once(self):
        orig = runner.run_job

        def with_fake(conn, job_id, root, timeout):
            return orig(conn, job_id, root, timeout=timeout, engine=self.fake)

        with mock.patch.object(runner, "run_job", with_fake):
            rc = wmain.main(["--once"], db_path=self.db, lock_path=self.tmp / "var" / "worker.lock", root=self.tmp)
        self.assertEqual(rc, 0)

    def to_review(self, jid):
        self.run_worker_once()
        self.assertEqual(self.row(jid), ("review", "held_human"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outputs WHERE job_id=?", (jid,)).fetchone()[0], 1)
        self.assertTrue(self.out_file.is_file())

    def inbox_card(self, jid):
        page = self.c.get(f"/inbox?job={jid}")
        self.assertEqual(page.status_code, 200)
        m = re.search(rf'<article class="card[^"]*" id="card-{jid}".*?</article>', page.text, re.S)
        self.assertIsNotNone(m, "승인함에 카드가 있어야 한다")
        return m.group(0)

    def card_form(self, card):
        return {"output_id": hidden(card, "output_id"), "sha256": hidden(card, "sha256"),
                "plan_hash": hidden(card, "plan_hash"), "confirm": hidden(card, "confirm")}


class FullFlowTest(FlowBase):
    def test_own_rights_make_to_video_approved(self):
        self.seed("own")
        jid = self.submit_make()
        self.approve_plan_via_screen(jid)
        self.to_review(jid)
        card = self.inbox_card(jid)
        self.assertIn('formaction', card)  # 수정 요청·다시 만들기
        self.assertRegex(card, r'<button[^>]*data-needs-checks[^>]*>')  # 승인 버튼(R10)
        self.assertIn(f'formaction="/jobs/{jid}/request-change"', card)
        for key, _ in CHANGE_TARGETS:
            self.assertIn(f'name="targets" value="{key}"', card)
        for i in range(1, 7):
            self.assertIn(f'name="chk_{i}"', card)
        f = self.card_form(card)
        oid = self.conn.execute("SELECT id, sha256 FROM outputs WHERE job_id=?", (jid,)).fetchone()
        self.assertEqual((f["output_id"], f["sha256"]), (str(oid[0]), oid[1]))
        r = self.post(f"/jobs/{jid}/approve-video", {**f, **CHKS})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/inbox"), r.text[:300])
        self.assertEqual(self.row(jid), ("approved", "held_human"))
        # 승인 뒤 승인함 '승인됨' 탭
        self.assertIn("승인됨", self.c.get(f"/inbox?tab=approved&job={jid}").text)

    def test_unverified_rights_r9_ok_r10_409_and_no_approve_button(self):
        self.seed("unverified")
        jid = self.submit_make()
        self.approve_plan_via_screen(jid)  # R9 는 통과
        self.to_review(jid)
        card = self.inbox_card(jid)
        self.assertNotIn('name="chk_1"', card)
        self.assertIn("권리", card)
        self.assertNotIn('data-needs-checks', card)  # [승인] 없음
        self.assertNotIn(f'formaction="/jobs/{jid}/approve-video"', card)
        r = self.post(f"/jobs/{jid}/approve-video", {**self.card_form(card), **CHKS})
        self.assertEqual(r.status_code, 409)
        self.assertIn("권리", r.text)
        self.assertEqual(self.row(jid), ("review", "held_human"))

    def test_output_replaced_before_r10_409(self):
        self.seed("own")
        jid = self.submit_make()
        self.approve_plan_via_screen(jid)
        self.to_review(jid)
        f = self.card_form(self.inbox_card(jid))
        self.out_file.write_bytes(b"swapped-after-screen")
        r = self.post(f"/jobs/{jid}/approve-video", {**f, **CHKS})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.row(jid), ("review", "held_human"))

    def test_r10_missing_check_422_keeps_review(self):
        """실패 경로 보강: 체크 6칸 중 하나라도 빠지면 승인되지 않는다."""
        self.seed("own")
        jid = self.submit_make()
        self.approve_plan_via_screen(jid)
        self.to_review(jid)
        f = self.card_form(self.inbox_card(jid))
        chks = {k: v for k, v in CHKS.items() if k != "chk_4"}
        self.assertEqual(self.post(f"/jobs/{jid}/approve-video", {**f, **chks}).status_code, 422)
        self.assertEqual(self.row(jid), ("review", "held_human"))


if __name__ == "__main__":
    unittest.main()
