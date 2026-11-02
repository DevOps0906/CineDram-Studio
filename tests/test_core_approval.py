"""core/approval.py·core/gate_cli.py·0003 마이그레이션·CSP (개발 3 U0a)."""
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402

from core import approval  # noqa: E402
from server.app import create_app  # noqa: E402

_spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="승인_테스트_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = self.tmp / "d.sqlite3"
        migrate_mod.migrate(self.db)
        self.conn = migrate_mod.connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO works(title) VALUES ('작품')")
        (self.tmp / "plans").mkdir()
        (self.tmp / "plans" / "원본.mp4").write_bytes(b"x")
        self.plan = self.tmp / "plans" / "컷표.json"
        self.write_plan("원본.mp4")

    def write_plan(self, src, **extra):
        self.plan.write_text(json.dumps({"meta": {"source_video": src, **extra}, "cuts": []}), encoding="utf-8")

    def add_source(self, path="plans/원본.mp4", rights="own"):
        return self.conn.execute(
            "INSERT INTO sources(work_id, file_path, origin, rights_status) VALUES (1,?, 'local_file', ?)",
            (path, rights)).lastrowid

    def add_job(self, source_id, plan_path="plans/컷표.json"):
        return self.conn.execute("INSERT INTO jobs(source_id, plan_path, state) VALUES (?,?, 'topic')",
                                 (source_id, plan_path)).lastrowid


class SourceCheckTest(Base):
    def check(self, job, **kw):
        return approval.check_source_binding(self.conn, job, self.plan, self.tmp, **kw)

    def test_ok(self):
        j = self.add_job(self.add_source())
        self.assertEqual(self.check(j, need_rights=True), (1, "own"))

    def test_source_id_null(self):
        with self.assertRaises(approval.ApprovalError):
            self.check(self.add_job(None))

    def test_real_path_mismatch(self):
        (self.tmp / "plans" / "다른.mp4").write_bytes(b"y")
        self.write_plan("다른.mp4")
        with self.assertRaises(approval.ApprovalError):
            self.check(self.add_job(self.add_source()))

    def test_escape_and_missing(self):
        j = self.add_job(self.add_source())
        for bad in ("../x.mp4", "C:/x.mp4", "/abs.mp4"):
            self.write_plan(bad)
            with self.assertRaises(approval.ApprovalError):
                self.check(j)
        self.write_plan("없음.mp4")
        with self.assertRaises(approval.ApprovalError):
            self.check(j)
        self.conn.execute("UPDATE sources SET file_path='plans/없음.mp4'")
        self.assertEqual(self.check(j, require_exists=False)[0], 1)  # 존재 확인 생략 옵션

    def test_rights_from_db_only(self):
        self.write_plan("원본.mp4", rights_status="own")  # 컷표 값은 무시
        j = self.add_job(self.add_source(rights="unverified"))
        self.check(j)  # R9 는 허용
        with self.assertRaises(approval.ApprovalError):
            self.check(j, need_rights=True)  # R10 은 거부
        self.conn.execute("UPDATE sources SET rights_status='licensed'")
        self.check(j, need_rights=True)


class ApprovalFileTest(Base):
    def test_read(self):
        f = self.plan.parent / "기획승인.json"
        with self.assertRaises(approval.ApprovalError):
            approval.read_approval_file(self.plan.parent)  # 없음
        for bad in ("", '{"plan_hash": "ab', "[]", '{"plan_hash": "", "tts_hash": "x"}', '{"plan_hash": "a"}'):
            f.write_text(bad, encoding="utf-8")
            with self.assertRaises(approval.ApprovalError, msg=bad):
                approval.read_approval_file(self.plan.parent)
        f.write_text('{"plan_hash": "a", "tts_hash": "b", "x": 1}', encoding="utf-8")
        self.assertEqual(approval.read_approval_file(self.plan.parent), {"plan_hash": "a", "tts_hash": "b"})


