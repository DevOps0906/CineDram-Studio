# NOW — 재진입 스냅샷 (30줄 이내, 매 작업 끝에 덮어쓴다)

- 갱신: 2026-11-02
- 현재 작업: 개발 0단계(스택 ADR·엔진 테스트·DB 스키마) — 상태: 완료, 다음 2단계 서버·보안 골격 대기
- 마지막으로 한 일: docs/16 ADR 6건, 엔진 테스트 71개(통과), 승인 게이트 tts_hash 결함 수정, db/ 스키마·migrate.py·ERD, README 갱신
- 다음 한 걸음: FastAPI 확정 → 서버·보안 골격(개발 2단계) / 실제 소스 1편 검증(개발 1단계, 승인 approve는 사람)
- 열린 결정(사용자 확인 필요): 남의 콘텐츠 재사용 지원 범위, 폰 원격 접속, 의역 허용 범위, 구글 계정 수 상한, 한국어 서체, 글자 배율 기능, 모바일 더보기 메뉴, 구글 계정 탭 지시문 끊긴 뒷부분(업로드방식), 동시 제작 방식, LLM 호출 주체(헤드리스 vs API 키)
- 주의: 업로드·approve는 사람만(AGENTS.md). 외부 강의 캡처는 숨김 폴더 .captures/(.gitignore 제외). 승인 해시는 컷표만 묶으므로 채널 변경은 별도 대조 필요(docs/14 §6-1)
- SDLC 단계: 05 기술 설계 · 06 구현 초기 (전체 완성도 약 42%, README 참조)
- 담당 모델/역할: planner · designer(Opus), challenger(Opus)
- 수정 파일: docs/00~15, design/**, modes/topic-modes.md, README.md, AGENTS.md
- 테스트 상태: 통과 (unittest 71개, skip 6 = docs/13 §10 미구현 검사)
- 남은 작업: 기술 설계(API·ERD·ADR·스택), 구현 단계 1(상태머신·SQLite·만들기 화면), QA 리뷰(코드·디자인·접근성·보안)
