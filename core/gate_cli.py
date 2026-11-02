"""엔진 gate 를 별도 프로세스에서 실행하는 진입점 (개발 3 C5). 서버·워커 프로세스는 엔진을 import 하지 않는다.

사용: python core/gate_cli.py <컷표.json>
종료 코드: 0 통과 · 1 미승인/불일치(사유는 JSON reason) · 2 읽기 오류.
stdout 마지막 줄은 JSON 한 줄: {"ok": bool, "plan_hash": str|null, "reason": str}
"""
import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "engine" / "shortsflow.py"


def run_gate(plan_file):
    spec = importlib.util.spec_from_file_location("shortsflow_engine", ENGINE)
    eng = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = eng
    spec.loader.exec_module(eng)
    plan = eng.load_plan(plan_file)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            ok = eng.gate(plan)
        except (ValueError, OSError) as e:  # 잘린·깨진 기획승인.json = 미승인
            ok = False
            print(f"✗ [P0-1] 기획승인.json 을 읽을 수 없다(미승인으로 취급): {e!r}")
    return ok, plan["_hash"], buf.getvalue().strip()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(json.dumps({"ok": False, "plan_hash": None, "reason": "사용법: gate_cli <컷표.json>"}, ensure_ascii=False))
        return 2
    try:
        ok, h, reason = run_gate(argv[0])
    except Exception as e:
        print(json.dumps({"ok": False, "plan_hash": None, "reason": f"컷표 읽기 실패: {e!r}"}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": ok, "plan_hash": h, "reason": reason}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(main())
