"""T1: 토큰 정본(design/tokens.css) = 서버 사본(server/static/_tokens.css) = 시안 사본(design/wireframes/_tokens.css)."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANON = ROOT / "design" / "tokens.css"
SERVER = ROOT / "server" / "static" / "_tokens.css"
WIRE = ROOT / "design" / "wireframes" / "_tokens.css"
COPY_HINT = "복사: cp design/wireframes/_tokens.css server/static/_tokens.css (정본 design/tokens.css 를 바꿨다면 시안 사본부터 맞춘다)"


def declarations(path):
    """(블록 머리글, 변수명) -> 값. 정본의 `@theme`는 사본의 `:root`와 같은 블록으로 본다."""
    text = re.sub(r"/\*.*?\*/", "", Path(path).read_text(encoding="utf-8"), flags=re.S)
    out, stack = {}, []
    for m in re.finditer(r"([^{};]*)\{|\}|(--[\w-]+)\s*:\s*([^;]+);", text):
        if m.group(0) == "}":
            stack.pop()
        elif m.group(2):
            ctx = " / ".join(s for s in stack if s not in ("@theme", ":root")) or "root"
            out[(ctx, m.group(2))] = " ".join(m.group(3).split())
        else:
            stack.append(" ".join(m.group(1).split()))
    return out


class TokensSyncTest(unittest.TestCase):
    def test_parse_finds_tokens(self):
        d = declarations(CANON)
        self.assertGreater(len(d), 40)
        self.assertIn(("root", "--color-primary"), d)
        self.assertTrue(any(ctx.startswith(".dark") for ctx, _ in d))

    def test_server_copy_matches_canon(self):
        self.assertEqual(declarations(CANON), declarations(SERVER), f"_tokens.css 가 design/tokens.css 와 다름. {COPY_HINT}")

    def test_wireframe_copy_matches_canon(self):
        self.assertEqual(declarations(CANON), declarations(WIRE), "시안 _tokens.css 가 정본과 다름")

    def test_server_copy_is_wireframe_copy(self):
        def norm(p):
            return p.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(norm(SERVER), norm(WIRE), COPY_HINT)

    def test_app_css_uses_no_hex_colors(self):
        css = (ROOT / "server" / "static" / "_app.css").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", css), "_app.css 에 hex 색 금지(토큰만)")


if __name__ == "__main__":
    sys.exit(unittest.main())
