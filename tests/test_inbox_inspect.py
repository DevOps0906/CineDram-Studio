"""inbox.inspect_for — 점검결과.json 읽기(개발 4 I)."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.deps import templates  # noqa: E402
from server.routes import inbox  # noqa: E402

OUT = {"file": "out.mp4", "size": 4, "sha256": "SHA"}


def good(**over):
    d = {"schema": 1, "plan_hash": "PH1", "output": dict(OUT),
         "summary": {"ok": 3, "warn": 1, "fail": 1, "auto_fixes": 2, "fail_reason": "무음 0.5s"}, "items": []}
    d.update(over)
    return d


class InspectForTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.d = self.root / "p1"
        self.d.mkdir()
        (self.d / "컷표.json").write_text("{}", encoding="utf-8")
        (self.d / "out.mp4").write_bytes(b"abcd")
        self.job = {"id": 1, "plan_path": "p1/컷표.json", "plan_hash": "PH1"}
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE outputs(id INTEGER PRIMARY KEY, job_id INT, render_ver INT, sha256 TEXT)")
        self.conn.execute("INSERT INTO outputs(job_id, render_ver, sha256) VALUES (1, 1, 'SHA')")

    def tearDown(self):
        self.conn.close()
        self._t.cleanup()

    def write(self, data):
        text = data if isinstance(data, str) else json.dumps(data)
        (self.d / "점검결과.json").write_text(text, encoding="utf-8")

    def call(self, job=None):
        return inbox.inspect_for(self.conn, self.root, job or self.job)

    def test_ok(self):
        self.write(good())
        self.assertEqual(self.call(), {"ok": 3, "warn": 1, "fail": 1, "fail_reason": "무음 0.5s", "auto_fixes": 2,
                                       "items": []})

    def test_missing_file(self):
        self.assertIsNone(self.call())

    def test_broken_json(self):
        self.write("{not json")
        self.assertIsNone(self.call())

    def test_schema_mismatch(self):
        for bad in (good(schema=2), good(summary={"ok": "3", "warn": 0, "fail": 0}), good(summary={"ok": 1}),
                    good(output={"file": "out.mp4", "sha256": "SHA"}), good(items=["x"]), "[]"):
            self.write(bad)
            self.assertIsNone(self.call(), bad)

    def test_hash_mismatch(self):
        self.write(good(plan_hash="OLD"))
        self.assertIsNone(self.call())

    def test_size_mismatch(self):
        self.write(good(output={**OUT, "size": 99}))
        self.assertIsNone(self.call())

    def test_sha256_mismatch(self):
        self.write(good(output={**OUT, "sha256": "OTHER"}))
        self.assertIsNone(self.call())
        self.write(good(output={"file": "out.mp4", "size": 4}))
        self.assertIsNone(self.call())

    def test_long_reason_clipped(self):
        long = "가" * 500
        self.write(good(summary={"ok": 0, "warn": 0, "fail": 1, "fail_reason": long},
                        items=[{"id": "silence", "level": "fail", "msg": long}]))
        r = self.call()
        self.assertEqual(r["fail_reason"], "가" * 200 + "…")
        self.assertEqual(r["items"][0]["msg"], "가" * 200 + "…")

    def test_unknown_item_id_fixed_label(self):
        self.write(good(items=[{"id": "auto_fix_mode<b>", "level": "fail", "msg": "x"},
                               {"id": "silence", "level": "zzz"}]))
        r = self.call()
        self.assertEqual(r["fail"], 1)
        self.assertEqual([i["label"] for i in r["items"]], ["기타 점검", "무음"])
        self.assertEqual(r["items"][1]["level"], "warn")

    def test_path_escape(self):
        self.write(good(output={**OUT, "file": "../x.mp4"}))
        self.assertIsNone(self.call())
        self.write(good())
        self.assertIsNone(self.call({"id": 1, "plan_path": "../p1/컷표.json", "plan_hash": "PH1"}))

    def test_macro_shows_auto_fixes(self):
        t = templates.env.from_string('{% from "_macros.html" import inspect_line %}{{ inspect_line(3, 1, 1, 2, "무음 0.5s") }}')
        html = t.render()
        self.assertIn("자동 보정 2건", html)
        self.assertIn("무음 0.5s", html)


if __name__ == "__main__":
    unittest.main()
