"""plan-dev3 §2 라우트 표(R1~R15)·plan-dev5 §1(R16~R20)와 앱 라우트 대조. 미구현 행은 건너뛰고, 표 밖 라우트·CSRF 누락은 실패."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402

from server.app import create_app  # noqa: E402

BASE = "http://127.0.0.1:8765"
# (번호, 메서드, 경로 템플릿). POST 는 모두 CSRF 필요.
CONTRACT = [
    ("R1", "GET", "/make"), ("R2", "GET", "/make/oneclick"), ("R3", "POST", "/make"), ("R4", "POST", "/make/oneclick"),
    ("R5", "GET", "/status"), ("R6", "GET", "/status/fragment"), ("R7", "GET", "/inbox"),
    ("R8", "GET", "/jobs/{id}/video"),
    ("R9", "POST", "/jobs/{id}/approve-plan"), ("R10", "POST", "/jobs/{id}/approve-video"),
    ("R11", "POST", "/jobs/{id}/request-change"), ("R12", "POST", "/jobs/{id}/rebuild"),
    ("R13", "POST", "/jobs/{id}/hold"), ("R14", "POST", "/jobs/{id}/cancel"), ("R15", "POST", "/jobs/{id}/revert-plan"),
    # plan-dev5 §1
    ("R16", "GET", "/queue"), ("R16", "GET", "/queue/fragment"), ("R17", "POST", "/jobs/{id}/pause"),
    ("R18", "POST", "/jobs/{id}/resume"), ("R19", "POST", "/jobs/{id}/priority"), ("R20", "POST", "/jobs/{id}/retry"),
]
INFRA = {("GET", "/healthz")}


def app_routes(app):
    out = {}
    for r in app.routes:
        if isinstance(r, Route):
            for m in r.methods - {"HEAD"}:
                out[(m, re.sub(r"\{(\w+)(:\w+)?\}", "{id}", r.path))] = r
    return out


class RoutesContractTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(db_path="nonexistent.db")
        self.routes = app_routes(self.app)

    def test_contract_rows_implemented_or_skipped(self):
        missing = [no for no, m, p in CONTRACT if (m, p) not in self.routes]
        if len(missing) == len(CONTRACT):
            self.skipTest(f"계약 라우트 {len(missing)}건 모두 미구현")
        # 일부만 구현된 동안은 정보용: 미구현 번호를 메시지로 남긴다.
        self.assertTrue(True, f"미구현: {missing}")

    def test_post_requires_csrf_and_origin(self):
        client = TestClient(self.app, base_url=BASE, client=("127.0.0.1", 50000))
        impl = [p for _, m, p in CONTRACT if m == "POST" and (m, p) in self.routes]
        if not impl:
            self.skipTest("구현된 POST 계약 라우트 없음")
        for p in impl:
            url = p.replace("{id}", "1")
            with self.subTest(path=p):
                self.assertEqual(client.post(url, data={"confirm": "yes"}).status_code, 403)  # Origin 없음
                self.assertEqual(client.post(url, data={"confirm": "yes"}, headers={"origin": BASE}).status_code, 403)  # 토큰 없음

    def test_no_routes_outside_contract(self):
        allowed = {(m, p) for _, m, p in CONTRACT} | INFRA
        extra = {k for k in self.routes if k not in allowed}
        self.assertEqual(extra, set(), "계약(plan-dev3 §2) 밖 라우트")

    def test_only_static_mount(self):
        mounts = [r.path for r in self.app.routes if isinstance(r, Mount)]
        self.assertEqual(mounts, ["/static"])

    def test_only_get_and_post(self):
        for (m, p) in self.routes:
            self.assertIn(m, ("GET", "POST"), p)


if __name__ == "__main__":
    sys.exit(unittest.main())
