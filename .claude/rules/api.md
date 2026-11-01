---
paths:
  - "apps/api/**"
---
# API 영역 규칙 (apps/api/** 작업 때만 로드)
- 먼저 `~/.claude/skills/stack-fastapi/SKILL.md`를 읽는다.
- LLM·외부 API 키는 이 서버에만. 모델·제공자는 설정값으로(하드코딩 금지).
- 응답 형식이 바뀌면 `docs/04_API.md` 해당 행과 웹 소비 타입을 같은 작업에서 맞춘다.