class OutputTest(Base):
    def test_hash_file_and_folder(self):
        f = self.plan.parent / "a.mp4"
        f.write_bytes(b"abc")
        self.assertEqual(len(approval.hash_output(f)), 64)
        d = self.plan.parent / "pkg"
        (d / "sub").mkdir(parents=True)
        (d / "a.txt").write_text("1", encoding="utf-8")
        (d / "sub" / "b.txt").write_text("2", encoding="utf-8")
        h1 = approval.hash_output(d)
        (d / "sub" / "b.txt").write_text("3", encoding="utf-8")
        self.assertNotEqual(h1, approval.hash_output(d))  # 내용 변경
        (d / "sub" / "b.txt").write_text("2", encoding="utf-8")
        self.assertEqual(h1, approval.hash_output(d))
        (d / "c.txt").write_text("", encoding="utf-8")
        self.assertNotEqual(h1, approval.hash_output(d))  # 파일 목록 변경
        with self.assertRaises(FileNotFoundError):
            approval.hash_output(self.plan.parent / "없음")

    def test_record_output_versions(self):
        j = self.add_job(self.add_source())
        mp4 = self.plan.parent / "숏폼_상하템플릿.mp4"
        mp4.write_bytes(b"v1")
        o1 = approval.record_output(self.conn, j, self.plan, mp4, "auto_render", "ph")
        mp4.write_bytes(b"v2")
        o2 = approval.record_output(self.conn, j, self.plan, mp4, "auto_render", "ph")
        rows = self.conn.execute(
            "SELECT id, render_ver, file_path, sha256, mode, plan_hash FROM outputs ORDER BY id").fetchall()
        self.assertEqual([r[1] for r in rows], [1, 2])
        self.assertEqual(rows[0][2], "숏폼_상하템플릿.mp4")
        self.assertNotEqual(rows[0][3], rows[1][3])
        self.assertEqual((rows[1][0], rows[1][4], rows[1][5]), (o2, "auto_render", "ph"))
        self.assertLess(o1, o2)


class GateCliTest(Base):
    def cli(self):
        return subprocess.run([sys.executable, str(ROOT / "core" / "gate_cli.py"), str(self.plan)],
                              capture_output=True, text=True, encoding="utf-8")

    def approve(self):
        spec = importlib.util.spec_from_file_location("sf_test", ROOT / "engine" / "shortsflow.py")
        eng = importlib.util.module_from_spec(spec)
        sys.modules["sf_test"] = eng
        spec.loader.exec_module(eng)
        plan = eng.load_plan(self.plan)
        (self.plan.parent / "기획승인.json").write_text(
            json.dumps({"plan_hash": plan["_hash"], "tts_hash": eng.tts_state_hash(plan)}), encoding="utf-8")
        return plan["_hash"]

    def test_unapproved_then_approved_then_tampered(self):
        r = self.cli()
        self.assertEqual(r.returncode, 1)
        self.assertFalse(json.loads(r.stdout.splitlines()[-1])["ok"])
        self.assertFalse(approval.run_gate(self.plan)[0])
        h = self.approve()
        r = self.cli()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads(r.stdout.splitlines()[-1]), {"ok": True, "plan_hash": h, "reason": ""})
        self.assertEqual(approval.run_gate(self.plan), (True, h, ""))
        self.write_plan("원본.mp4", x=1)  # 승인 뒤 변경
        ok, h2, reason = approval.run_gate(self.plan)
        self.assertFalse(ok)
        self.assertNotEqual(h2, h)
        self.assertIn("P0-2", reason)

    def test_truncated_approval_file_is_unapproved(self):
        (self.plan.parent / "기획승인.json").write_text('{"plan_hash": "ab', encoding="utf-8")
        ok, _, reason = approval.run_gate(self.plan)
        self.assertFalse(ok)
        self.assertIn("P0-1", reason)

    def test_bad_plan_is_exit_2(self):
        self.plan.write_text("{깨짐", encoding="utf-8")
        self.assertEqual(self.cli().returncode, 2)
        self.assertFalse(approval.run_gate(self.plan)[0])


class MigrationTest(Base):
    def test_outputs_columns(self):
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(outputs)")}
        self.assertTrue({"sha256", "mode", "plan_hash"} <= cols)

    def test_active_plan_path_unique(self):
        s = self.add_source()
        a = self.add_job(s)
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_job(s)  # 같은 컷표 활성 2건
        self.add_job(s, plan_path="plans/다른.json")
        self.conn.execute("INSERT INTO jobs(source_id) VALUES (?)", (s,))  # NULL plan_path 여러 건 허용
        self.conn.execute("INSERT INTO jobs(source_id) VALUES (?)", (s,))
        self.conn.execute("UPDATE jobs SET queue_state='finished' WHERE id=?", (a,))
        self.add_job(s)  # 끝난 작업의 경로는 재사용 가능


