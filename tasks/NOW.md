# NOW — 재진입 스냅샷 (30줄 이내, 매 작업 끝에 덮어쓴다)

- 갱신: 2026-11-03
- 현재 작업: 없음 — 병행 구현 정리 완료·푸시(2f373df). 다른 세션 브랜치 `session/parallel-dev-2026-11-02`와 비교해 이 master를 정본으로 확정, 필수·권장 이식(해시 고정·CINEDRAM_ROOTS/DB·보안 3종·C3-3·문서 정합) 반영. 선택 이식(attach_plan·0004 임대 필드·docs/14 한 줄)은 보류
- 다음 후보(모두 사람 결정·준비 필요):
  1. task-008 개발 1: 실제 자체 보유(own) 소스 1편 + SRT 경로 → check→recalc→render→inspect, 음량 허용 오차·LRA·dynamic 허용·true peak(AAC 뒤) 실측. approve는 사람.
  2. task-013 개발 6: LLM 방식 확정(헤드리스 유지+API 보조 / API 전환), 헤드리스 자동 호출 이용 조건 근거 — 위험 높음, challenger 먼저.
  3. 그 뒤 개발 7(환경설정·레이더)·개발 8(채널·업로드, S4: 업로드 직전 outputs.sha256 재검사 필수).
- 열린 결정(사용자): LLM 방식(착수 때 "승인은 사람만" 기술 차단 함께 — docs/16 열린 질문 8), LLM 슬롯 병렬, RD3 KPI, 수정 요청 칩 미확인 2개(docs/13 §14), need_source→topic 폴더 감시, 음량 허용 오차·LRA
- 사람이 할 일: **VS Code 전체 재시작(ffmpeg PATH 반영 — Claude Code만 재시작하면 부족, 2026-11-02 확인)**, task-008용 own 영상+SRT 경로 준비, GitHub Code security 4개, python 스텁 끄기
- 주의: 실행은 .venv\Scripts\python(설치 `pip install --require-hashes -r requirements.txt -r requirements-dev.txt`). 작업 루트·DB는 CINEDRAM_ROOTS·CINEDRAM_DB(절대 경로). 외부 강의 캡처는 .captures/(git 제외, 삭제 예정). 새 마이그레이션은 0004부터, B 브랜치의 C10-4·5·0002는 가져오지 않음. 재시작 전엔 ffmpeg bin(C:\Users\User\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin)을 PATH 앞에. approve CLI·8765 curl·studio.db sqlite3는 권한 거부. 375px은 shot 한계 → 500px.
- 정본 계획: tasks/plan-dev0-2.md · plan-dev3.md · plan-dev4.md · plan-dev5.md
- 테스트 상태: 454 통과(skip 3), secscan 취약점 0, README 완성도 약 52%
