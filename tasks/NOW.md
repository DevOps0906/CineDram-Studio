# NOW — 재진입 스냅샷 (30줄 이내, 매 작업 끝에 덮어쓴다)

- 갱신: 2026-11-02
- 현재 작업: task-012 개발 5(대기 큐) 구현 완료 → reviewer(code)·tester(실행 중 취소 통합) 진행. task-011 개발 4는 규칙 A10-12~18 rules 반영 중(planner)
- 정본 계획: tasks/plan-dev3.md (§2 계약 R1~R15 · §4 승인 경로 · §7~§10 결정 기록)
- 마지막으로 한 일: U0a~U5 구현·통합, 리뷰 code·security·design 반영(S1 승인 파일 무효화, S2 ai_assistant 권리 요구, 접근성), README 완성도 약 49%
- 다음 한 걸음: 커밋 → 개발 4(자동 검수 6가지, docs/13 §10) · 개발 5(대기 큐 화면)
- 열린 결정(사용자): 실제 소스 1편(개발 1), LLM 방식, LLM 슬롯 병렬, RD3 KPI, 수정 요청 칩 미확인 2개(docs/13 §14), need_source→topic 폴더 감시
- 개발 8 요구: 업로드 직전 outputs.sha256 재검사(S4)
- 사람이 할 일: GitHub Code security 4개, python 스텁 끄기, **Claude Code 재시작(ffmpeg PATH 반영 — 그전엔 WinGet bin 경로를 PATH 앞에)**
- 주의: 실행은 .venv\Scripts\python. 375px은 shot 한계(CSP·XFO) → 500px로 확인. approve CLI·8765 curl·studio.db sqlite3는 권한 거부.
- 테스트 상태: 361 통과(skip 8 = 자동 검수 미구현 6·계약 2), ffmpeg 9.0.2 설치됨
