"""server/security.py — ADR-003 체크리스트 1~7 거부 테스트 (표준 라이브러리만)."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import security as sec  # noqa: E402
from server.security import SecurityError  # noqa: E402

PORT = 8765
FAKE_KEYS = [
    "sk-ant-api03-" + "A" * 40,
    "sk-proj-" + "b" * 40,
    "AIza" + "C" * 35,
    "ya29." + "d" * 40,
    "ghp_" + "E" * 36,
    "GOCSPX-" + "f" * 28,
    "1//" + "g" * 40,
]


class BindHostTest(unittest.TestCase):  # 1
    def test_loopback_allowed(self):
        self.assertEqual(sec.validate_bind_host("127.0.0.1"), "127.0.0.1")

    def test_reject_other_bind(self):
        for host in ("0.0.0.0", "::", "", None, "192.168.0.10", "localhost"):
            with self.subTest(host=host), self.assertRaises(SecurityError):
                sec.validate_bind_host(host)


class HostHeaderTest(unittest.TestCase):  # 2
    def test_allowed(self):
        sec.check_host(f"127.0.0.1:{PORT}", PORT)
        sec.check_host(f"localhost:{PORT}", PORT)

    def test_case_and_outer_space_normalized(self):
        sec.check_host(f"LocalHost:{PORT}", PORT)
        sec.check_host(f" 127.0.0.1:{PORT} ", PORT)

    def test_reject_inner_space(self):
        for h in (f"localhost :{PORT}", f"local host:{PORT}", f"127.0.0.1: {PORT}"):
            with self.subTest(h=h), self.assertRaises(SecurityError):
                sec.check_host(h, PORT)

    def test_reject_other_host(self):
        for h in ("evil.com", f"evil.com:{PORT}", "127.0.0.1:9999", "127.0.0.1", "", None):
            with self.subTest(h=h), self.assertRaises(SecurityError):
                sec.check_host(h, PORT)


class MethodOriginTest(unittest.TestCase):  # 3
    def test_get_passes(self):
        self.assertEqual(sec.check_request_method("GET", None, PORT), "GET")

    def test_head_passes(self):
        self.assertEqual(sec.check_request_method("head", None, PORT), "HEAD")

    def test_post_same_origin(self):
        sec.check_request_method("POST", f"http://127.0.0.1:{PORT}", PORT)
        sec.check_request_method("POST", f"http://localhost:{PORT}", PORT)
        sec.check_request_method("post", f" HTTP://LOCALHOST:{PORT} ", PORT)

    def test_reject_origin_inner_space(self):
        with self.assertRaises(SecurityError):
            sec.check_request_method("POST", f"http://localhost :{PORT}", PORT)

    def test_reject_other_origin(self):
        for o in ("http://evil.com", f"http://127.0.0.1:{PORT + 1}", "null", "", None,
                  f"https://localhost:{PORT}"):
            with self.subTest(o=o), self.assertRaises(SecurityError):
                sec.check_request_method("POST", o, PORT)

    def test_reject_non_post_state_change(self):
        for m in ("PUT", "DELETE", "PATCH", "OPTIONS", ""):
            with self.subTest(m=m), self.assertRaises(SecurityError):
                sec.check_request_method(m, f"http://127.0.0.1:{PORT}", PORT)

    def test_reject_cors_header(self):
        self.assertTrue(sec.check_no_cors({"Content-Type": "text/html"}))
        with self.assertRaises(SecurityError):
            sec.check_no_cors({"Access-Control-Allow-Origin": "*"})


class CsrfTest(unittest.TestCase):  # 4
    def setUp(self):
        self.secret = sec.new_server_secret()
        self.token = sec.issue_csrf_token(self.secret, "sess-1")

    def test_valid(self):
        self.assertTrue(sec.verify_csrf_token(self.secret, "sess-1", self.token, self.token))

    def reject(self, msg, token, cookie="same", **kw):
        cookie = token if cookie == "same" else cookie
        with self.assertRaisesRegex(SecurityError, msg):
            sec.verify_csrf_token(self.secret, kw.pop("session", "sess-1"), token, cookie, **kw)

    def test_reject_missing(self):
        for t in (None, ""):
            with self.subTest(t=t):
                self.reject("토큰 없음", t)

    def test_reject_missing_cookie(self):
        for c in (None, ""):
            with self.subTest(c=c):
                self.reject("쿠키 없음", self.token, c)

    def test_reject_malformed(self):
        for t in ("abc", ".1.sig", "nonce..sig", "x.y", "a.b.c", "a.1.b.c"):
            with self.subTest(t=t):
                self.reject("형식 오류|불일치", t)

    def test_reject_non_ascii(self):
        nonce, issued, sig = self.token.split(".")
        self.reject("형식 오류", f"{nonce}.{issued}.{sig[:-1]}가")
        self.reject("형식 오류", self.token, self.token + "é")

    def test_reject_other_session(self):
        self.reject("불일치", self.token, session="sess-2")

    def test_reject_other_secret(self):
        with self.assertRaises(SecurityError):
            sec.verify_csrf_token(sec.new_server_secret(), "sess-1", self.token, self.token)

    def test_reject_tampered(self):
        nonce, issued, sig = self.token.split(".")
        self.reject("불일치", f"{nonce}.{issued}.{'0' * len(sig)}")
        self.reject("불일치", f"{nonce}.{int(issued) + 100}.{sig}")

    def test_reject_expired(self):
        t0 = 1_800_000_000
        tok = sec.issue_csrf_token(self.secret, "sess-1", now=t0)
        self.assertTrue(sec.verify_csrf_token(
            self.secret, "sess-1", tok, tok, now=t0 + sec.CSRF_MAX_AGE))
        self.reject("만료", tok, now=t0 + sec.CSRF_MAX_AGE + 1)
        self.reject("만료", tok, now=t0 - 10)  # 미래 발급 시각

    def test_reject_cookie_mismatch(self):
        other = sec.issue_csrf_token(self.secret, "sess-1")
        with self.assertRaises(SecurityError):
            sec.verify_csrf_token(self.secret, "sess-1", self.token, other)

    def test_cookie_samesite_strict(self):
        h = sec.csrf_cookie_header(self.token)
        self.assertIn("SameSite=Strict", h)
        self.assertIn("HttpOnly", h)
        self.assertNotIn("SameSite=None", h)


class ResponseKeyTest(unittest.TestCase):  # 5
    def test_clean_body_passes(self):
        sec.check_response_body('{"secret_ref": "cred:anthropic", "masked": "sk-…abcd"}')

    def test_word_boundary_no_false_positive(self):
        for s in ("task-" + "0f8c2b1e-6a3d-4e7f-9b2a-1c4d5e6f7a8b",
                  "disk-backup-2026-11-02-final-version-copy",
                  "rusk-" + "a" * 30, "x1//" + "b" * 40, "myAIza" + "C" * 35):
            with self.subTest(s=s[:12]):
                self.assertEqual(sec.find_secrets(s), [])
                sec.check_response_body('{"id": "%s"}' % s)

    def test_csrf_token_in_page_passes(self):
        tok = sec.issue_csrf_token(sec.new_server_secret(), "sess-1")
        sec.check_response_body(f'<input type="hidden" name="csrf_token" value="{tok}">')
        sec.check_response_body('{"csrf_token": "%s"}' % tok)
        sec.check_response_body(f"X-CSRF-Token: {tok}")

    def test_reject_key_in_body(self):
        for k in FAKE_KEYS + ['{"api_key": "abcdef1234567890xyz"}']:
            with self.subTest(k=k[:10]), self.assertRaises(SecurityError):
                sec.check_response_body(('{"value": "%s"}' % k).encode("utf-8"))


class LogMaskTest(unittest.TestCase):  # 6
    def test_no_key_left_after_mask(self):
        for k in FAKE_KEYS + ["Authorization: Bearer abcdefghijklmnop1234",
                              "password=hunter2hunter2xyz",
                              "secret: Zx9_qwertyuiop12",
                              '"client_secret": "abcDEF123456ghi"']:
            with self.subTest(k=k[:10]):
                line = f"호출 실패 key={k} 끝"
                masked = sec.mask_secrets(line)
                self.assertNotIn(k, masked)
                self.assertEqual(sec.find_secrets(masked), [])
                self.assertIn(sec.MASK, masked)

    def test_plain_text_unchanged(self):
        line = "작업 12 완료 (컷 5개, 41.2초)"
        self.assertEqual(sec.mask_secrets(line), line)


class PathLimitTest(unittest.TestCase):  # 7
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="경로_테스트_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = self.tmp / "작업"
        self.src = self.tmp / "원본"
        self.work.mkdir()
        self.src.mkdir()
        self.roots = [self.work, self.src]

    def test_inside_allowed(self):
        p = sec.resolve_within("회차1/컷표.json", self.roots)
        self.assertEqual(p, (self.work / "회차1" / "컷표.json").resolve())
        p = sec.resolve_within(str(self.src / "a.mp4"), self.roots)
        self.assertEqual(p, (self.src / "a.mp4").resolve())
        self.assertEqual(sec.resolve_within(str(self.src), self.roots), self.src.resolve())

    def test_reject_unc_and_device(self):
        for bad in (r"\\attacker\share\x.srt", "//attacker/share/x.srt",
                    r"\\?\C:\Windows\win.ini", r"\\.\pipe\x", "\\/attacker/x"):
            with self.subTest(bad=bad), self.assertRaises(SecurityError):
                sec.resolve_within(bad, self.roots)

    def test_reject_nt_device_prefix(self):
        for bad in (r"\??\C:\Windows\win.ini", "/??/C:/Windows/win.ini", r"\??\UNC\attacker\share\x",
                    r"\\?\UNC\attacker\share\x", "//?/C:/x", r"\\.\C:\x", "//./pipe/x", "\\\\.\\PhysicalDrive0"):
            with self.subTest(bad=bad), self.assertRaisesRegex(SecurityError, "NT·장치"):
                sec.resolve_within(bad, self.roots)

    @unittest.skipUnless(os.name == "nt", "드라이브 문자는 Windows 전용")
    def test_reject_other_drive(self):
        drive = Path(self.work).resolve().drive.upper()
        other = "Q:" if drive != "Q:" else "R:"
        for bad in (other + r"\x.srt", other + "x.srt"):
            with self.subTest(bad=bad), self.assertRaisesRegex(SecurityError, "드라이브"):
                sec.resolve_within(bad, self.roots)

    @unittest.skipUnless(os.name == "nt", "정션은 Windows 전용")
    def test_reject_junction_escape(self):
        import subprocess
        outside = self.tmp / "밖"
        outside.mkdir()
        link = self.work / "정션"
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                           capture_output=True)
        if r.returncode != 0 or not link.exists():
            self.skipTest("mklink /J 실패(파일 시스템·권한 제한) — 정션 탈출 미검증")
        with self.assertRaises(SecurityError):
            sec.resolve_within("정션/x.txt", self.roots)

    def test_reject_escape(self):
        outside = self.tmp / "밖.txt"
        for bad in ("../밖.txt", "회차1/../../밖.txt", str(outside),
                    os.path.abspath(os.sep), "", None, "a\x00b"):
            with self.subTest(bad=bad), self.assertRaises(SecurityError):
                sec.resolve_within(bad, self.roots)

    def test_reject_prefix_sibling(self):
        sibling = self.tmp / "작업2"
        sibling.mkdir()
        with self.assertRaises(SecurityError):
            sec.resolve_within(str(sibling / "x"), self.roots)

    def test_reject_symlink_escape(self):
        link = self.work / "링크"
        try:
            os.symlink(self.tmp, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("심볼릭 링크 생성 권한 없음")
        with self.assertRaises(SecurityError):
            sec.resolve_within("링크/밖.txt", self.roots)


if __name__ == "__main__":
    unittest.main()
