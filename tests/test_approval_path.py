"""승인 경로 R9~R15 (개발 3 U4, 위험 높음). 보장 3가지를 고정한다.
(1) 엔진 approve 호출은 server/approval_service.py 한 곳  (2) R9·R10 이 권리·source_id·실경로·해시·6칸 체크를 다시 검사
(3) 파일 앞섬: DB 실패 시 topic 유지·자동 승격 없음·재시도 성공.
엔진 approve 는 테스트 안에서 서버 핸들러가 core.subproc 로 부른다(셸에서 직접 실행하지 않는다)."""
import importlib.util
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402

from _sf import BASE_SRT, plan_dict, srt_text  # noqa: E402
from core.approval import hash_output, run_gate  # noqa: E402
from server.app import create_app  # noqa: E402
from worker import queue, runner  # noqa: E402

BASE = "http://127.0.0.1:8765"
_spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)
OUT_NAME = "숏폼_상하템플릿.mp4"
CHKS = {f"chk_{i}": "yes" for i in range(1, 7)}
FAKE_RENDER = ("import sys\nfrom pathlib import Path\n"
               f"Path(sys.argv[2]).parent.joinpath('{OUT_NAME}').write_bytes(b'mp4-fake')\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="승인경로_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "var").mkdir()
        self.db = self.tmp / "var" / "studio.db"
        migrate_mod.migrate(self.db, backup=False)
        self.conn = migrate_mod.connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO works(title) VALUES ('작품')")
        self.n = 0

    # ── 픽스처 ──
    def make_job(self, state="topic", queue_state="held_human", rights="own", plan=None, with_source=True,
                 plan_hash=None):
        self.n += 1
        d = self.tmp / "plans" / f"p{self.n}"
        d.mkdir(parents=True)
        (d / "컷표.json").write_text(json.dumps(plan or plan_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "원본.srt").write_text(srt_text(BASE_SRT), encoding="utf-8")
        (d / "원본.mp4").write_bytes(b"fake-video")
        sid = None
        if with_source:
            sid = self.conn.execute(
                "INSERT INTO sources(work_id, file_path, origin, rights_status) VALUES (1, ?, 'local_file', ?)",
                (f"plans/p{self.n}/원본.mp4", rights)).lastrowid
        jid = self.conn.execute(
            "INSERT INTO jobs(source_id, rule_set, state, queue_state, plan_path, plan_hash) VALUES (?, '[\"common\"]', ?, ?, ?, ?)",
            (sid, state, queue_state, f"plans/p{self.n}/컷표.json", plan_hash)).lastrowid
        return jid

    def plan_file(self, jid):
        return self.tmp / self.row(jid)["plan_path"]

    def row(self, jid):
        cur = self.conn.execute("SELECT * FROM jobs WHERE id=?", (jid,))
        return dict(zip([c[0] for c in cur.description], cur.fetchone()))

    def val(self, sql, *args):
        r = self.conn.execute(sql, args).fetchone()
        return r[0] if r else None

    def shown_hash(self, jid):
        return run_gate(self.plan_file(jid))[1]

    def edit_plan(self, jid, fn):
        f = self.plan_file(jid)
        p = json.loads(f.read_text(encoding="utf-8"))
        fn(p)
        f.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── HTTP ──
    def client(self):
        app = create_app(db_path=self.db)
        app.state.root = self.tmp
        return TestClient(app, base_url=BASE, client=("127.0.0.1", 50000))

    def post(self, path, data=None, csrf=True, origin=True, client=None):
        c = client or self.client()
        c.get("/status")
        d = dict(data or {})
        if csrf:
            d["csrf_token"] = c.cookies.get("csrf")
        return c.post(path, data=d, headers={"origin": BASE} if origin else {}, follow_redirects=False)

    def plan_form(self, jid, **over):
        d = {"confirm": "yes", "gate_ok": "yes", "plan_hash": self.shown_hash(jid)}
        d.update(over)
        return {k: v for k, v in d.items() if v is not None}

    def approve_plan(self, jid, **over):
        return self.post(f"/jobs/{jid}/approve-plan", self.plan_form(jid, **over))

    def to_review(self, jid, sha_override=None):
        """실제 R9 로 승인한 뒤 워커가 끝낸 것처럼 review + 산출물 행을 만든다. output id 를 돌려준다."""
        r = self.approve_plan(jid)
        self.assertEqual(r.status_code, 303, r.text[:300])
        return self.add_output(jid, move_to_review=True)

    def add_output(self, jid, move_to_review=False, content=b"mp4-fake"):
        d = self.plan_file(jid).parent
        (d / OUT_NAME).write_bytes(content)
        oid = self.conn.execute(
            "INSERT INTO outputs(job_id, file_path, sha256, mode, plan_hash) VALUES (?,?,?,?,?)",
            (jid, OUT_NAME, hash_output(d / OUT_NAME), "auto_render", self.row(jid)["plan_hash"])).lastrowid
        if move_to_review:
            self.conn.execute("UPDATE jobs SET state='review', queue_state='held_human' WHERE id=?", (jid,))
        return oid

    def video_form(self, jid, oid, **over):
        o = self.conn.execute("SELECT sha256, plan_hash FROM outputs WHERE id=?", (oid,)).fetchone()
        d = {"confirm": "yes", "plan_hash": o[1], "output_id": str(oid), "sha256": o[0], **CHKS}
        d.update(over)
        return {k: v for k, v in d.items() if v is not None}

    def approve_video(self, jid, oid, **over):
        return self.post(f"/jobs/{jid}/approve-video", self.video_form(jid, oid, **over))


# ───────── 보장 1·2: 호출 위치·사람만 ─────────


class GuaranteeTest(Base):
    def test_engine_approve_call_site_is_single_file(self):
        """보장 1: 엔진 approve 인자는 server/approval_service.py 한 곳에만 있다."""
        pat = re.compile(r"""["']approve["']""")
        hits = {str(p.relative_to(ROOT)).replace("\\", "/")
                for d in ("server", "worker", "core", "engine") for p in (ROOT / d).rglob("*.py") if pat.search(p.read_text(encoding="utf-8"))}
        hits.discard("engine/shortsflow.py")  # 엔진 자신의 argparse choices
        self.assertEqual(hits, {"server/approval_service.py"})
        src = (ROOT / "server" / "approval_service.py").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"subproc\.run\(", src)), 1)
        self.assertIn("APPROVER", src)
        self.assertNotIn("form.get(\"by\"", src)

    def test_only_post_routes_exist_and_get_changes_nothing(self):
        jid = self.make_job()
        c = self.client()
        for tail in ("approve-plan", "approve-video", "request-change", "rebuild", "hold", "cancel", "revert-plan"):
            for method in ("get", "put", "delete", "patch"):
                r = getattr(c, method)(f"/jobs/{jid}/{tail}", headers={"origin": BASE})
                self.assertIn(r.status_code, (403, 404, 405), (tail, method, r.status_code))
        self.assertEqual(self.row(jid)["state"], "topic")

    def test_post_requires_csrf_and_origin(self):
        jid = self.make_job()
        r = self.post(f"/jobs/{jid}/approve-plan", self.plan_form(jid), csrf=False)
        self.assertEqual(r.status_code, 403)
        r = self.post(f"/jobs/{jid}/approve-plan", self.plan_form(jid), origin=False)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.row(jid)["state"], "topic")
        self.assertFalse((self.plan_file(jid).parent / "기획승인.json").exists())

    def test_unknown_job_404(self):
        for tail in ("approve-plan", "approve-video", "request-change", "rebuild", "hold", "cancel", "revert-plan"):
            r = self.post(f"/jobs/9999/{tail}", {"confirm": "yes", "gate_ok": "yes", "plan_hash": "x", **CHKS,
                                                 "output_id": "1", "sha256": "x"})
            self.assertEqual(r.status_code, 404, tail)


