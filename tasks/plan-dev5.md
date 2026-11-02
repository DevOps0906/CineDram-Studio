# 개발 5 계획 — 대기 큐 화면 (task-012 정본)

기준일 2026-11-02. 근거: docs/16 구현 순서 개발 5행, docs/14 §2 S-QU1·§4(queue_state 표·취소 의미), docs/01 §2, tasks/plan-dev3.md §2(R1~R15 계약)·§9 C12·C13.

## 1. 라우트 계약 (R16~R20, R14 확장) — plan-dev3 §2 표에 이어 붙는 정본
| ID | 메서드·경로 | 입력 | 전이 | 실패 |
|---|---|---|---|---|
| R16 | GET `/queue` (`?state=`, `?q=`, `?page=`), GET `/queue/fragment` | 필터·검색·더 보기 | 없음 | — |
| R17 | POST `/jobs/{id}/pause` | csrf_token, confirm | queue_state waiting → paused(state 유지) | 그 외 409 |
| R18 | POST `/jobs/{id}/resume` | csrf_token, confirm | paused → waiting | 그 외 409 |
| R19 | POST `/jobs/{id}/priority` | csrf_token, `dir`=up\|down | priority ±1(waiting·paused만) | 그 외 409, dir 이상 422 |
| R20 | POST `/jobs/{id}/retry` | csrf_token, confirm | failed/finished → 승인 해시 있으면 building/waiting, 없으면 topic/held_human | 그 외 409 |
| R14+ | POST `/jobs/{id}/cancel` | (기존) | **running도 허용** → canceled/finished. 워커가 다음 확인(≤2초)에 엔진 프로세스 트리를 종료 | done 409 |

## 2. 결정 (팀장, 2026-11-02)
- Q1 재시도(R20): docs/14 §4 "failed → queued"는 접수 단계(R3)가 없는 지금은 위처럼 대체. 워커가 집을 때 gate·해시를 다시 보므로 승인 우회는 없다.
- Q2 실행 중 취소: 서버는 DB만 바꾼다(canceled/finished). 워커는 엔진 실행 중 2초마다 자기 작업의 queue_state를 확인해 running이 아니면 `kill_tree` 후 이벤트만 남긴다(전이는 이미 조건부라 덮어쓰지 않음). 워커는 직렬 루프라 엔진이 끝나기 전 다음 작업을 집지 않는다 → GPU 슬롯 1 유지.
- Q3 LLM 슬롯: LLM 단계가 아직 없으므로 화면 표시는 "LLM 슬롯 0/1 · 미사용". 병렬 여부는 사용자 확정 대기 그대로.
- Q4 paused는 워커가 집지 않는다(waiting만 집음 — 이미 보장).

## 3. 작업 분해 (병렬, 쓰기 경로 분리)
| 단위 | 담당 | 쓰기 경로 | 완료 조건 |
|---|---|---|---|
| Q-A 워커 취소 | builder | core/subproc.py(`should_stop` 콜백 인자 추가, 기존 시그니처 유지), worker/runner.py, tests/test_worker_cancel.py | 실행 중 취소 → 손자 프로세스까지 종료·상태 canceled 유지·outputs 없음, 콜백 없는 기존 호출 동작 동일 |
| Q-B 큐 화면 | builder | server/routes/queue.py, server/templates/queue.html·_queue_list.html, server/static/queue.js, server/approval_service.py(R14 running 허용만), tests/test_queue_screen.py, tests/test_routes_contract.py(R16~R20 추가) | R16~R20 테스트(상태별 409·422·CSRF 403), 동시 R19 두 건 정합, 슬롯 표시, 빈·오류 상태, 폴링 접근성(개발 3 §10 규칙), shot 768·1280 + 500 |
- 변경 후: reviewer(code) → tester(실제 워커 프로세스로 실행 중 취소 통합 1건).
