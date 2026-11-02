"""PreToolUse 훅: 승인은 사람만 — 에이전트의 승인 명령·승인 라우트 호출을 막는다.

stdin JSON의 Bash/PowerShell `command`, WebFetch `url`을 검사해 걸리면 stderr 사유 + exit 2(차단).
정책 수준 차단이다. `python -c`·직접 짠 스크립트 등 우회는 막지 못한다(docs/16 열린 질문 8).
"""
import json
import re
import sys

# 따옴표·이스케이프(bash \, PowerShell `, cmd ^)를 지운 뒤 검사한다: "approve"·'approve'·ap\prove·줄 이어쓰기.
STRIP = re.compile(r"[\"'\\`^]")
ENGINE = re.compile(r"shortsflow", re.I)
APPROVE_WORD = re.compile(r"(?<![A-Za-z0-9_])approve(?![A-Za-z0-9_])", re.I)
ROUTE = re.compile(r"/approve(?:-plan|-video)?\b", re.I)


def find_reason(payload):
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    for key in ("command", "url"):
        text = tool_input.get(key)
        if not isinstance(text, str):
            continue
        plain = STRIP.sub("", text)
        if ENGINE.search(plain) and APPROVE_WORD.search(plain):
            return "엔진 approve CLI"
        if ROUTE.search(plain):
            return "승인 라우트(/approve, /jobs/{id}/approve-plan·approve-video)"
    return None


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0
    reason = find_reason(payload)
    if reason:
        msg = f"차단: 승인은 사람만 합니다({reason}). 운영 화면 승인함에서 사람이 직접 승인하세요.\n"
        sys.stderr.buffer.write(msg.encode("utf-8"))  # Windows 기본 인코딩(cp949)과 무관하게 UTF-8
        sys.stderr.flush()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