# ───────── R9 ─────────


class ApprovePlanTest(Base):
    def test_success_updates_state_hash_approval_and_event(self):
        jid = self.make_job()
        shown = self.shown_hash(jid)
        r = self.approve_plan(jid)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/status"))
        row = self.row(jid)
        self.assertEqual((row["state"], row["queue_state"], row["slot_id"], row["hold_reason"]),
                         ("building", "waiting", None, None))
        self.assertEqual(row["plan_hash"], shown)
        ap = json.loads((self.plan_file(jid).parent / "기획승인.json").read_text(encoding="utf-8"))
        self.assertEqual((ap["plan_hash"], row["tts_hash"]), (shown, ap["tts_hash"]))
        a = self.conn.execute("SELECT plan_hash, tts_hash, approved_by FROM approvals WHERE job_id=?", (jid,)).fetchall()
        self.assertEqual([tuple(x) for x in a], [(shown, ap["tts_hash"], "사용자")])
        self.assertEqual(self.val("SELECT COUNT(*) FROM job_events WHERE job_id=? AND to_state='building'", jid), 1)

    def test_requires_confirm_gate_ok_and_hash(self):
        jid = self.make_job()
        for over in ({"confirm": None}, {"gate_ok": None}, {"confirm": "no"}, {"plan_hash": None}):
            with self.subTest(over=over):
                r = self.approve_plan(jid, **over)
                self.assertEqual(r.status_code, 422)
        self.assertEqual(self.row(jid)["state"], "topic")
        self.assertFalse((self.plan_file(jid).parent / "기획승인.json").exists())

    def test_state_must_be_topic_held_human(self):
        for st, qs in (("building", "waiting"), ("review", "held_human"), ("queued", "waiting"), ("topic", "waiting")):
            with self.subTest(state=st, queue=qs):
                jid = self.make_job(state=st, queue_state=qs)
                self.assertEqual(self.approve_plan(jid).status_code, 409)
                self.assertFalse((self.plan_file(jid).parent / "기획승인.json").exists())

    def test_source_id_null_409(self):
        jid = self.make_job(with_source=False)
        self.assertEqual(self.approve_plan(jid).status_code, 409)
        self.assertEqual(self.row(jid)["state"], "topic")
        self.assertFalse((self.plan_file(jid).parent / "기획승인.json").exists())

    def test_source_video_must_match_registered_source(self):
        jid = self.make_job()
        d = self.plan_file(jid).parent
        (d / "다른원본.mp4").write_bytes(b"other")
        self.edit_plan(jid, lambda p: p["meta"].update(source_video="다른원본.mp4"))
        r = self.approve_plan(jid)
        self.assertEqual(r.status_code, 409)
        self.assertNotIn("내용이 바뀌었습니다", r.text)  # 해시 때문이 아니라 원본 불일치로 거부
        self.assertFalse((d / "기획승인.json").exists())

    def test_source_video_outside_root_409(self):
        jid = self.make_job()
        self.edit_plan(jid, lambda p: p["meta"].update(source_video="../../../밖.mp4"))
        r = self.approve_plan(jid, plan_hash="deadbeef")
        self.assertEqual(r.status_code, 409)
        self.assertNotIn("내용이 바뀌었습니다", r.text)
        self.assertFalse((self.plan_file(jid).parent / "기획승인.json").exists())

    def test_source_file_missing_409(self):
        jid = self.make_job()
        (self.plan_file(jid).parent / "원본.mp4").unlink()
        self.assertEqual(self.approve_plan(jid, plan_hash="deadbeef").status_code, 409)

    def test_unverified_rights_allowed_for_plan(self):
        """T3: 기획 승인은 권리 미확인이어도 허용(영상 승인에서 막는다)."""
        jid = self.make_job(rights="unverified")
        self.assertEqual(self.approve_plan(jid).status_code, 303)
        self.assertEqual(self.row(jid)["state"], "building")

    def test_rights_in_plan_meta_is_ignored(self):
        """권리 정본은 DB. 컷표 meta.rights_status 가 own 이어도 DB 가 unverified 면 R10 에서 막힌다(여기서는 R9 통과만 확인)."""
        jid = self.make_job(rights="unverified")
        self.assertEqual(json.loads(self.plan_file(jid).read_text(encoding="utf-8"))["meta"]["rights_status"], "own")
        self.assertEqual(self.approve_plan(jid).status_code, 303)

    def test_hidden_hash_mismatch_409(self):
        jid = self.make_job()
        r = self.approve_plan(jid, plan_hash="0123456789abcdef")
        self.assertEqual(r.status_code, 409)
        self.assertIn("내용이 바뀌었습니다", r.text)
        self.assertFalse((self.plan_file(jid).parent / "기획승인.json").exists())

    def test_plan_edited_after_form_shown_409(self):
        jid = self.make_job()
        shown = self.shown_hash(jid)
        self.edit_plan(jid, lambda p: p["meta"]["title"].__setitem__(0, "바뀐 제목"))
        r = self.post(f"/jobs/{jid}/approve-plan", {"confirm": "yes", "gate_ok": "yes", "plan_hash": shown})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.row(jid)["state"], "topic")

    def test_engine_rejection_422_masked_tail_without_paths(self):
        p = plan_dict()
        p["meta"]["plan"]["flow"] = p["meta"]["plan"]["flow"][:3]
        jid = self.make_job(plan=p)
        r = self.approve_plan(jid)
        self.assertEqual(r.status_code, 422)
        self.assertIn("기획", r.text)
        self.assertNotIn(str(self.tmp), r.text)
        self.assertNotIn("Traceback", r.text)
        self.assertEqual(self.row(jid)["state"], "topic")
        self.assertEqual(self.val("SELECT COUNT(*) FROM approvals"), 0)
        self.assertFalse((self.plan_file(jid).parent / "기획승인.json").exists())

    def test_double_click_only_one_succeeds(self):
        jid = self.make_job()
        shown = self.shown_hash(jid)
        clients = [self.client(), self.client()]
        for c in clients:
            c.get("/status")
        barrier = threading.Barrier(2)
        out = []

        def go(c):
            barrier.wait(30)
            r = c.post(f"/jobs/{jid}/approve-plan", headers={"origin": BASE}, follow_redirects=False,
                       data={"csrf_token": c.cookies.get("csrf"), "confirm": "yes", "gate_ok": "yes", "plan_hash": shown})
            out.append(r.status_code)

        ts = [threading.Thread(target=go, args=(c,)) for c in clients]
        [t.start() for t in ts]
        [t.join(120) for t in ts]
        self.assertEqual(sorted(out), [303, 409])
        self.assertEqual(self.val("SELECT COUNT(*) FROM approvals WHERE job_id=?", jid), 1)
        self.assertEqual(self.val("SELECT COUNT(*) FROM job_events WHERE job_id=? AND to_state='building'", jid), 1)
        self.assertEqual(self.row(jid)["state"], "building")

    def test_file_ahead_db_failure_keeps_topic_and_retry_succeeds(self):
        """보장 3: 엔진이 파일을 쓴 뒤 DB 기록이 실패 → topic 유지, 자동 승격 없음, 재시도 성공."""
        jid = self.make_job()
        approval_file = self.plan_file(jid).parent / "기획승인.json"
        with mock.patch("server.approval_service.jobs_repo.add_event", side_effect=sqlite3.OperationalError("disk I/O")):
            r = self.approve_plan(jid)
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("disk I/O", r.text)
        self.assertTrue(approval_file.exists(), "파일이 정본으로 앞선다")
        row = self.row(jid)
        self.assertEqual((row["state"], row["queue_state"], row["plan_hash"]), ("topic", "held_human", None))
        self.assertEqual(self.val("SELECT COUNT(*) FROM approvals"), 0)
        self.client().get("/status")  # 조회·폴링은 상태를 바꾸지 않는다
        self.assertEqual(self.row(jid)["state"], "topic")
        r = self.approve_plan(jid)  # 재시도(approve 가 파일을 다시 씀 — 멱등)
        self.assertEqual(r.status_code, 303)
        row = self.row(jid)
        self.assertEqual((row["state"], row["queue_state"]), ("building", "waiting"))
        self.assertEqual(row["plan_hash"], self.shown_hash(jid))
        self.assertEqual(self.val("SELECT COUNT(*) FROM approvals WHERE job_id=?", jid), 1)

    def test_plan_changed_during_engine_run_invalidates_approval_file(self):
        """S1: 엔진 실행 중 컷표가 바뀌면 409 + 승인 파일 무효화(gate 는 미승인)."""
        jid = self.make_job()
        shown = self.shown_hash(jid)
        fake = self.tmp / "racing_engine.py"
        fake.write_text(chr(10).join([
            "import json, subprocess, sys",
            "from pathlib import Path",
            "f = Path(sys.argv[2]); p = json.loads(f.read_text(encoding='utf-8'))",
            "p['meta']['title'][0] = '실행 중 바뀜'",
            "f.write_text(json.dumps(p, ensure_ascii=False), encoding='utf-8')",
            f"sys.exit(subprocess.call([sys.executable, r'{ROOT / 'engine' / 'shortsflow.py'}'] + sys.argv[1:]))",
        ]), encoding="utf-8")
        with mock.patch("server.approval_service.ENGINE", fake):
            r = self.post(f"/jobs/{jid}/approve-plan", {"confirm": "yes", "gate_ok": "yes", "plan_hash": shown})
        self.assertEqual(r.status_code, 409)
        d = self.plan_file(jid).parent
        self.assertFalse((d / "기획승인.json").exists())
        self.assertEqual(len(list(d.glob("기획승인.무효-*.json"))), 1)
        self.assertFalse(run_gate(self.plan_file(jid))[0])
        self.assertEqual(self.row(jid)["state"], "topic")

    def test_ai_assistant_requires_rights(self):
        jid = self.make_job(rights="unverified")
        self.conn.execute("UPDATE jobs SET exec_mode='ai_assistant' WHERE id=?", (jid,))
        self.assertEqual(self.approve_plan(jid).status_code, 409)
        self.assertEqual(self.row(jid)["state"], "topic")
        j2 = self.make_job(rights="own")
        self.conn.execute("UPDATE jobs SET exec_mode='ai_assistant' WHERE id=?", (j2,))
        self.assertEqual(self.approve_plan(j2).status_code, 303)

    def test_lock_busy_409(self):
        from server import approval_service as svc
        jid = self.make_job()
        form = self.plan_form(jid)
        with mock.patch.object(svc, "LOCK_WAIT", 0.1):
            svc.APPROVAL_LOCK.acquire()
            try:
                r = self.post(f"/jobs/{jid}/approve-plan", form)
            finally:
                svc.APPROVAL_LOCK.release()
        self.assertEqual(r.status_code, 409)
        self.assertIn("다른 승인 처리 중", r.text)

    def test_truncated_approval_file_is_unapproved_500(self):
        jid = self.make_job()
        with mock.patch("server.approval_service._run_engine_approve",
                        side_effect=lambda root, job_id, f: (f.parent / "기획승인.json").write_text("{", encoding="utf-8")):
            r = self.approve_plan(jid)
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.row(jid)["state"], "topic")

    def test_engine_timeout_or_crash_500(self):
        jid = self.make_job()
        form = self.plan_form(jid)
        fake = mock.Mock(timed_out=True, returncode=-1, output="")
        with mock.patch("server.approval_service.run_gate", return_value=(False, form["plan_hash"], "")), \
                mock.patch("server.approval_service.subproc.run", return_value=fake):
            r = self.post(f"/jobs/{jid}/approve-plan", form)
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.row(jid)["state"], "topic")


