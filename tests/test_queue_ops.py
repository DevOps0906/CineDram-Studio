"""대기 큐 사람 조작 테스트 (docs/14 §4, docs/16 ADR-005): 상태별 의미·한 트랜잭션·동시성·실행 중 취소."""
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_worker import WorkerTestBase, pid_alive  # noqa: E402
from worker import queue_ops as qo  # noqa: E402
from worker import worker as wk  # noqa: E402


class OpsBase(WorkerTestBase):
    def set_state(self, job_id, queue_state, state=None):
        self.c.execute("UPDATE jobs SET queue_state=? WHERE id=?", (queue_state, job_id))
        if state:
            self.c.execute("UPDATE jobs SET state=? WHERE id=?", (state, job_id))

    def order(self):
        return [r[0] for r in self.c.execute(
            "SELECT id FROM jobs WHERE queue_state IN ('waiting','paused') ORDER BY priority DESC, id")]

    def race(self, fn, job_id, n=2):
        """n 개 연결이 같은 조작을 동시에 부른다 → (성공 수, 실패 예외 목록)."""
        barrier = threading.Barrier(n)
        ok, errors = [], []

        def run():
            conn = wk.migrate_mod.connect(self.db)
            try:
                barrier.wait()
                fn(conn, job_id)
                ok.append(1)
            except qo.QueueOpError as e:
                errors.append(e)
            finally:
                conn.close()
        threads = [threading.Thread(target=run) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        return len(ok), errors


class TransitionTest(OpsBase):
    def test_pause_resume(self):
        j = self.add_job()
        self.assertEqual(qo.pause(self.c, j), "paused")
        self.assertEqual(self.row(j)[:2], ("paused", "queued"))
        self.assertIsNone(wk.claim(self.c, "w1", [self.root]))  # 일시정지는 집지 않는다
        self.assertEqual(qo.resume(self.c, j), "waiting")
        self.assertEqual(self.events(j)[-2][:2], ("waiting", "paused"))
        self.assertEqual(self.events(j)[-1][:2], ("paused", "waiting"))

    def test_pause_only_waiting(self):
        j = self.add_job()
        for qs in ("running", "held_human", "held_resource", "paused", "finished"):
            self.set_state(j, qs)
            with self.assertRaises(qo.InvalidTransition):
                qo.pause(self.c, j)
        self.set_state(j, "waiting")
        with self.assertRaises(qo.InvalidTransition):
            qo.resume(self.c, j)
        self.assertEqual(self.events(j), [])

    def test_reorder_changes_priority_only(self):
        a, b = self.add_job(), self.add_job()
        self.assertEqual(qo.reorder(self.c, b, 10), "waiting")
        self.assertEqual(self.row(b, "priority, queue_state, state"), (10, "waiting", "queued"))
        self.assertEqual(wk.claim(self.c, "w1", [self.root])["id"], b)
        self.assertIn("0→10", self.events(b)[0][2])
        self.set_state(a, "finished", "done")
        with self.assertRaises(qo.InvalidTransition):
            qo.reorder(self.c, a, 5)
        for bad in ("5", 1.5, True):
            with self.assertRaises(qo.QueueOpError):
                qo.reorder(self.c, b, bad)

    def test_move_tied_one_step(self):
        a, b, c = self.add_job(), self.add_job(), self.add_job()  # [A0,B0,C0]
        self.assertEqual(qo.move(self.c, c, "up"), "waiting")
        self.assertEqual(self.order(), [a, c, b])
        prios = [self.row(j, "priority")[0] for j in (a, c, b)]
        self.assertEqual(len(set(prios)), 3)  # 동률 해소
        self.assertIn("순서 변경 up", self.events(c)[-1][2])
        self.assertEqual(self.events(c)[-1][:2], ("waiting", "waiting"))
        self.assertEqual(wk.claim(self.c, "w1", [self.root])["id"], a)  # 워커도 같은 순서

    def test_move_down_top_and_paused(self):
        a, b, c = self.add_job(), self.add_job(), self.add_job()
        qo.pause(self.c, b)
        qo.move(self.c, a, "down")
        self.assertEqual(self.order(), [b, a, c])
        qo.move(self.c, c, "top")
        self.assertEqual(self.order(), [c, b, a])
        self.assertEqual(self.row(b)[0], "paused")  # 상태는 그대로

    def test_move_edges_noop_and_rejects(self):
        a, b = self.add_job(), self.add_job()
        before = len(self.events(a))
        qo.move(self.c, a, "up")
        qo.move(self.c, a, "top")
        qo.move(self.c, b, "down")
        self.assertEqual(self.order(), [a, b])
        self.assertEqual(len(self.events(a)), before)
        with self.assertRaises(qo.QueueOpError):
            qo.move(self.c, a, "left")
        with self.assertRaises(qo.JobNotFound):
            qo.move(self.c, 999, "up")
        for qs in ("running", "held_human", "held_resource", "finished"):
            self.set_state(b, qs)
            with self.assertRaises(qo.InvalidTransition):
                qo.move(self.c, b, "up")

    def test_move_keeps_other_groups(self):
        a, b = self.add_job(), self.add_job()
        h = self.add_job(priority=4)
        self.set_state(h, "held_human")
        qo.move(self.c, b, "up")
        self.assertEqual(self.row(h, "priority")[0], 4)

    def test_retry_failed_only(self):
        j = self.add_job()
        self.c.execute("UPDATE jobs SET queue_state='finished', state='failed', attempts=3, "
                       "finished_at='2026-01-01T00:00:00.000Z' WHERE id=?", (j,))
        self.assertEqual(qo.retry(self.c, j), "waiting")
        self.assertEqual(self.row(j, "queue_state, state, attempts, finished_at"), ("waiting", "queued", 0, None))
        self.assertEqual(self.events(j)[-1][:2], ("finished", "waiting"))
        for st in ("canceled", "done"):
            self.set_state(j, "finished", st)
            with self.assertRaises(qo.InvalidTransition):
                qo.retry(self.c, j)

    def test_not_found(self):
        for fn in (qo.pause, qo.resume, qo.cancel, qo.retry):
            with self.assertRaises(qo.JobNotFound):
                fn(self.c, 999)
        with self.assertRaises(qo.JobNotFound):
            qo.reorder(self.c, 999, 1)

    def test_event_failure_rolls_back(self):
        j = self.add_job()
        with mock.patch.object(qo, "_event", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                qo.cancel(self.c, j)
        self.assertEqual(self.row(j)[:2], ("waiting", "queued"))
        self.assertEqual(self.events(j), [])


class CancelTest(OpsBase):
    def test_waiting_like_cancel_immediate(self):
        for qs in ("waiting", "paused", "held_human", "held_resource"):
            j = self.add_job()
            self.c.execute("UPDATE jobs SET queue_state=?, hold_reason='사유' WHERE id=?", (qs, j))
            self.assertEqual(qo.cancel(self.c, j), "finished")
            self.assertEqual(self.row(j)[:4], ("finished", "canceled", None, None))
            self.assertEqual(self.events(j)[-1][:2], (qs, "finished"))
        self.assertIsNone(wk.claim(self.c, "w1", [self.root]))

    def test_finished_and_scheduled_rejected(self):
        j = self.add_job()
        for qs, st in (("finished", "done"), ("finished", "canceled"), ("finished", "failed"),
                       ("held_human", "scheduled"), ("finished", "scheduled"), ("held_human", "uploading")):
            self.set_state(j, qs, st)
            with self.assertRaises(qo.InvalidTransition):
                qo.cancel(self.c, j)
        self.assertEqual(self.events(j), [])

    def test_running_cancel_clears_lease_and_not_reclaimed(self):
        j = self.add_job()
        wk.claim(self.c, "w1", [self.root])
        qo.cancel(self.c, j)
        self.assertEqual(self.row(j, "queue_state, state, slot_id, lease_owner, lease_expires_at"),
                         ("finished", "canceled", None, None, None))
        self.assertIn("실행 중", self.events(j)[-1][2])
        self.assertFalse(wk.heartbeat(self.c, j, "w1", 60))  # 워커의 임대 확인은 실패
        self.assertEqual(wk.reclaim(self.c, self.lock, expired_only=False), [])
        # 이미 끝난 실행 결과 기록도 취소를 덮어쓰지 않는다
        self.assertFalse(wk.finish(self.c, {"id": j, "cmd": "render"}, "w1", 0, ""))
        self.assertEqual(self.row(j)[:2], ("finished", "canceled"))

    def test_running_cancel_kills_process_tree(self):
        pid_file = self.tmp / "child.pid"
        j = self.add_job(cfg={"sleep": 60, "child_pid_file": str(pid_file)})
        result = {}

        def run():
            w = wk.Worker(self.db, [self.root], timeout=60, engine_script=self.engine,
                          log_dir=self.tmp / "logs", heartbeat_seconds=0.2)
            w.conn = wk.migrate_mod.connect(self.db)
            w.lock = self.lock
            try:
                result["job"] = w.run_once()
            finally:
                w.conn.close()
        t = threading.Thread(target=run)
        t0 = time.monotonic()
        t.start()
        for _ in range(200):
            if pid_file.exists() and pid_file.read_text():
                break
            time.sleep(0.05)
        self.assertEqual(self.row(j)[0], "running")
        qo.cancel(self.c, j)
        t.join(30)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - t0, 30)
        self.assertIsNone(result["job"]["returncode"])
        self.assertTrue(result["job"]["canceled"])
        self.assertEqual(self.row(j)[:2], ("finished", "canceled"))
        ev = self.events(j)
        self.assertEqual([e[:2] for e in ev],
                         [("waiting", "running"), ("running", "finished"), ("finished", "finished")])
        self.assertEqual(ev[-1][2], "워커: 취소 확인, 프로세스 트리 종료")
        child = int(pid_file.read_text())
        for _ in range(50):
            if not pid_alive(child):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(child), "손자 프로세스가 살아 있다")


    def test_timeout_is_not_cancel(self):
        """시간 초과는 failed 로 기록하고 취소 이벤트를 남기지 않는다."""
        j = self.add_job(cfg={"sleep": 60})
        with self.assertNoLogs("worker", "INFO"):
            job = self.worker(timeout=1, heartbeat_seconds=0.2).run_once()
        self.assertIsNone(job["returncode"])
        self.assertFalse(job["canceled"])
        self.assertEqual(self.row(j)[:2], ("finished", "failed"))
        self.assertNotIn("워커: 취소 확인, 프로세스 트리 종료", [e[2] for e in self.events(j)])

    def test_cancel_logs_result_discarded(self):
        j = self.add_job(cfg={"sleep": 60})
        w = self.worker(heartbeat_seconds=0.2)
        real = wk.heartbeat

        def cancel_then_beat(conn, job_id, worker_id, lease_seconds):
            qo.cancel(self.c, job_id)
            return real(conn, job_id, worker_id, lease_seconds)
        with mock.patch.object(wk, "heartbeat", cancel_then_beat), \
                self.assertLogs("worker", "INFO") as logs:
            job = w.run_once()
        self.assertTrue(job["canceled"])
        self.assertTrue(any("임대 잃음/취소됨, 결과 버림" in m for m in logs.output))
        self.assertEqual(self.events(j)[-1][2], "워커: 취소 확인, 프로세스 트리 종료")


