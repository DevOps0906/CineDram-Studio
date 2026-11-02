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


class WorkRootsEnvTest(unittest.TestCase):
    """CINEDRAM_ROOTS: 미설정이면 저장소 폴더·base 제한 없음, 설정이면 그 루트들 안에서만 허용."""

    def setUp(self):
        self.a = tempfile.TemporaryDirectory(prefix="루트A_")
        self.b = tempfile.TemporaryDirectory(prefix="루트B_")
        self.out = tempfile.TemporaryDirectory(prefix="밖_")
        self.saved = os.environ.pop(paths.ROOTS_ENV, None)

    def tearDown(self):
        os.environ.pop(paths.ROOTS_ENV, None)
        if self.saved is not None:
            os.environ[paths.ROOTS_ENV] = self.saved
        for d in (self.a, self.b, self.out):
            d.cleanup()

    def _set(self, *vals):
        os.environ[paths.ROOTS_ENV] = os.pathsep.join(vals)

    def _real(self, d):
        return Path(os.path.realpath(d.name))

    def test_unset_keeps_default(self):
        self.assertEqual(paths.configured_roots(), [])
        self.assertEqual(paths.default_root(), paths.DEFAULT_ROOT)
        paths.resolve_in(self.out.name, "x.txt")  # base 제한 없음(하위 호환)

    def test_blank_value_is_unset(self):
        self._set("", "  ")
        self.assertEqual(paths.work_roots(), [paths.DEFAULT_ROOT])

    def test_set_roots_and_default(self):
        self._set(self.a.name, self.b.name)
        self.assertEqual(paths.work_roots(), [self._real(self.a), self._real(self.b)])
        self.assertEqual(paths.default_root(), self._real(self.a))
        self.assertEqual(paths.resolve_in(self.b.name, "s/x.txt"), self._real(self.b) / "s" / "x.txt")
        os.makedirs(Path(self.a.name) / "작업")
        paths.resolve_in(Path(self.a.name) / "작업", "x.txt")  # 루트 아래 하위 폴더 base 허용

    def test_set_rejects_base_outside_roots(self):
        self._set(self.a.name)
        for base in (self.out.name, paths.DEFAULT_ROOT, Path(self.a.name).parent):
            with self.subTest(base=base), self.assertRaises(paths.PathRejected):
                paths.resolve_in(base, "x.txt")
        os.makedirs(Path(self.out.name) / "원본")
        with self.assertRaises(paths.PathRejected):
            paths.resolve_source(self.out.name, "a.mp4")

    def test_set_rejects_prefix_sibling(self):
        sib = self.a.name + "x"
        os.makedirs(sib)
        try:
            self._set(self.a.name)
            with self.assertRaises(paths.PathRejected):
                paths.resolve_in(sib, "x.txt")
        finally:
            os.rmdir(sib)

    def test_relative_db_rejected(self):
        saved = os.environ.pop(paths.DB_ENV, None)
        try:
            os.environ[paths.DB_ENV] = "var/cine.db"
            with self.assertRaises(paths.PathRejected):
                paths.db_path_from_env()
            os.environ[paths.DB_ENV] = str(Path(self.a.name) / "cine.db")
            self.assertEqual(paths.db_path_from_env(), Path(self.a.name) / "cine.db")
        finally:
            os.environ.pop(paths.DB_ENV, None)
            if saved is not None:
                os.environ[paths.DB_ENV] = saved

    def test_relative_root_rejected(self):
        self._set("상대/경로")
        with self.assertRaises(paths.PathRejected):
            paths.work_roots()
        with self.assertRaises(paths.PathRejected):
            paths.resolve_in(self.a.name, "x.txt")


if __name__ == "__main__":
    unittest.main()
