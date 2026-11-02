---
paths:
  - "server/**"
  - "worker/**"
  - "core/**"
---
# 서버·워커 영역 규칙 (server/·worker/·core/ 작업 때만 로드)
- 먼저 `~/.claude/skills/stack-fastapi/SKILL.md`를 읽는다. 스택·결정 정본은 `docs/16-기술설계ADR.md`.
- 실행·테스트는 `.venv\Scripts\python`(Store 스텁 금지). 새 의존성은 `requirements.in` → `requirements.txt` 정확 고정 + `secscan`.
- 보안 골격(ADR-003)을 약하게 하지 않는다: 127.0.0.1 고정, Host·Origin 정확 비교, POST+CSRF, 본문 상한. 경로는 `core.paths`, 서브프로세스는 `core.subproc`만 쓴다.
- 워커는 `server`를 import하지 않는다(공용은 `core/`). 엔진 출력·예외를 저장할 때는 `core.logmask.mask()`를 거친다.
- 업로드·approve는 사람만. 화면에서 approve 경로를 바꾸면 `jobs.plan_hash` 갱신과 `source_video` 검사를 함께 한다.