class ConcurrencyTest(OpsBase):
    def assert_one_wins(self, fn, job_id, events_before):
        ok, errors = self.race(fn, job_id, n=4)
        self.assertEqual(ok, 1)
        self.assertEqual(len(errors), 3)
        self.assertTrue(all(isinstance(e, qo.InvalidTransition) for e in errors))
        self.assertEqual(len(self.events(job_id)), events_before + 1)

    def test_pause_race(self):
        j = self.add_job()
        self.assert_one_wins(qo.pause, j, 0)
        self.assertEqual(self.row(j)[0], "paused")

    def test_resume_race(self):
        j = self.add_job()
        self.set_state(j, "paused")
        self.assert_one_wins(qo.resume, j, 0)

    def test_cancel_race(self):
        j = self.add_job()
        self.assert_one_wins(qo.cancel, j, 0)
        self.assertEqual(self.row(j)[:2], ("finished", "canceled"))

    def test_retry_race(self):
        j = self.add_job()
        self.set_state(j, "finished", "failed")
        self.assert_one_wins(qo.retry, j, 0)
        self.assertEqual(self.row(j)[:2], ("waiting", "queued"))

    def test_reorder_race_all_recorded(self):
        j = self.add_job()
        ok, errors = self.race(lambda conn, jid: qo.reorder(conn, jid, 7), j, n=4)
        self.assertEqual((ok, errors), (4, []))  # 순서 변경은 겹쳐도 모두 기록된다
        self.assertEqual(len(self.events(j)), 4)
        self.assertEqual(self.row(j, "priority")[0], 7)

    def test_move_race_serialized(self):
        a, b, c = self.add_job(), self.add_job(), self.add_job()
        ok, errors = self.race(lambda conn, jid: qo.move(conn, jid, "up"), c, n=2)
        self.assertEqual((ok, errors), (2, []))
        self.assertEqual(self.order(), [c, a, b])  # 두 번 다 한 칸씩
        self.assertEqual(len(self.events(c)), 2)

    def test_move_race_different_jobs_consistent(self):
        for _ in range(3):
            self.c.execute("DELETE FROM job_events")
            self.c.execute("DELETE FROM jobs")
            a, b, c = self.add_job(), self.add_job(), self.add_job()
            barrier = threading.Barrier(2)
            errors = []

            def run(jid):
                conn = wk.migrate_mod.connect(self.db)
                try:
                    barrier.wait()
                    qo.move(conn, jid, "up")
                except Exception as e:  # 스레드 예외를 본문에서 확인
                    errors.append(e)
                finally:
                    conn.close()
            ts = [threading.Thread(target=run, args=(j,)) for j in (b, c)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(30)
            self.assertEqual(errors, [])
            # b 먼저: [b,a,c]→[b,c,a] / c 먼저: [a,c,b]→[a,b,c]
            self.assertIn(self.order(), ([b, c, a], [a, b, c]))
            self.assertEqual((len(self.events(b)), len(self.events(c))), (1, 1))  # 둘 다 실제로 이동
            prios = [r[0] for r in self.c.execute("SELECT priority FROM jobs")]
            self.assertEqual(len(set(prios)), 3)

    def test_cancel_vs_claim(self):
        """집기와 대기 취소가 겹쳐도 결과는 둘 중 하나로 일관된다."""
        for _ in range(5):
            j = self.add_job()
            barrier = threading.Barrier(2)
            out = {}

            def do_claim():
                conn = wk.migrate_mod.connect(self.db)
                barrier.wait()
                out["claim"] = wk.claim(conn, "w1", [self.root])
                conn.close()

            def do_cancel():
                conn = wk.migrate_mod.connect(self.db)
                barrier.wait()
                out["cancel"] = qo.cancel(conn, j)
                conn.close()
            ts = [threading.Thread(target=do_claim), threading.Thread(target=do_cancel)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(30)
            self.assertEqual(out["cancel"], "finished")
            self.assertEqual(self.row(j, "queue_state, state, lease_owner"), ("finished", "canceled", None))
            moves = [e[:2] for e in self.events(j)]
            if out["claim"]:
                self.assertEqual(moves, [("waiting", "running"), ("running", "finished")])
            else:
                self.assertEqual(moves, [("waiting", "finished")])


if __name__ == "__main__":
    unittest.main()
