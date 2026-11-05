# NOW — 재진입 스냅샷 (30줄 이내, 매 작업 끝에 덮어쓴다)

- 갱신: 2026-11-03(개발 6 1차)
- 현재 작업: 없음 — 개발 6 1차 완료·푸시(core/llm.py·0004 llm_calls·승인 차단 훅·ADR-002 API 키만). 2차 중 recover_stale 워커 기동 연결 완료(2026-11-06, .venv에서 test_core_llm 35·test_worker_queue 30 통과). 설계 초안 tasks/plan-dev6-2.md(사용자 확정 대기). 남은 2차: 워커 단계 topic·flow·plan·subtitle 연결(프롬프트·출력 스키마 설계), 화면
- 다음 후보(모두 사람 결정·준비 필요):
  1. task-008 개발 1: 실제 자체 보유(own) 소스 1편 + SRT 경로 → check→recalc→render→inspect, 음량 허용 오차·LRA·dynamic 허용·true peak(AAC 뒤) 실측. approve는 사람.
  2. task-013 개발 6 2차: 워커 단계·화면 연결. SRT는 job source에서 직접 읽어 권리-내용 결속, 실제 API 시험 호출은 사용자 키·비용 승인 후.
  3. 그 뒤 개발 7(환경설정·레이더)·개발 8(채널·업로드, S4: 업로드 직전 outputs.sha256 재검사 필수).
- 열린 결정(사용자): LLM 작업당 상한($2 잠정)과 max_tokens(8000, thinking 포함이라 잘림 위험) 조정, 기본 모델(claude-opus-5-5) 유지 여부, LLM 슬롯 병렬, RD3 KPI, 수정 요청 칩 미확인 2개(docs/13 §14), need_source→topic 폴더 감시, 음량 허용 오차·LRA
- 사람이 할 일: **VS Code 전체 재시작(ffmpeg PATH 반영 — Claude Code만 재시작하면 부족, 2026-11-02 확인)**, task-008용 own 영상+SRT 경로 준비, GitHub Code security 4개, python 스텁 끄기
- 주의: 실행은 .venv\Scripts\python(설치 `pip install --require-hashes -r requirements.txt -r requirements-dev.txt`). 작업 루트·DB는 CINEDRAM_ROOTS·CINEDRAM_DB(절대 경로). 외부 강의 캡처는 .captures/(git 제외, 삭제 예정). 새 마이그레이션은 0004부터, B 브랜치의 C10-4·5·0002는 가져오지 않음. 재시작 전엔 ffmpeg bin(C:\Users\User\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin)을 PATH 앞에. approve CLI·8765 curl·studio.db sqlite3는 권한 거부. 375px은 shot 한계 → 500px.
- 정본 계획: tasks/plan-dev0-2.md · plan-dev3.md · plan-dev4.md · plan-dev5.md
- 테스트 상태: 495 통과(skip 2, dev5 시간 assert 1건 간헐 — 단독 재실행 통과), secscan 취약점 0, README 완성도 약 53%