class CspTest(unittest.TestCase):
    def test_csp_on_all_responses(self):
        csp = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
        c = TestClient(create_app(), base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000))
        for r in (c.get("/healthz"), c.get("/nope"), c.get("/healthz", headers={"host": "evil.com"}),
                  c.post("/healthz")):
            self.assertEqual(r.headers["content-security-policy"], csp)



class WorkerRecordsOutputTest(GateCliTest):
    """워커 성공(종료 코드 0) 시 outputs 행이 기록된다(C1). 엔진 render 는 가짜."""

    def test_run_job_records_output(self):
        from worker import runner, queue
        h = self.approve()
        fake = self.tmp / "fake.py"
        fake.write_text("import sys, pathlib\n"
                        "pathlib.Path(sys.argv[2]).parent.joinpath('숏폼_상하템플릿.mp4').write_bytes(b'mp4')\n",
                        encoding="utf-8")
        j = self.add_job(self.add_source())
        self.conn.execute("UPDATE jobs SET state='building', plan_hash=? WHERE id=?", (h, j))
        self.assertEqual(queue.claim_next(self.conn), j)
        self.assertEqual(runner.run_job(self.conn, j, self.tmp, 60, fake), ("review", "held_human"))
        row = self.conn.execute("SELECT file_path, sha256, mode, plan_hash FROM outputs WHERE job_id=?", (j,)).fetchone()
        self.assertEqual(row[0], "숏폼_상하템플릿.mp4")
        self.assertEqual(row[1], approval.hash_output(self.plan.parent / row[0]))
        self.assertEqual(row[2:], ("auto_render", h))


class MessageAndAtomicTest(WorkerRecordsOutputTest):
    def test_error_messages_have_no_path_or_repr(self):  # S5
        j = self.add_job(self.add_source())
        msgs = []
        for bad in ("../x.mp4", "C:/x.mp4", "/abs.mp4"):
            self.write_plan(bad)
            with self.assertRaises(approval.ApprovalError) as cm:
                approval.check_source_binding(self.conn, j, self.plan, self.tmp)
            msgs.append(str(cm.exception))
        self.conn.execute("UPDATE sources SET file_path='../밖.mp4'")
        self.write_plan("원본.mp4")
        with self.assertRaises(approval.ApprovalError) as cm:
            approval.check_source_binding(self.conn, j, self.plan, self.tmp)
        msgs.append(str(cm.exception))
        for m in msgs:
            for bad in (str(self.tmp), "x.mp4", "밖.mp4", "PathRejected", "(", "\\", "Error"):
                self.assertNotIn(bad, m)

    def _setup_job(self):
        h = self.approve()
        fake = self.tmp / "fake.py"
        fake.write_text("import sys, pathlib\n"
                        "pathlib.Path(sys.argv[2]).parent.joinpath('숏폼_상하템플릿.mp4').write_bytes(b'mp4')\n",
                        encoding="utf-8")
        j = self.add_job(self.add_source())
        self.conn.execute("UPDATE jobs SET state='building', plan_hash=? WHERE id=?", (h, j))
        from worker import queue
        self.assertEqual(queue.claim_next(self.conn), j)
        return j, fake

    def test_review_transition_failure_leaves_no_output(self):  # K5
        from worker import runner
        j, fake = self._setup_job()
        self.conn.execute("CREATE TRIGGER boom BEFORE INSERT ON job_events WHEN NEW.to_state='review' "
                          "BEGIN SELECT RAISE(ABORT, 'boom'); END")
        runner.run_job(self.conn, j, self.tmp, 60, fake)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outputs").fetchone()[0], 0)
        self.assertNotEqual(self.conn.execute("SELECT state FROM jobs WHERE id=?", (j,)).fetchone()[0], "review")

    def test_cancelled_while_running_leaves_no_output(self):  # K5
        from worker import runner
        j, fake = self._setup_job()
        fake.write_text(fake.read_text(encoding="utf-8") + "import sqlite3\n"
                        f"c = sqlite3.connect({str(self.db)!r}); "
                        "c.execute(\"UPDATE jobs SET state='canceled', queue_state='finished', slot_id=NULL\"); "
                        "c.commit()\n", encoding="utf-8")
        runner.run_job(self.conn, j, self.tmp, 60, fake)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outputs").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT state FROM jobs WHERE id=?", (j,)).fetchone()[0], "canceled")


if __name__ == "__main__":
    unittest.main()
