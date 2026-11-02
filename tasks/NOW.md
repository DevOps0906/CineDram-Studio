# NOW — 재진입 스냅샷 (30줄 이내, 매 작업 끝에 덮어쓴다)

- 갱신: 2026-11-02
- 현재 작업: 병렬 개발(개발 0·2·4단계) — 상태: 완료·커밋, 다음 CSRF·POST 라우트 → 만들기·승인함 화면
- 마지막으로 한 일: FastAPI 빈 서버+보안 미들웨어(dev-0), 보안 순수 함수·큐 워커·0002(dev-2), 자동 검수 4종(dev-4). 모두 challenger/reviewer 반영
- 다음 한 걸음: CSRF(쿠키 필수·12시간 만료, 함수는 server/security.py에 있음)+첫 POST 라우트 / 실제 소스 1편 검증(개발 1, approve는 사람)
- 열린 결정(사용자 확인 필요): mute 대사 컷을 C5-9 소리 구간으로 셀지, 자동 받아쓰기 SRT의 C3-3 허용 폭(지금 0.1초, docs/13 §10-2 미확인), 음량·검은 화면 규칙 ID, LLM 방식·슬롯 병렬, 남의 콘텐츠 재사용 범위, 의역 범위, 구글 계정 수 상한, 한국어 서체
- 주의: 업로드·approve는 사람만. 캡처는 숨김 폴더 .captures/(삭제 예정, tasks/README.md). Codex 한도 2026-11-04까지 → 리뷰는 reviewer
- 팀장 판단 메모: 워커 로그는 엔진 출력 전체를 로컬 저장(mask_secrets, git 제외) — 원본 본문 제외 여부 재검토. 엔진 ffmpeg 이름 실행(작업 폴더 exe 하이재킹 여지)은 엔진 범위 밖 후속
- SDLC 단계: 05 기술 설계 · 06 구현 (전체 완성도 약 47%, README 참조)
- 담당 모델/역할: builder·reviewer·challenger(Opus)
- 수정 파일: server/**, worker/**, engine/shortsflow.py, db/migrations/0002, tests/**, requirements*, docs/16, AGENTS.md, README.md
- 테스트 상태: 통과 (.venv unittest 166개, skip 3 = 음량·검은 화면·심볼릭 링크)
- 남은 작업: 개발 2 마무리(CSRF·POST), 3 화면, 5 큐 고도화, 6 LLM, 7 환경설정, 8 업로드, QA(디자인·접근성)
