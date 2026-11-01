@AGENTS.md

# Claude Code 전용 (이 프로젝트)
- 팀 운영·위임 규격·위험도별 검증은 전역 `~/.claude/CLAUDE.md`를 따른다. 여기에는 이 프로젝트만의 차이만 적는다.
- 영역 규칙은 `.claude/rules/`에 경로 조건으로 있다(해당 파일을 다룰 때만 로드).
- 재진입: 새 세션은 `tasks/NOW.md`부터 읽는다.
