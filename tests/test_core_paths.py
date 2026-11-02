import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import paths  # noqa: E402


class PathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="경로 테스트_")
        self.root = Path(self.tmp.name)
        (self.root / "원본" / "sub").mkdir(parents=True)
        (self.root / "원본" / "a.mp4").write_bytes(b"x")
        self.outside = tempfile.TemporaryDirectory(prefix="밖_")

    def tearDown(self):
        self.tmp.cleanup()
        self.outside.cleanup()

    def test_ok(self):
        p = paths.resolve_source(self.root, "sub/b.txt")
        self.assertEqual(p.name, "b.txt")
        paths.resolve_in(self.root, "원본/./sub")

    def test_case_insensitive_compare_on_windows(self):
        if os.name != "nt":
            self.skipTest("Windows 전용")
        p = paths.resolve_source(str(self.root).upper(), "A.MP4")
        self.assertEqual(p.name.lower(), "a.mp4")

    def test_rejected_forms(self):
        bad = ["", "..", "../x", "sub/../../x", "sub\\..\\..\\x", "/etc/passwd", "\\x",
               "C:\\Windows", "C:relative", "c:foo", "\\\\server\\share\\x",
               "\\\\?\\C:\\x", "//server/share", "a.mp4:stream", "a.mp4::$DATA",
               "a\x00b", os.path.abspath(self.outside.name)]
        for rel in bad:
            with self.subTest(rel=rel):
                with self.assertRaises(paths.PathRejected):
                    paths.resolve_source(self.root, rel)

    def test_reserved_names_and_trailing(self):
        bad = ["CON", "con.txt", "sub/NUL", "aux.tar.gz", "COM1", "lpt9.log", "COM1 .txt",
               "a.", "sub/b ", "sub/x./y", "Con"]
        for rel in bad:
            with self.subTest(rel=rel):
                with self.assertRaises(paths.PathRejected):
                    paths.resolve_source(self.root, rel)
        paths.resolve_source(self.root, "console.txt")
        paths.resolve_source(self.root, "COM10.txt")

    def test_source_junction_distinct_message(self):
        if os.name != "nt":
            self.skipTest("Windows 전용")
        import shutil
        shutil.rmtree(self.root / "원본")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.root / "원본"), self.outside.name],
                           capture_output=True)
        if r.returncode != 0:
            self.skipTest("junction 생성 불가")
        try:
            with self.assertRaises(paths.PathRejected) as cm:
                paths.resolve_source(self.root, "a.mp4")
            self.assertIn("원본", str(cm.exception))
            self.assertIn("junction", str(cm.exception))
        finally:
            os.rmdir(self.root / "원본")

    def test_source_restricted_but_root_ok(self):
        (self.root / "다른").mkdir()
        paths.resolve_in(self.root, "다른")
        with self.assertRaises(paths.PathRejected):
            paths.resolve_source(self.root, "../다른")

    def test_junction_escape(self):
        link = self.root / "원본" / "link"
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), self.outside.name],
                               capture_output=True)
            if r.returncode != 0:
                self.skipTest("junction 생성 불가")
        else:
            try:
                os.symlink(self.outside.name, link)
            except OSError:
                self.skipTest("symlink 불가")
        with self.assertRaises(paths.PathRejected):
            paths.resolve_source(self.root, "link/x.txt")
        if os.name == "nt":
            os.rmdir(link)


if __name__ == "__main__":
    unittest.main()
