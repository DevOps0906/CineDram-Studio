import copy
import io
import logging
import logging.config
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import logmask  # noqa: E402


class LogMaskTest(unittest.TestCase):
    def test_mask(self):
        cases = {
            "api_key=abcd1234efgh": "abcd1234efgh",
            "Authorization: Bearer abcdefgh12345678": "abcdefgh12345678",
            "Authorization: Basic dXNlcjpwYXNzd29yZA==": "dXNlcjpwYXNzd29yZA",
            'token: "xyz 98765 432"': "98765",
            "{'password': 'hun ter2 x'}": "ter2",
            "sk-ant-abcdefghijkl": "abcdefghijkl",
            "AIzaSyABCDEFGHIJKLMNOPQRSTU": "SyABCDEFGH",
            "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2": "a1B2c3D4e5F6",
            "AKIAABCDEFGHIJKLMNOP": "ABCDEFGHIJKLMNOP",
            "xoxb-123456789012-abcdefghijkl": "abcdefghijkl",
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJl": "c2lnbmF0dXJl",
        }
        for c, leak in cases.items():
            with self.subTest(c=c):
                out = logmask.mask(c)
                self.assertIn("***", out)
                self.assertNotIn(leak, out)

    def test_plain_untouched(self):
        self.assertEqual(logmask.mask("GET /healthz 200"), "GET /healthz 200")


class FactoryTest(unittest.TestCase):
    def setUp(self):
        self._orig = logging.getLogRecordFactory()
        logmask.install()
        logmask.install()  # 멱등
        self.buf = io.StringIO()

    def tearDown(self):
        logmask.uninstall()
        logging.setLogRecordFactory(self._orig)
        for n in ("uvicorn", "uvicorn.access", "uvicorn.error", "app.child"):
            lg = logging.getLogger(n)
            lg.handlers.clear()
            lg.propagate = True

    def test_idempotent_single_wrap(self):
        f = logging.getLogRecordFactory()
        self.assertFalse(getattr(f._inner, "_logmask", False))

    def test_args_shape_kept(self):
        r = logging.makeLogRecord({})
        r = logging.getLogRecordFactory()("n", logging.INFO, "p", 1, '%s - "%s %s" %d',
                                          ("1.2.3.4:5", "GET", "/x?api_key=SECRETVALUE", 200), None)
        self.assertEqual(len(r.args), 4)
        self.assertEqual(r.args[3], 200)
        self.assertNotIn("SECRETVALUE", r.getMessage())

    def test_secret_in_template_keeps_arg_count(self):
        r = logging.getLogRecordFactory()("n", logging.INFO, "p", 1, "token=%s %d", ("LEAKVALUE1", 5), None)
        self.assertEqual(len(r.args), 2)
        self.assertNotIn("LEAKVALUE1", r.getMessage())

    def test_uvicorn_access_config(self):
        from uvicorn.config import LOGGING_CONFIG
        logging.config.dictConfig(copy.deepcopy(LOGGING_CONFIG))
        lg = logging.getLogger("uvicorn.access")
        for h in lg.handlers:
            h.setStream(self.buf)
        lg.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET",
                "/p?api_key=SECRETVALUE123&token=TOKVALUE456", "1.1", 200)
        out = self.buf.getvalue()
        self.assertIn("GET", out)
        self.assertIn("200", out)
        self.assertNotIn("SECRETVALUE123", out)
        self.assertNotIn("TOKVALUE456", out)

    def test_child_logger_propagation_and_late_handler(self):
        root = logging.getLogger()
        h = logging.StreamHandler(self.buf)  # 부착 이후에 만든 핸들러
        root.addHandler(h)
        try:
            logging.getLogger("app.child").warning("password=hunter2value")
        finally:
            root.removeHandler(h)
        self.assertNotIn("hunter2value", self.buf.getvalue())
        self.assertIn("***", self.buf.getvalue())

    def test_exception_traceback(self):
        lg = logging.getLogger("app.child")
        lg.propagate = False
        lg.addHandler(logging.StreamHandler(self.buf))
        try:
            raise RuntimeError("failed api_key=TRACESECRET99")
        except RuntimeError:
            lg.exception("boom")
        out = self.buf.getvalue()
        self.assertIn("RuntimeError", out)
        self.assertNotIn("TRACESECRET99", out)


if __name__ == "__main__":
    unittest.main()
