# NOW — 재진입 스냅샷 (30줄 이내, 매 작업 끝에 덮어쓴다)

- 갱신: 2026-11-02
- 현재 작업: 병렬 개발 2차 — 상태: 완료·커밋(CSRF, 큐 사람 조작, 제작 현황·승인함 GET 화면)
- 마지막으로 한 일: dev-5 큐 조작(worker/queue_ops.py), CSRF(server/csrf.py, 비GET은 require_csrf 강제), /status·/inbox(server/views.py, CINEDRAM_DB 환경변수)
- 다음 한 걸음: 큐 조작 POST(대기 큐 화면 /queue, connect_rw+queue_ops+require_csrf) · 승인함 승인 버튼(승인 경로 = challenger 먼저, approve는 사람만) · 만들기·원클릭 화면
- 열린 결정(사용자 확인 필요): mute 대사 컷 C5-9 처리, 자동 SRT의 C3-3 허용 폭, 음량·검은 화면 규칙 ID, LLM 방식·슬롯 병렬, 남의 콘텐츠 재사용 범위, 의역 범위, 구글 계정 수 상한, 한국어 서체, 토큰 사본 동기화(현재 시안 CSS를 /static에서 직접 서빙)
- 주의: 업로드·approve는 사람만. 캡처는 .captures/(삭제 예정). Codex 한도 2026-11-04까지 → 리뷰는 reviewer
- 팀장 판단 메모: 폼 상한(10MB·파일 5·필드 200)은 임시값, 영상 업로드 때 재정의. FastAPI 올릴 때 include_router 지연 객체(iter_route_contexts) 감시 테스트 재확인. 포커스 보존·드로어는 브라우저 수동 확인 필요(shot은 ?쿼리 URL 불가). 워커 로그 원본 본문 제외 여부 재검토
- SDLC 단계: 06 구현 (전체 완성도 약 49%, README 참조)
- 담당 모델/역할: builder·reviewer·challenger(Opus)
- 테스트 상태: 통과 (.venv unittest 231개, skip 3)
- 남은 작업: POST 화면(큐·승인·만들기), 6 LLM, 7 환경설정, 8 업로드, 자동 검수 2종(규칙 대기), 실제 소스 검증(개발 1)
