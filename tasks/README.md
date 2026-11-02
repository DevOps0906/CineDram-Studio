# 작업 표

| 번호 | 제목 | 웨이브 | 선행 | 위험도 | 담당 | 상태 |
|---|---|---|---|---|---|---|
| task-002 | 결정 기록: FastAPI 확정·사용자 1명·팀장 결정 3건(docs/16) | W1 | — | 낮음 | 팀장 | 완료 |
| task-003 | 보안 기본 L1: Dependabot·SECURITY.md (requirements.txt 생긴 뒤 secscan setup) | W3 | task-005 | 낮음 | 팀장(/secure) | 완료 |
| task-004 | 화면 필수 구조 검토(진짜 11·오탐 7, tasks/review-task-004.md). 결정: ①탭=필터 ③CH1→목록 ④ST4 명시 저장+확인, ②RD3 보류 | W1 | — | 낮음 | planner | 완료 |
| task-010 | 개발 3: 만들기·원클릭·제작 현황·승인함 화면(approve 경로에서 jobs.plan_hash 갱신·source_video 검사 포함) | W4 | task-006·007·009 | 보통(승인 경로 높음) | builder | 대기 |
| task-009 | 화면 표 보완(task-004 진짜 누락 11건 반영, docs/13·14). 남은 plan2spec 🔴는 도구 어휘 한계·CH1 의도적 예외 | W4 | task-004 | 낮음 | planner | 완료 |
| task-005 | 개발 0: FastAPI 골격·core 공용 모듈·의존성 고정(정본 tasks/plan-dev0-2.md, challenger 반영). 리뷰 2건 반영, 테스트 103 통과 | W2 | task-002 | 높음 | builder | 완료(미커밋) |
| task-006 | 개발 2a: 보안 골격(ADR-003 체크리스트 1~7). 0.0.0.0 실기동 차단 확인, 리뷰 반영(본문 상한·CSRF TTL·보안 헤더) | W3 | task-005 | 높음 | builder | 완료(미커밋) |
| task-007 | 개발 2b: 상태머신·큐 집기·최소 워커(GPU 슬롯 1, 포트 없음). 리뷰 반영(poison job·출력 마스킹·source_video 경로), 통합 테스트 166 통과 | W3 | task-005 | 높음 | builder·tester | 완료(미커밋) |
| task-008 | 개발 1: 실제 소스 1편 검증(권리 own, approve는 사람) | — | 사람 소스 준비 | 낮음 | 사람 | 보류 |

상태: 대기 / 진행 / 완료 / 보류

## 발견한 문제 (범위 밖 — 나중 작업 후보)
| 날짜 | 위치 | 내용 | 제안 |
|---|---|---|---|
| 2026-11-02 | 이 PC | ffmpeg·ffprobe 없음 → 엔진 check 실제 실행 테스트 skip, render 불가 | ffmpeg 설치 후 test_engine_check_on_sample 확인 |
| 2026-11-02 | ~/.claude/nomad/bin/sql2erd | ALTER TABLE ADD COLUMN 미반영 → erd.mmd에 jobs.plan_path 없음, python3 스텁으로 bin 실행 실패 | NOMAD 본체 도구 수정 |
| 2026-11-02 | ~/.claude/nomad/bin/plan2spec | input·select·note·row·`button:더 보기` 등 블록 어휘를 몰라 필수 구조 🔴 오탐(18건 유지) | NOMAD 본체 어휘 보강 |
| 2026-11-02 | worker/__main__.py | 기동 거부 메시지가 stderr cp949로 출력(테스트는 PYTHONIOENCODING으로 우회) | 서버가 워커를 자식으로 띄울 때 인코딩 고정 |

## 교훈 (다음 작업에 그대로 적용할 것만, 20줄 이내 유지)
- Windows에서 python은 Store 스텁이 잡힌다 → `C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe` 또는 `.venv` 사용
- plan2spec은 `design/wireframes/index.html`을 덮어쓴다 → 실행 후 `git checkout` 으로 복구, 생성본은 `_spec-index.html`
