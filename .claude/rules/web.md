---
paths:
  - "server/templates/**"
  - "server/static/**"
  - "design/**"
---
# 화면 영역 규칙 (server/templates·static, design/ 작업 때만 로드)
- 서버 렌더 HTML(Jinja2) + `_app.css`·`_tokens.css` 재사용, 클라이언트 JS는 폴링·폼 보조만(ADR-001). Tailwind 빌드 없음.
- 토큰 정본은 `design/tokens.css`, `_tokens.css`는 수동 사본 — 둘이 같은지 테스트로 확인한다.
- 근거는 `spec <화면 ID>` + `design/wireframes/<ID>.html` + `design/DESIGN.md`(기획 문서 통독 금지).
- 상태(빈·로딩·오류·성공)를 spec대로 모두 구현하고, `shot <URL>`로 3구간(~767 / 768~1199 / 1200~)을 확인한다.
- 모든 POST 폼에 CSRF hidden 필드(`csrf_token` = `request.state.csrf_token`).
