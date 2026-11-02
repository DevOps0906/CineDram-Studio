"""render 다음 inspect: rc 0 → review, rc 1 → outputs 없이 held_human, rc 2·시간 초과 → failed."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_worker_queue import Base  # noqa: E402
from worker import queue, runner  # noqa: E402

RENDER = ("import sys, pathlib; pathlib.Path(sys.argv[2]).parent.joinpath('숏폼_상하템플릿.mp4')"
          ".write_bytes(b'mp4')")


class InspectFlowTest(Base):
    def setUp(self):
        super().setUp()
        self.render = self.tmp / "render.py"
        self.render.write_text(RENDER, encoding="utf-8")

    def go(self, code, sleep=0, result=None, timeout=30, mode="auto_render", with_inspect=True, stamp=True, stale=None):
        self.write_plan()
        if isinstance(result, dict) and stamp:
            result = {"plan_hash": self.plan_hash, **result}
        body = f"import sys, time; time.sleep({sleep})\n"
        if result is not None:
            body += ("import pathlib, json; pathlib.Path(sys.argv[1]).parent.joinpath('점검결과.json')"
                     f".write_text({json.dumps(result)!r}, encoding='utf-8')\n")
        body += f"print('로그 꼬리 SECRET'); sys.exit({code})\n"
        insp = self.tmp / "inspect.py"
        insp.write_text(body, encoding="utf-8")
        if stale is not None:
            if stale.get("plan_hash") == "STAMP":  # 해시까지 같은 가장 나쁜 경우
                stale = {**stale, "plan_hash": self.plan_hash}
            (self.tmp / "plans" / "점검결과.json").write_text(json.dumps(stale), encoding="utf-8")
        j = self.add_job(exec_mode=mode)
        queue.claim_next(self.conn)
        res = runner.run_job(self.conn, j, self.tmp, engine=self.render,
                             inspect_engine=insp if with_inspect else None, inspect_timeout=timeout)
        return j, res

    def outputs(self, j):
        return self.conn.execute("SELECT COUNT(*) FROM outputs WHERE job_id=?", (j,)).fetchone()[0]

    def test_rc0_review(self):
        j, res = self.go(0)
        self.assertEqual(res, ("review", "held_human"))
        self.assertEqual(self.outputs(j), 1)

    def test_rc1_held_with_fail_reason(self):
        j, res = self.go(1, result={"summary": {"fail_reason": "무음 0.5초; 검은 화면"}})
        self.assertEqual(res, ("building", "held_human"))
        self.assertEqual(self.outputs(j), 0)
        self.assertEqual(self.job(j)[3], "점검 실패: 무음 0.5초; 검은 화면")

    def test_rc1_crash_without_result_is_failed(self):
        j, res = self.go(1)
        self.assertEqual(res, ("failed", "finished"))
        self.assertEqual(self.outputs(j), 0)

    def test_rc1_broken_result_is_failed(self):
        j, res = self.go(1, result="not-dict")
        self.assertEqual(res, ("failed", "finished"))

    def test_rc1_hash_mismatch_is_failed(self):
        j, res = self.go(1, result={"plan_hash": "other", "summary": {"fail_reason": "x"}}, stamp=False)
        self.assertEqual(res, ("failed", "finished"))

    def test_stale_result_deleted_before_inspect(self):  # 이전 차단 결과가 남아 있어도 크래시는 차단이 아니다
        stale = {"plan_hash": "STAMP", "summary": {"fail_reason": "옛 사유"}}
        j, res = self.go(1, stale=stale)
        self.assertEqual(res, ("failed", "finished"))
        self.assertFalse((self.tmp / "plans" / "점검결과.json").exists())

    def test_rc2_failed(self):
        j, res = self.go(2)
        self.assertEqual(res, ("failed", "finished"))
        self.assertEqual(self.outputs(j), 0)

    def test_timeout_failed(self):
        j, res = self.go(0, sleep=30, timeout=1)
        self.assertEqual(res, ("failed", "finished"))
        self.assertEqual(self.outputs(j), 0)

    def test_manual_skips_inspect(self):
        self.write_plan()
        j = self.add_job(exec_mode="manual")
        queue.claim_next(self.conn)
        stub = self.tmp / "handoff.py"
        stub.write_text("import sys, pathlib; pathlib.Path(sys.argv[2]).parent.joinpath('캡컷패키지').mkdir()",
                        encoding="utf-8")
        bad = self.tmp / "bad_inspect.py"
        bad.write_text("import sys; sys.exit(1)", encoding="utf-8")
        res = runner.run_job(self.conn, j, self.tmp, engine=stub, inspect_engine=bad)
        self.assertEqual(res, ("review", "held_human"))


if __name__ == "__main__":
    unittest.main()
