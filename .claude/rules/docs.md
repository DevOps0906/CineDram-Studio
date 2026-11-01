---
paths:
  - "docs/**"
  - "DESIGN.md"
---
# 문서 영역 규칙 (docs/** 작업 때만 로드)
- 기획 문서는 `~/.claude/skills/plan-docs/SKILL.md`의 체계·길이 상한을 따른다. 한 사실은 한 곳에만 쓰고 링크한다.
- 그림은 `docs/diagrams/*.mmd`(Mermaid)로만. 저장 후 `mmd-check` 통과.
- 디자인 수치는 토큰 파일이 정답이다. 토큰 표(`docs/DESIGN_TOKENS.md`)는 `design-preview --md`로 자동 생성되며 손으로 고치지 않는다. 브리프·설명은 `DESIGN.md`에.
