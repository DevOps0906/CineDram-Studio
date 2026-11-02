# 작업 표

| 번호 | 제목 | 웨이브 | 선행 | 위험도 | 담당 | 상태 |
|---|---|---|---|---|---|---|
| task-002 | 결정 기록: FastAPI 확정·사용자 1명·팀장 결정 3건(docs/16) | W1 | — | 낮음 | 팀장 | 완료 |
| task-003 | 보안 기본 L1: Dependabot·SECURITY.md (requirements.txt 생긴 뒤 secscan setup) | W3 | task-005 | 낮음 | 팀장(/secure) | 완료 |
| task-004 | 화면 필수 구조 검토(진짜 11·오탐 7, tasks/review-task-004.md). 결정: ①탭=필터 ③CH1→목록 ④ST4 명시 저장+확인, ②RD3 보류 | W1 | — | 낮음 | planner | 완료 |
| task-010 | 개발 3: 만들기·원클릭·제작 현황·승인함 화면(approve 경로에서 jobs.plan_hash 갱신·source_video 검사 포함) | W4 | task-006·007·009 | 보통(승인 경로 높음) | builder | 완료 |
| task-009 | 화면 표 보완(task-004 진짜 누락 11건 반영, docs/13·14). 남은 plan2spec 🔴는 도구 어휘 한계·CH1 의도적 예외 | W4 | task-004 | 낮음 | planner | 완료 |
| task-005 | 개발 0: FastAPI 골격·core 공용 모듈·의존성 고정(정본 tasks/plan-dev0-2.md, challenger 반영). 리뷰 2건 반영, 테스트 103 통과 | W2 | task-002 | 높음 | builder | 완료(미커밋) |
| task-006 | 개발 2a: 보안 골격(ADR-003 체크리스트 1~7). 0.0.0.0 실기동 차단 확인, 리뷰 반영(본문 상한·CSRF TTL·보안 헤더) | W3 | task-005 | 높음 | builder | 완료(미커밋) |
| task-007 | 개발 2b: 상태머신·큐 집기·최소 워커(GPU 슬롯 1, 포트 없음). 리뷰 반영(poison job·출력 마스킹·source_video 경로), 통합 테스트 166 통과 | W3 | task-005 | 높음 | builder·tester | 완료(미커밋) |
| task-011 | 개발 4: 자동 검수 6가지(docs/13 §10) — 규칙 A10-12~18 확정(2026-11-02, -14 LUFS·-1 dBTP, 무음 0.3초·첫 소리 0.1초·검은 화면 1프레임), rules 반영 중 | W5 | task-010·규칙 반영 | 보통 | planner→builder | 완료 |
| task-012 | 개발 5: 대기 큐 화면(R16~R20, 실행 중 취소) — 정본 tasks/plan-dev5.md. 테스트 361 | W5 | task-010 | 보통 | builder | 완료 |
| task-013 | 개발 6: LLM 연동(ADR-002) + llm_calls + 한도 | W6 | 사용자 결정: LLM 방식(헤드리스 유지/API 전환)·헤드리스 이용 조건 근거(docs/16 열린 질문 2·7) | 높음 | builder | 보류 |
| task-008 | 개발 1: 실제 소스 1편 검증(권리 own, approve는 사람) — 음량 허용 오차·LRA·true peak 실측 포함 | — | 사람 소스 준비(ffmpeg 준비됨) | 낮음 | 사람 | 보류 |

상태: 대기 / 진행 / 완료 / 보류

## 발견한 문제 (범위 밖 — 나중 작업 후보)
| 날짜 | 위치 | 내용 | 제안 |
|---|---|---|---|
| 2026-11-02 | ~/.claude/nomad/bin/sql2erd | ALTER TABLE ADD COLUMN 미반영 → erd.mmd에 jobs.plan_path 없음, python3 스텁으로 bin 실행 실패 | NOMAD 본체 도구 수정 |
| 2026-11-02 | ~/.claude/nomad/bin/plan2spec | input·select·note·row·`button:더 보기` 등 블록 어휘를 몰라 필수 구조 🔴 오탐(18건 유지) | NOMAD 본체 어휘 보강 |
| 2026-11-02 | ~/.claude/nomad/bin/py/shot.py | 500px 미만은 file:// iframe 래퍼로 찍는데 CSP frame-ancestors 'none'·X-Frame-Options DENY인 페이지는 깨진 아이콘만 나옴 → 375 모바일 확인 불가 | 도구를 `--force-device-scale-factor=2 --window-size=750`(CSS 375px) 방식으로 바꾸기(NOMAD 본체) |
| 2026-11-02 | `.captures/`(숨김, git 제외) | 외부 강의 캡처 25장(중복 1장 `_중복_images2/` 포함). 프로그램 완성·안정화 후 삭제 예정 | 삭제 전 `grep -rn "\.captures/" docs design`로 근거 링크(docs/13·15, design/DESIGN.md·tokens.css)를 글 설명으로 바꾼 뒤 폴더·`.gitignore` 줄 삭제 |
| 2026-11-02 | worker/__main__.py | 기동 거부 메시지가 stderr cp949로 출력(테스트는 PYTHONIOENCODING으로 우회) | 서버가 워커를 자식으로 띄울 때 인코딩 고정 |

## 교훈 (다음 작업에 그대로 적용할 것만, 20줄 이내 유지)
- Windows에서 python은 Store 스텁이 잡힌다 → `C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe` 또는 `.venv` 사용
- plan2spec은 `design/wireframes/index.html`을 덮어쓴다 → 실행 후 `git checkout` 으로 복구, 생성본은 `_spec-index.html`
