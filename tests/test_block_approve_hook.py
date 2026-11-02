import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "block_approve.py"


def run_hook(payload):
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=data, capture_output=True,
                          text=True, encoding="utf-8", timeout=20)


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def fetch(url):
    return {"tool_name": "WebFetch", "tool_input": {"url": url, "prompt": "x"}}


class BlockApproveHookTest(unittest.TestCase):
    def assertBlocked(self, payload):
        r = run_hook(payload)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("승인은 사람만", r.stderr)

    def assertPassed(self, payload):
        r = run_hook(payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")

    def test_blocks_engine_approve_cli(self):
        self.assertBlocked(bash("python engine/shortsflow.py approve 컷표.json --by me"))
        self.assertBlocked(bash(r'.venv\Scripts\python.exe "engine\shortsflow.py" approve x.json'))
        self.assertBlocked({"tool_name": "PowerShell",
                            "tool_input": {"command": "python engine/shortsflow.py  APPROVE a.json"}})

    def test_blocks_quoted_and_escaped_approve(self):
        self.assertBlocked(bash('python engine/shortsflow.py "approve" a.json'))
        self.assertBlocked(bash("python engine/shortsflow.py 'approve' a.json"))
        self.assertBlocked(bash("python -X utf8 engine/shortsflow.py --verbose x approve a.json"))
        self.assertBlocked(bash("python engine/ShortsFlow.py ApPrOvE a.json"))
        self.assertBlocked(bash("python engine/shortsflow.py \\\n  approve a.json"))
        self.assertBlocked(bash("python engine/shortsflow.py ap\\prove a.json"))
        self.assertBlocked({"tool_name": "PowerShell",
                            "tool_input": {"command": "python engine\\shortsflow.py `\n approve a.json"}})
        self.assertBlocked({"tool_name": "PowerShell",
                            "tool_input": {"command": "& python 'engine/shortsflow.py' \"approve\" a.json"}})

    def test_passes_harmless_engine_commands(self):
        self.assertPassed(bash("cat engine/README.md"))
        self.assertPassed(bash("python engine/shortsflow.py recalc 컷표.json"))
        self.assertPassed(bash("grep -n approved_by engine/shortsflow.py"))

    def test_blocks_approve_routes(self):
        self.assertBlocked(bash("curl -X POST http://127.0.0.1:8765/jobs/3/approve-plan"))
        self.assertBlocked(bash("curl -X POST http://localhost:8765/jobs/3/approve-video"))
        self.assertBlocked(fetch("http://127.0.0.1:8765/jobs/7/approve-plan"))
        self.assertBlocked(fetch("http://127.0.0.1:8765/approve"))

    def test_passes_normal_commands(self):
        self.assertPassed(bash("python engine/shortsflow.py check 컷표.json"))
        self.assertPassed(bash("python -m unittest discover -s tests"))
        self.assertPassed(bash("cat tests/test_approval_path.py"))
        self.assertPassed(fetch("https://code.claude.com/docs/en/hooks.md"))
        self.assertPassed({"tool_name": "Read", "tool_input": {"file_path": "server/approval_service.py"}})

    def test_bad_input_passes(self):
        self.assertPassed("not json")
        self.assertPassed({"tool_name": "Bash", "tool_input": "x"})


if __name__ == "__main__":
    unittest.main()
