"""개발 4 통합: 실제 shortsflow render -> inspect -> 워커 전이 -> 승인함 점검 표시. 가짜 엔진 없음.
엔진 approve 는 셸이 아니라 엔진 함수(sf.main)로 부른다. ffmpeg 가 없으면 건너뛴다."""
import importlib.util
import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi.testclient import TestClient  # noqa: E402

from _sf import HAS_FFMPEG, ffmpeg, sf  # noqa: E402
from server.app import create_app  # noqa: E402
from worker import __main__ as wmain  # noqa: E402

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
_spec = importlib.util.spec_from_file_location("db_migrate", ROOT / "db" / "migrate.py")
migrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mod)
_ms = importlib.util.spec_from_file_location("make_sample", ROOT / "engine" / "examples" / "make_sample.py")
make_sample = importlib.util.module_from_spec(_ms)
_ms.loader.exec_module(make_sample)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe 가 PATH 에 없음")
class InspectFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="통합4_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "var").mkdir()
        self.db = self.tmp / "var" / "studio.db"
        migrate_mod.migrate(self.db, backup=False)
        self.conn = migrate_mod.connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO works(title) VALUES ('작품')")
        self.conn.execute("INSERT INTO sources(work_id, file_path, origin, rights_status) "
                          "VALUES (1, 'plans/p1/원본.mp4', 'local_file', 'own')")
        self.dir = self.tmp / "plans" / "p1"
        make_sample.main(self.dir)
        self.plan = self.dir / "컷표.json"
        app = create_app(port=PORT, db_path=self.db)
        app.state.root = self.tmp
        self.c = TestClient(app, base_url=BASE)
        self.addCleanup(self.c.close)

    def approve_and_enqueue(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sf.main(["tts-measure", str(self.plan)]), 0)
            self.assertEqual(sf.main(["approve", str(self.plan), "--by", "테스터"]), 0)
            h = sf.load_plan(self.plan)["_hash"]
        self.conn.execute(
            "INSERT INTO jobs(source_id, state, queue_state, exec_mode, plan_path, plan_hash) "
            "VALUES (1,'building','waiting','auto_render','plans/p1/컷표.json',?)", (h,))
        return self.conn.execute("SELECT MAX(id) FROM jobs").fetchone()[0]

    def run_worker(self):
        rc = wmain.main(["--once"], db_path=self.db, lock_path=self.tmp / "var" / "worker.lock", root=self.tmp)
        self.assertEqual(rc, 0)

    def row(self, jid):
        return tuple(self.conn.execute("SELECT state, queue_state FROM jobs WHERE id=?", (jid,)).fetchone())

    def card(self, jid):
        page = self.c.get(f"/inbox?job={jid}")
        self.assertEqual(page.status_code, 200)
        m = re.search(rf'<article class="card[^"]*" id="card-{jid}".*?</article>', page.text, re.S)
        self.assertIsNotNone(m, "승인함에 카드가 있어야 한다")
        return m.group(0)

    def test_real_render_inspect_review_and_inbox(self):
        jid = self.approve_and_enqueue()
        self.run_worker()
        self.assertEqual(self.row(jid), ("review", "held_human"))
        outs = self.conn.execute("SELECT sha256 FROM outputs WHERE job_id=?", (jid,)).fetchall()
        self.assertEqual(len(outs), 1)
        res = json.loads((self.dir / "점검결과.json").read_text(encoding="utf-8"))
        self.assertEqual(res["output"]["sha256"], outs[0][0])
        self.assertEqual(res["summary"]["fail"], 0)
        card = self.card(jid)
        self.assertNotIn("자동 점검 미실행", card)
        self.assertIn(f"통과 {res['summary']['ok']}", card)
        self.assertIn(f"경고 {res['summary']['warn']}", card)
        fixes = json.loads((self.dir / "작업기록.json").read_text(encoding="utf-8")).get("auto_fixes", [])
        if fixes:
            self.assertIn("자동 보정", card)

    def test_silent_head_blocks_inspect_without_output(self):
        # 첫 컷(원본 101.8초~)의 앞 1.7초 음성을 0 으로 만든 변형 원본 -> 첫 소리 지연
        src = self.dir / "원본.mp4"
        alt = self.dir / "무음.mp4"
        ffmpeg("-i", src, "-c:v", "copy", "-af", "volume=enable='between(t,101.5,103.5)':volume=0",
               "-c:a", "aac", alt)
        shutil.move(alt, src)
        jid = self.approve_and_enqueue()
        self.run_worker()
        self.assertEqual(self.row(jid), ("building", "held_human"))
        reason = self.conn.execute("SELECT hold_reason FROM jobs WHERE id=?", (jid,)).fetchone()[0]
        self.assertIn("점검 실패", reason)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outputs WHERE job_id=?", (jid,)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
