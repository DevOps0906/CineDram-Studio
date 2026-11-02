# NOW — 재진입 스냅샷 (30줄 이내, 매 작업 끝에 덮어쓴다)

- 갱신: 2026-11-02
- 현재 작업: 병렬 개발 4차 — 상태: 완료·커밋(컷표·다음 명령 지정, 상태 전이, 원클릭 /make/oneclick)
- 마지막으로 한 일: attach_plan(P0 보류 재지정 포함), render→building→review(render_count), check·recalc→다음 명령 대기, 영상 승인 시 큐 종료, 원클릭 화면(중복 제출 모드 충돌 409)
- 다음 한 걸음: 개발 6 LLM 연동 — challenger 먼저, "승인은 사람만" 기술 차단(에이전트 훅·헤드리스 도구에서 셸·웹 제외) 함께, LLM 방식(헤드리스 vs API) 사용자 확정 필요
- 열린 결정(사용자 확인 필요): LLM 방식·이용 조건·슬롯 수 설정 위치, C10-4·5 기준값(pix_th 0.10), 원클릭 제작 방식 목록 범위(docs/13 §4), 모드 변경 규칙(§15-18), 남의 콘텐츠 재사용 범위, 의역 범위, 구글 계정 수 상한, 한국어 서체, 토큰 사본 동기화
- 주의: 업로드·approve(기획)는 사람만 — 승인함 [승인]은 영상 승인(job_events만). 작업 루트 CINEDRAM_ROOTS, DB CINEDRAM_DB. 캡처는 .captures/(삭제 예정)
- 팀장 판단 메모: 승인 시 finished_at 미설정(approved는 종료 아님). C10-5 어두운 장면 오탐은 실제 원본으로 확인. views_queue·views_oneclick이 views·views_create 함수에 결합(공용 helper 정리 후보). shot은 ?쿼리 URL 불가
- SDLC 단계: 06 구현 (전체 완성도 약 52%, README 참조)
- 담당 모델/역할: builder·reviewer·challenger(Opus)
- 테스트 상태: 통과 (.venv unittest 331개, skip 1 = 심볼릭 링크 권한)
- 남은 작업: 6 LLM, 7 환경설정·레이더, 8 채널·업로드, 실제 소스 검증(개발 1)