# ───────── R10 ─────────


class ApproveVideoTest(Base):
    def test_success(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        r = self.approve_video(jid, oid)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/inbox"))
        row = self.row(jid)
        self.assertEqual((row["state"], row["queue_state"], row["hold_reason"]), ("approved", "held_human", "업로드 승인 대기"))
        msg = self.val("SELECT message FROM job_events WHERE job_id=? AND to_state='approved'", jid)
        self.assertIn("영상 승인", msg)
        self.assertIn("미실행", msg)

    def test_engine_approve_not_called(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        with mock.patch("server.approval_service.subproc.run", side_effect=AssertionError("approve 호출 금지")):
            with mock.patch("server.approval_service.run_gate", return_value=(True, self.row(jid)["plan_hash"], "")):
                self.assertEqual(self.approve_video(jid, oid).status_code, 303)

    def test_requires_confirm_and_all_six_checks(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        cases = [{"confirm": None}] + [{f"chk_{i}": None} for i in range(1, 7)] + [{"chk_3": "no"}]
        for over in cases:
            with self.subTest(over=over):
                self.assertEqual(self.approve_video(jid, oid, **over).status_code, 422)
        self.assertEqual(self.row(jid)["state"], "review")

    def test_state_must_be_review_held_human(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        self.conn.execute("UPDATE jobs SET state='building' WHERE id=?", (jid,))
        self.assertEqual(self.approve_video(jid, oid).status_code, 409)

    def test_unverified_rights_409(self):
        jid = self.make_job(rights="unverified")
        oid = self.to_review(jid)  # R9 는 통과
        r = self.approve_video(jid, oid)
        self.assertEqual(r.status_code, 409)
        self.assertIn("권리", r.text)
        self.assertEqual(self.row(jid)["state"], "review")

    def test_source_id_null_409(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        self.conn.execute("UPDATE jobs SET source_id=NULL WHERE id=?", (jid,))
        self.assertEqual(self.approve_video(jid, oid).status_code, 409)
        self.assertEqual(self.row(jid)["state"], "review")

    def test_source_video_mismatch_409(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        (self.plan_file(jid).parent / "다른.mp4").write_bytes(b"x")
        self.edit_plan(jid, lambda p: p["meta"].update(source_video="다른.mp4"))
        self.assertEqual(self.approve_video(jid, oid).status_code, 409)

    def test_source_video_outside_root_409(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        self.edit_plan(jid, lambda p: p["meta"].update(source_video="../../../밖.mp4"))
        self.assertEqual(self.approve_video(jid, oid).status_code, 409)

    def test_plan_changed_after_approval_409(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        self.edit_plan(jid, lambda p: p["meta"]["title"].__setitem__(0, "승인 뒤 수정"))
        r = self.approve_video(jid, oid)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.row(jid)["state"], "review")

    def test_form_plan_hash_mismatch_409(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        self.assertEqual(self.approve_video(jid, oid, plan_hash="0123456789abcdef").status_code, 409)

    def test_db_plan_hash_mismatch_409(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        self.conn.execute("UPDATE jobs SET plan_hash='0123456789abcdef' WHERE id=?", (jid,))
        self.assertEqual(self.approve_video(jid, oid).status_code, 409)

    def test_form_sha256_mismatch_409(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        self.assertEqual(self.approve_video(jid, oid, sha256="0" * 64).status_code, 409)
        self.assertEqual(self.row(jid)["state"], "review")

    def test_output_rerendered_after_screen_409(self):
        """화면 뒤에 같은 경로로 다시 렌더 → 파일 해시 불일치."""
        jid = self.make_job()
        oid = self.to_review(jid)
        (self.plan_file(jid).parent / OUT_NAME).write_bytes(b"re-rendered")
        r = self.approve_video(jid, oid)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.row(jid)["state"], "review")

    def test_older_output_id_409(self):
        jid = self.make_job()
        old = self.to_review(jid)
        self.add_output(jid, content=b"newer")
        self.assertEqual(self.approve_video(jid, old).status_code, 409)

    def test_output_from_older_plan_409(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        self.conn.execute("UPDATE outputs SET plan_hash='0123456789abcdef' WHERE id=?", (oid,))
        self.assertEqual(self.approve_video(jid, oid).status_code, 409)

    def test_output_file_missing_409(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        (self.plan_file(jid).parent / OUT_NAME).unlink()
        self.assertEqual(self.approve_video(jid, oid).status_code, 409)

    def test_output_path_escape_409(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        self.conn.execute("UPDATE outputs SET file_path='../../../밖.mp4' WHERE id=?", (oid,))
        self.assertEqual(self.approve_video(jid, oid).status_code, 409)

    def test_double_click_only_one_succeeds(self):
        jid = self.make_job()
        oid = self.to_review(jid)
        form = self.video_form(jid, oid)
        clients = [self.client(), self.client()]
        for c in clients:
            c.get("/status")
        barrier = threading.Barrier(2)
        out = []

        def go(c):
            barrier.wait(30)
            r = c.post(f"/jobs/{jid}/approve-video", headers={"origin": BASE}, follow_redirects=False,
                       data={**form, "csrf_token": c.cookies.get("csrf")})
            out.append(r.status_code)

        ts = [threading.Thread(target=go, args=(c,)) for c in clients]
        [t.start() for t in ts]
        [t.join(120) for t in ts]
        self.assertEqual(sorted(out), [303, 409])
        self.assertEqual(self.val("SELECT COUNT(*) FROM job_events WHERE job_id=? AND to_state='approved'", jid), 1)

    def test_package_folder_output(self):
        jid = self.make_job()
        self.assertEqual(self.approve_plan(jid).status_code, 303)
        pkg = self.plan_file(jid).parent / "캡컷패키지"
        pkg.mkdir()
        (pkg / "대본.txt").write_text("대본", encoding="utf-8")
        oid = self.conn.execute(
            "INSERT INTO outputs(job_id, file_path, sha256, mode, plan_hash) VALUES (?,?,?,?,?)",
            (jid, "캡컷패키지", hash_output(pkg), "manual", self.row(jid)["plan_hash"])).lastrowid
        self.conn.execute("UPDATE jobs SET state='review', queue_state='held_human' WHERE id=?", (jid,))
        (pkg / "대본.txt").write_text("바뀐 대본", encoding="utf-8")
        self.assertEqual(self.approve_video(jid, oid).status_code, 409)


# ───────── R11~R15 ─────────


class OtherDecisionsTest(Base):
    def review_job(self, **kw):
        jid = self.make_job(**kw)
        oid = self.to_review(jid)
        return jid, oid

    def test_request_change(self):
        jid, _ = self.review_job()
        r = self.post(f"/jobs/{jid}/request-change", {"note": "제목을 더 세게", "targets": ["title", "hook"]})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/inbox"))
        row = self.row(jid)
        self.assertEqual((row["state"], row["queue_state"]), ("topic", "held_human"))  # C12
        msg = self.val("SELECT message FROM job_events WHERE job_id=? AND to_state='topic'", jid)
        self.assertIn("제목을 더 세게", msg)
        self.assertIn("title", msg)

    def test_request_change_masks_keys_and_limits_note(self):
        jid, _ = self.review_job()
        self.assertEqual(self.post(f"/jobs/{jid}/request-change", {"note": "가" * 501}).status_code, 422)
        self.assertEqual(self.row(jid)["state"], "review")
        self.assertEqual(self.post(f"/jobs/{jid}/request-change", {"note": "key sk-ABCDEFGH12345678zzSECRET"}).status_code, 303)
        msg = self.val("SELECT message FROM job_events WHERE job_id=? AND to_state='topic'", jid)
        self.assertNotIn("sk-ABCDEFGH12345678zzSECRET", msg)

    def test_request_change_wrong_state_409(self):
        jid = self.make_job()
        self.assertEqual(self.post(f"/jobs/{jid}/request-change", {"note": "x"}).status_code, 409)

    def test_reapprove_after_change_overwrites_plan_hash(self):
        jid, _ = self.review_job()
        old = self.row(jid)["plan_hash"]
        self.assertEqual(self.post(f"/jobs/{jid}/request-change", {"note": "수정"}).status_code, 303)
        self.edit_plan(jid, lambda p: p["meta"]["title"].__setitem__(0, "수정한 제목"))
        self.assertEqual(self.approve_plan(jid).status_code, 303)
        row = self.row(jid)
        self.assertNotEqual(row["plan_hash"], old)
        self.assertEqual(row["plan_hash"], self.shown_hash(jid))
        self.assertEqual(row["state"], "building")

    def test_rebuild(self):
        jid, _ = self.review_job()
        r = self.post(f"/jobs/{jid}/rebuild", {"confirm": "yes"})
        self.assertEqual(r.status_code, 303)
        row = self.row(jid)
        self.assertEqual((row["state"], row["queue_state"]), ("building", "waiting"))

    def test_rebuild_needs_confirm_and_same_hash(self):
        jid, _ = self.review_job()
        self.assertEqual(self.post(f"/jobs/{jid}/rebuild", {}).status_code, 422)
        self.edit_plan(jid, lambda p: p["meta"]["title"].__setitem__(0, "바뀜"))
        self.assertEqual(self.post(f"/jobs/{jid}/rebuild", {"confirm": "yes"}).status_code, 409)
        self.assertEqual(self.row(jid)["state"], "review")

    def test_hold(self):
        jid, _ = self.review_job(rights="unverified")
        r = self.post(f"/jobs/{jid}/hold", {"confirm": "yes"})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/inbox"))
        row = self.row(jid)
        self.assertEqual((row["state"], row["hold_reason"]), ("review", "보관(권리 미확인)"))
        self.assertEqual(self.post(f"/jobs/{jid}/hold", {}).status_code, 422)

    def test_hold_wrong_state_409(self):
        jid = self.make_job()
        self.assertEqual(self.post(f"/jobs/{jid}/hold", {"confirm": "yes"}).status_code, 409)

    def test_cancel(self):
        for st, qs in (("queued", "waiting"), ("topic", "held_human"), ("building", "waiting"), ("review", "held_human")):
            with self.subTest(state=st):
                jid = self.make_job(state=st, queue_state=qs)
                self.assertEqual(self.post(f"/jobs/{jid}/cancel", {}).status_code, 422)
                r = self.post(f"/jobs/{jid}/cancel", {"confirm": "yes"})
                self.assertEqual(r.status_code, 303)
                row = self.row(jid)
                self.assertEqual((row["state"], row["queue_state"]), ("canceled", "finished"))
                self.assertIsNotNone(row["finished_at"])

    def test_cancel_running_allowed_done_409(self):  # R14+: 실행 중 취소 허용(워커가 프로세스 종료)
        jid = self.make_job(state="building", queue_state="running")
        self.conn.execute("UPDATE jobs SET slot_id='gpu' WHERE id=?", (jid,))
        self.assertEqual(self.post(f"/jobs/{jid}/cancel", {"confirm": "yes"}).status_code, 303)
        self.assertEqual((self.row(jid)["state"], self.row(jid)["queue_state"]), ("canceled", "finished"))
        j2 = self.make_job(state="done", queue_state="finished")
        self.assertEqual(self.post(f"/jobs/{j2}/cancel", {"confirm": "yes"}).status_code, 409)

    def test_cancel_approved_frees_plan_path(self):
        jid = self.make_job(state="approved", queue_state="held_human")
        self.assertEqual(self.post(f"/jobs/{jid}/cancel", {"confirm": "yes"}).status_code, 303)
        row = self.row(jid)
        self.assertEqual((row["state"], row["queue_state"]), ("canceled", "finished"))

    def test_request_change_unknown_target_422(self):
        jid, _ = self.review_job()
        self.assertEqual(self.post(f"/jobs/{jid}/request-change", {"targets": ["미확인"]}).status_code, 422)
        self.assertEqual(self.post(f"/jobs/{jid}/request-change", {"targets": ["cuts"]}).status_code, 422)
        self.assertEqual(self.row(jid)["state"], "review")

    def test_hold_reason_by_rights(self):
        jid, _ = self.review_job(rights="own")
        self.assertEqual(self.post(f"/jobs/{jid}/hold", {"confirm": "yes"}).status_code, 303)
        self.assertEqual(self.row(jid)["hold_reason"], "보관")

    def test_revert_plan_from_held_building(self):
        jid = self.make_job(state="building", queue_state="held_human")
        self.assertEqual(self.post(f"/jobs/{jid}/revert-plan", {}).status_code, 422)
        r = self.post(f"/jobs/{jid}/revert-plan", {"confirm": "yes"})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/status"))
        row = self.row(jid)
        self.assertEqual((row["state"], row["queue_state"]), ("topic", "held_human"))

    def test_revert_plan_only_building_held_human(self):
        for st, qs in (("building", "waiting"), ("building", "running"), ("topic", "held_human"), ("review", "held_human")):
            with self.subTest(state=st, queue=qs):
                jid = self.make_job(state=st, queue_state=qs)
                self.assertEqual(self.post(f"/jobs/{jid}/revert-plan", {"confirm": "yes"}).status_code, 409)
                self.assertEqual(self.row(jid)["state"], st)


# ───────── 통합 1건: 재승인 → 워커 → 승인함 → 영상 승인 ─────────


class WorkerIntegrationTest(Base):
    def test_reapproval_then_worker_reaches_review_then_video_approved(self):
        jid = self.make_job()
        fake = self.tmp / "fake_engine.py"
        fake.write_text(FAKE_RENDER, encoding="utf-8")
        self.assertEqual(self.approve_plan(jid).status_code, 303)
        # 보류 탈출 → 컷표 수정 → 재승인 (R15 → R9)
        self.conn.execute("UPDATE jobs SET queue_state='held_human', hold_reason='승인 해시 불일치' WHERE id=?", (jid,))
        self.assertEqual(self.post(f"/jobs/{jid}/revert-plan", {"confirm": "yes"}).status_code, 303)
        self.edit_plan(jid, lambda p: p["meta"]["title"].__setitem__(0, "재승인 제목"))
        self.assertEqual(self.approve_plan(jid).status_code, 303)
        picked = queue.claim_next(self.conn)
        self.assertEqual(picked, jid)
        self.assertEqual(runner.run_job(self.conn, jid, self.tmp, engine=fake), ("review", "held_human"))
        oid = self.val("SELECT id FROM outputs WHERE job_id=? ORDER BY id DESC LIMIT 1", jid)
        self.assertIsNotNone(oid)
        self.assertEqual(self.approve_video(jid, oid).status_code, 303)
        self.assertEqual(self.row(jid)["state"], "approved")


if __name__ == "__main__":
    unittest.main()
