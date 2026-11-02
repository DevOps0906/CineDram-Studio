# 개발 3 구현 계획 (task-010 정본) — 만들기·원클릭·제작 현황·승인함

기준일 2026-11-02. 근거: docs/16 개발 3행·ADR-001·003·005, [docs/03](../docs/03-화면설계.md), [docs/13 §5·§9·§16](../docs/13-원클릭제작흐름.md), [design/DESIGN.md §3·§4](../design/DESIGN.md), [plan-dev0-2.md#리뷰 반영 결정](plan-dev0-2.md). 이 문서의 라우트 표(§2)가 API 계약 정본이다. 위험도: 화면·조회 보통, **승인 경로(§4) 높음** → 착수 전 challenger.

## 1. 범위와 범위 밖
| 범위 | 범위 밖 |
|---|---|
| S-MK(만들기)·S-OC1(원클릭)·제작 현황·승인함 4화면 + 서버 라우트 + 승인 경로 | LLM 호출, 받아쓰기·대본·컷표 자동 생성(개발 6) |
| AI 단계(받아쓰기·대본·제목·장면 맞추기·TTS)는 **사람·스킬이 만든 `컷표.json` 경로를 입력**으로 대신한다 | 업로드·예약 버튼의 실동작(개발 8). 카드의 [업로드 예약]은 비활성 표시만 |
| 작업 생성(`jobs`) → 제작 현황 표시 → 기획 승인 → 워커 제작 → 승인함 판정 | 레이더(개발 5), 관문 ①② 전용 화면, 링크로 만들기(단계 2), 자동 검수 6가지(개발 4), 로그인 |
- 원클릭 = 방식(`rule_set`)·소재·제작 모드(`exec_mode`)를 한 폼에서 받는 것. 사람 게이트(기획 승인·승인함)는 건너뛰지 않는다(docs/13 §9-1 안 A).
- 스택: FastAPI + Jinja2 서버 렌더, 클라이언트 JS는 폴링·폼 보조만(`.claude/rules/web.md`). 새 의존성 없음(폼은 `parse_qs` 공용 파서, C10).

## 2. 라우트·API 계약 (정본)
공통: 127.0.0.1:8765, Host·Origin 정확 비교(기존 `SecurityMiddleware`), 상태 변경은 POST + CSRF(폼 필드 `csrf_token` 또는 헤더 `X-CSRF-Token`), 본문 ≤ 1 MB. 모든 응답에 비밀·키·절대 경로 금지. HTML은 Jinja2 자동 이스케이프, `|safe` 금지.

| # | 메서드 | 경로 | 입력 | 응답 | 상태 변경 | CSRF |
|---|---|---|---|---|---|---|
| R1 | GET | `/make` | — | 200 HTML 만들기 폼(작품·원본 select) | 없음 | 폼에 토큰 발급 |
| R2 | GET | `/make/oneclick` | — | 200 HTML S-OC1 폼(방식 카드·제작 모드 3택) | 없음 | 폼에 토큰 발급 |
| R3 | POST | `/make` | `source_id` int, `rule_set` 허용 목록 값, `plan_path` 작업 루트 상대 경로, `exec_mode` ∈ {auto_render,manual,ai_assistant}(기본 auto_render), `topic_mode`·`speed`(fast/review)·`channel_id` 선택 | 303 → `/status#job-<id>`; 오류 422 폼 재렌더(필드별 메시지) | `jobs` 신규: queued → matching → topic(원본·자막 확인) 또는 need_source. `job_events` 기록 | 필요 |
| R4 | POST | `/make/oneclick` | R3와 동일 필드(핸들러 공용, 화면만 다름) | R3와 동일 | R3와 동일 | 필요 |
| R5 | GET | `/status` | — | 200 HTML 제작 현황(진행 중 n / 승인 대기 n) | 없음 | — |
| R6 | GET | `/status/fragment` | — | 200 HTML 조각(목록만, 3초 폴링용) | 없음 | — |
| R7 | GET | `/inbox` | `?job=<id>` 선택 | 200 HTML 승인함(`state=review` 목록 + 상세) | 없음 | 카드 폼 토큰 |
| R8 | GET | `/jobs/{id}/video` | `id` int | 200 `video/mp4`(Range 지원) / 404. `outputs.file_path`를 `core.paths`로 검사 | 없음 | — |
| R9 | POST | `/jobs/{id}/approve-plan` | `confirm=yes`, `gate_ok=yes`(관문 ①② 확인), hidden `plan_hash`(화면 표시 시점 컷표 해시, C6) | 303 → `/status`; 409 상태 불일치·권리 정본(`source_id`)·원본 경로·"내용이 바뀌었습니다", 422 엔진 거부(출력 꼬리 5줄, 마스킹) | topic → building(waiting), `plan_hash`·`tts_hash` 갱신, `approvals` 행(§4) | 필요 |
| R10 | POST | `/jobs/{id}/approve-video` | `confirm=yes`, hidden `plan_hash`·`output_id`·`sha256`, 체크 6칸 `chk_1`~`chk_6`(각 `yes`, 전부 필요) | 303 → `/inbox`; 409 상태·권리·원본·해시·산출물 sha256 불일치 | review → approved(§4) | 필요 |
| R11 | POST | `/jobs/{id}/request-change` | `note` ≤ 500자, `targets`(반복 필드, 수정 대상 칩) | 303 → `/inbox` | review → topic, 재승인 필요(§4). 메모는 `job_events.message`(마스킹) | 필요 |
| R12 | POST | `/jobs/{id}/rebuild` | `confirm=yes` | 303 / 409 | review → building(waiting). 현재 컷표 해시 = 승인 해시일 때만 | 필요 |
| R13 | POST | `/jobs/{id}/hold` | `confirm=yes` | 303 → `/inbox` | 상태 유지, `hold_reason='보관(권리 미확인)'` + 이벤트 | 필요 |
| R14 | POST | `/jobs/{id}/cancel` | `confirm=yes` | 303 / 409(실행 중) | queued~review → canceled, `queue_state=finished` | 필요 |
| R15 | POST | `/jobs/{id}/revert-plan` | `confirm=yes` (버튼: 제작 현황 "기획 수정으로 되돌리기") | 303 → `/status`; 409 상태 불일치 | building/held_human(보류) → topic. 재승인(R9) 필요, docs/01 §2·job-states.mmd 반영 | 필요 |
- 폼 파싱: `server/deps.py`의 `parse_qs` 기반 공용 파서만 쓴다(`python-multipart` 추가 없음, 본문 상한 재사용 — C10).
- 오류 형식: HTML 라우트는 폼 재렌더(상태 코드 + 사람 말 메시지), 예외 스택·경로·엔진 전체 출력 노출 금지.
- 존재하지 않는 id → 404. 전이는 **조건부 UPDATE**(`WHERE id=? AND state=?`)로 이중 클릭을 막는다(두 번째는 409). 전이와 `job_events`는 한 트랜잭션.
- `/docs`·`/openapi.json`은 계속 404(OpenAPI 없음). 이 표가 계약이고, `tests/test_routes_contract.py`가 표의 메서드·경로·CSRF 여부를 앱 라우트와 대조한다.
- 라우트 파일은 `server/routes/*.py`(각 `router` 속성). U0가 패키지 안 모듈을 자동 등록한다 → `app.py`는 U0만 수정.

## 3. 화면별 템플릿과 상태
위치: `server/templates/`(HTML)·`server/static/`(CSS·JS). 근거는 `spec <화면 ID>` + `design/wireframes/<id>.html`(create·oneclick·queue·inbox) + DESIGN.md §3.3·3.4·§4 패턴. 구성 블록은 docs/03·docs/13 §5 표 그대로(여기서 다시 쓰지 않는다).

| 화면 | 템플릿 | 빈 | 로딩 | 오류 | 성공 |
|---|---|---|---|---|---|
| 만들기 | `make.html` | 작품·원본 없음 → "원본 폴더를 먼저 등록" 안내 | 제출 버튼 스피너 + 중복 제출 잠금 | 필드별 메시지, 권리 미확인 배지(업로드 경로 잠김 안내), `원본 없음` → need_source로 접수 | 303 후 제작 현황에 카드 추가 |
| 원클릭 | `oneclick.html` | 소재 비움 → "비워 두면 후보를 제시(레이더는 개발 5)" 안내만 | 방식 카드 스켈레톤 | Aside 선택 + 미로그인 경고(환경설정 링크는 자리표시, docs/14 미구현) | 제작 현황에 작업 추가 |
| 제작 현황 | `status.html` + `_status_list.html` | "진행 중인 제작이 없습니다" + 만들기 링크 | 첫 렌더 스켈레톤 | 폴링 실패 시 마지막 목록 유지 + "갱신 실패 · 마지막 갱신 hh:mm:ss" 배너 | 5단계 진행바(DESIGN §3.4), 상태 배지 5종, 경과 시간 |
| 승인함 | `inbox.html` + `_approval_card.html` | "승인 대기 없음" | 눌린 버튼 스피너, 나머지 비활성(§3.3 배리언트) | 점검 ✗≥1 → [승인] 비활성 + 이유, 권리 미확인 → [승인] 대신 [보관], 409 메시지 | 승인 완료 표시 + 승인 로그(누가·언제·결과), [업로드 예약] 비활성(개발 8) |
- 공통 `base.html`(셸: 3구간 레이아웃 — 모바일 하단 탭 / 태블릿 접는 메뉴 / 데스크톱 좌 메뉴), `_macros.html`(배지·진행바·카드). 모든 POST 폼에 `<input type=hidden name=csrf_token value="{{ request.state.csrf_token }}">`.
- 폴링: `static/status.js` 3초, `document.hidden`이면 중지, 실패 시 지수 대기(최대 30초). 서버 푸시(SSE)는 범위 밖.
- 정적 파일: `server/static/_tokens.css`(사본)·`_app.css`·`status.js`·`inbox.js`. 정적 라우트는 이 디렉터리만 서빙(디렉터리 목록 끔, 경로 탈출 거부 테스트).
- **토큰 동기화 테스트** `tests/test_tokens_sync.py`: 정본 `design/tokens.css` = `server/static/_tokens.css` = `design/wireframes/_tokens.css`(줄바꿈 정규화 후 바이트 비교). 불일치 시 실패 메시지에 복사 명령을 적는다. docs/16 열린 질문 6은 "수동 사본 + 테스트"로 결정(T1).
- CSP(T7): `default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'`. 인라인 스크립트·스타일 금지, 시안의 인라인 JS는 `server/static/*.js`로 옮긴다.
- 접근성·반응형: 터치 44px(`--size-touch`), 상태는 색+아이콘+텍스트, `role="list"`·`aria-current="step"`. 3구간 폰트는 토큰으로만 지정.

## 4. 승인 경로 설계 (위험도: 높음)
승인에는 **두 가지 사람 게이트**가 있다. 이름을 섞지 않는다.
| 게이트 | 버튼 | 라우트 | 전이 (docs/01 §2) | 엔진 호출 |
|---|---|---|---|---|
| 기획 승인(P0) | [기획 승인] (제작 현황/관문 자리) | R9 | topic → building | `approve` (쓰기: `기획승인.json`) |
| 영상 승인 | [승인] (승인함) | R10 | review → approved | `approve` 호출 없음. 해시 대조만 `gate` 서브프로세스(C5) |

**결정 A: 엔진 `approve`는 서브프로세스로 호출한다** (`core.subproc.run([python, engine, "approve", 컷표, "--by", 고정값])`, 시간 제한 60초, 트리 종료).
- 이유: 엔진이 `기획승인.json`을 쓰는 유일한 길로 남는다(해시 계산 재구현 없음). 서버 프로세스에 `sys.exit`·stdout 오염·전역 상태가 새지 않는다. 워커의 엔진 호출과 같은 방식.
- 대안(함수 호출)은 기각: 엔진이 `print`로 출력하고 `load_plan`이 파일을 읽는 전역 상태를 쓰므로 리디렉션 코드가 필요하고, 서버 프로세스에서 예외가 곧 서버 오류가 된다.
- `--by`는 서버 설정 상수("사용자", 1인 도구). 요청 값을 받지 않는다. 종료 코드 0 → 성공, 1 → 422(점검 위반, 마스킹한 꼬리 5줄), 2·시간 초과 → 500 사람 말 메시지 + 로그 파일(`var/logs`).

**사람만 누를 수 있다는 보장** (`tests/test_approval_path.py`가 각 항목을 거부 테스트로 확인)
1. 승인 경로는 R9·R10 두 POST뿐이다. 엔진 `approve` 인자를 쓰는 서브프로세스 호출은 `server/approval_service.py` 한 곳. 워커·`core/`·다른 라우트에는 없다(소스 grep 테스트: `"approve"` 문자열 호출 위치 단언).
2. POST + CSRF 토큰(폼) + Origin 정확 일치 + Host 허용 목록(기존 미들웨어). GET·다른 메서드로는 상태가 바뀌지 않는다.
3. 폼 확인 필드 `confirm=yes`와, R9는 `gate_ok=yes`(관문 ①② 확인, T5), R10은 `chk_1`~`chk_6`이 모두 필요. 누락 시 422.
4. 자동 호출 경로 없음: 폴링·워커·스킬이 이 라우트를 호출하는 코드를 만들지 않는다. 같은 PC의 로컬 프로세스가 토큰을 얻어 POST 하는 위험은 **수용**(T2, ADR-003에 잔여 위험으로 기록).

**R9 기획 승인 순서** (앞 단계가 실패하면 뒤로 가지 않는다)
1. 작업 로드, `state='topic'` 확인(아니면 409). 폼 hidden `plan_hash`가 현재 컷표 해시와 다르면 409 "내용이 바뀌었습니다 — 다시 확인"(C6). 화면은 컷 수·길이·제목 요약을 보여 준다.
2. 공용 검사 `core/approval.py`(C2): `source_id` NULL → 409. `meta.source_video` 실경로(워커와 같은 기준 `resolve_in(컷표 폴더, …)` + 파일 존재)가 `sources.file_path` 실경로와 다르면 409. 권리 정본은 DB `sources.rights_status`이고 컷표 `meta.rights_status`는 무시한다. rights ∉ {own, licensed}여도 R9는 허용(T3).
3. 선점(C4): 엔진 호출 전 조건부 UPDATE로 `queue_state` held_human → running, `slot_id='approve'`(기존 슬롯 유일 인덱스로 직렬화). rowcount≠1이면 409. 활성 작업의 `plan_path` 중복은 `0003` 부분 유일 인덱스가 막는다.
4. 엔진 `approve` 서브프로세스. 실패하면 선점을 held_human으로 되돌리고 DB 해시는 바꾸지 않는다.
5. `기획승인.json`(정본)에서 `plan_hash`·`tts_hash`를 읽는다. 비었거나 잘렸거나 없으면 미승인으로 취급(gate 실패, C7) → 선점 복귀·500. 읽은 `plan_hash`가 1단계 hidden 값과 다르면 409(본 것=승인한 것, C6).
6. 한 트랜잭션(`BEGIN IMMEDIATE`): `UPDATE jobs SET state='building', queue_state='waiting', slot_id=NULL, plan_hash=?, tts_hash=?, hold_reason=NULL WHERE id=? AND state='topic' AND slot_id='approve'` (rowcount≠1 → 롤백·409) + `approvals` 행 + `job_events`.
7. 파일 앞섬(C7): 5단계 후 6단계 DB 쓰기가 실패하면 작업은 topic에 남고 **자동 승격하지 않는다**. 사람이 R9를 다시 누르면 approve가 파일을 다시 쓴다(멱등). 파일과 DB가 어긋나면 파일이 정본(ADR-004).
- 승인 뒤 컷표가 바뀌면 워커 D5 재검사가 `held_human`으로 막고, 사람은 R15로 topic에 돌아가 R9를 다시 한다.

**R10 영상 승인** (review + held_human에서만)
1. 공용 검사 `core/approval.py`(R9-2와 같은 함수): `source_id` NULL·`source_video` ≠ `sources.file_path` → 409, rights ∉ {own, licensed} → 409 `권리 미확인`(UI는 [승인]을 숨기고 서버가 최종 거부 — T3). 폼 `chk_1`~`chk_6`·`confirm`이 모두 있어야 한다.
2. 컷표 해시 대조는 **서브프로세스**(C5): `core.subproc`로 엔진 `gate`를 부르는 작은 진입점(U0 신설). 서버 프로세스 안에서 엔진 import·`redirect_stdout` 금지. 통과해도 `jobs.plan_hash`·폼 `plan_hash`와 다르면 409 "컷표가 승인 뒤 바뀜 — 수정 요청으로 재승인".
3. 산출물 대조(C1·C6): 폼 `output_id`의 `outputs` 행이 이 작업의 최신 행이고, 파일(mp4) 또는 패키지 폴더(파일 목록+내용)의 sha256을 서버가 다시 계산해 폼 `sha256`·`outputs.sha256`과 같아야 한다. 다르면 409(다시 렌더된 경우).
4. 전이: `review → approved`, `queue_state` 유지 `held_human`(`hold_reason='업로드 승인 대기'`, T6; `finished`는 done·failed·canceled 전용). 승인 로그에 점검 결과(없으면 "미실행")를 `job_events.message`로 남긴다.

**재승인·되돌리기 경로** (개발 2 리뷰의 개발 3 책임)
- R11 수정 요청: review → topic. 사람이 컷표를 고친 뒤 R9를 다시 눌러야 하며, R9 6단계가 `jobs.plan_hash`를 **새 승인 해시로 덮어쓴다**. 안 하면 워커가 영구 `held_human`.
- R12 다시 만들기: 해시가 같을 때만 review → building(waiting). 다르면 409 → 수정 요청 안내.
- R15 보류 탈출(C3): building/held_human → topic. 버튼은 제작 현황(U2), 핸들러는 U4.
- 전이 구현은 `server/jobs_repo.py`의 `transition_from(conn, job_id, expect_state, to_state, queue_state, ...)` 한 함수로만(조건부 UPDATE + `job_events`). 워커 `queue.transition`(running 전용)과는 별개.
- 단일 코드 소스: `core/approval.py`(신규)의 `read_approval_file`·`check_source_video`·권리/`source_id` 검사를 서버·워커가 함께 쓴다(T4, runner 호출부만 교체, 동작 동일 테스트 유지).

**위험 점검표 (challenger 질문 후보)**: 엔진 성공 후 DB 실패(파일 앞섬, 자동 승격 금지) · 이중 클릭·동시 승인(선점) · 승인 중 컷표 변경(hidden 해시 + D5) · `plan_path` 경로 탈출·중복 · 엔진 출력의 키 패턴(마스킹) · 권리 우회(UI 숨김만으로 안 막기) · 다시 렌더된 산출물을 승인(sha256).

## 5. 작업 분해
U0a·U0b가 선행이고 서로 병렬이다(쓰기 경로 분리). U1~U4는 둘 다 끝난 뒤 병렬(쓰기 경로 겹침 없음). U5는 마지막.
| 단위 | 담당 | 쓰기 경로 | 완료 조건 | 위험 / 검증 |
|---|---|---|---|---|
| U0a 코어·워커·DB | builder | `core/approval.py`(+ gate 서브프로세스 진입점 `core/gate_cli.py`), `worker/runner.py`·`worker/__init__.py`(`REQUIRED_SCHEMA`=3), `db/migrations/0003_*.sql`, `server/security.py`(CSP, T7), `tests/test_core_approval.py`, `tests/test_db_schema.py`·`test_worker_queue.py`·`test_integration_dev2.py`·`test_server_security.py`(기대값 갱신만) | `0003`: outputs에 sha256·mode·plan_hash 열(없는 것만) + 활성 작업 `plan_path` 부분 유일 인덱스; 워커 성공 시 `outputs` 행 기록(C1, auto_render=mp4 해시·그 외=폴더 목록+내용 해시); runner의 `source_video`·해시 검사를 `core/approval.py`·gate 서브프로세스로 교체(C5, T4)하고 기존 동작 테스트 통과; CSP 헤더 거부 테스트; `erd.mmd` 재생성 + `mmd-check`; 전체 `unittest` + `secscan` | **높음**(마이그레이션·워커·보안 헤더) · 착수 전 challenger 반영 확인, 변경 후 codex-reviewer(code) + reviewer(security) + tester |
| U0b 서버 셸 | builder | `server/app.py`, `server/deps.py`(C10 `parse_qs` 공용 파서·DB 의존성), `server/jobs_repo.py`, `server/routes/__init__.py`, `server/templates/base.html`·`_macros.html`(배지·진행바·카드·폼 매크로 전부, C9), `server/static/**`(`_tokens.css`·`_app.css`·공용 JS), `tests/test_tokens_sync.py`, `tests/test_server_shell.py`, `tests/test_routes_contract.py` | 라우트 자동 등록, 폼 파서(상한·잘못된 인코딩 거부), 정적 서빙 거부 테스트, 토큰 동기화 통과, 계약 대조 테스트(표 R1~R15, 미구현은 skip 표시), `/docs` 404 유지, 인라인 스크립트 없음, 매크로 렌더 테스트 | 보통 · codex-reviewer(code) |
| U1 만들기·원클릭 | builder | `server/routes/make.py`, `server/templates/make.html`·`oneclick.html`, `tests/test_make.py` | R1~R4 테스트(정상, `plan_path` 탈출, 잘못된 enum, CSRF 없음 403, need_source 분기, XSS 이스케이프), `shot` 3구간(375·768·1280) 첨부 | 보통 · codex-reviewer(code·design) |
| U2 제작 현황 | builder | `server/routes/status.py`, `server/templates/status.html`·`_status_list.html`, `server/static/status.js`, `tests/test_status.py` | R5·R6 테스트(상태→진행바 매핑 표 전부, 빈 상태, 권리 미확인 배지, 조각 응답) + **R9 폼 렌더**(`confirm`·`gate_ok`·hidden `plan_hash`·`csrf_token`, 컷 수·길이·제목 요약) + **R15 버튼**(building/held_human일 때만, `confirm`·`csrf_token`), `shot` 3구간 | 보통 · codex-reviewer(design) |
| U3 승인함 | builder | `server/routes/inbox.py`, `server/templates/inbox.html`·`_approval_card.html`, `server/static/inbox.js`, `tests/test_inbox.py` | R7·R8 테스트(배리언트 5종 렌더, 권리 미확인 → [승인] 없음·[보관], R10 폼 hidden `plan_hash`·`output_id`·`sha256`·`chk_1`~`chk_6`, R11 `targets` 칩, mp4면 재생·폴더면 파일 목록, 영상 경로 탈출 거부, Range), `shot` 3구간 + codex-reviewer(a11y) | 보통 · codex-reviewer(code·design·a11y) |
| U4 승인 경로 | builder | `server/routes/decisions.py`, `server/approval_service.py`, `tests/test_approval_path.py` | R9~R15 테스트: §4 보장 1~3 거부, `plan_hash` 갱신, `source_id` NULL·`source_video`≠`sources.file_path`·밖 경로 409, 권리 미확인 R10 409(R9 허용), hidden 해시 불일치 409, 산출물 sha256 불일치 409, 이중 클릭·동시 승인(선점) 1건만 성공, 파일 앞섬(DB 실패 주입 → topic 유지·자동 승격 없음), R15 보류 탈출, 재승인 뒤 워커가 집어 review까지 가는 통합 1건 | **높음** · 착수 전 challenger, 변경 후 codex-reviewer(code) + reviewer(security) + tester |
| U5 통합·검증 | tester | `tests/test_integration_dev3.py` | 만들기 → 기획 승인 → 워커 → 승인함 → 영상 승인 전체 흐름(합성 샘플 `make_sample.py`), `q python -m unittest discover -s tests` 통과, `secscan` ⚠ 없음, 3구간 `shot` 4화면 | 보통 · codex-reviewer(code·design) |
- 의존: U1~U3의 폼 action 문자열은 §2 표 경로를 쓴다(U4 완성 전에도 렌더 테스트 가능). 라우트·템플릿 파일이 모두 다르므로 병합 충돌 없음. 공용 매크로·CSS·JS·`app.py`·`jobs_repo.py`·`deps.py`·`worker/`·`security.py` 변경이 필요하면 U0a/U0b 담당에게 요청한다(C9). U0a와 U0b는 쓰기 경로가 서로 겹치지 않는다(`core/`·`worker/`·`db/`·`security.py` vs `server/` 나머지). `tests/` 파일도 단위마다 이름이 다르다.
- 문서 쪽 반영(R15를 docs/01 §2와 `job-states.mmd`에, 위험 기록을 ADR-003에)은 이 계획의 쓰기 범위 밖 — 팀장 몫.
- 문서 갱신(README 진행 현황·docs/16 열린 질문 6 결과)은 팀장이 완료 후 별도 처리 — 이 계획의 쓰기 범위 밖.

## 6. 미확인·열린 질문
해결된 항목(폼 파싱·토큰 사본·CSRF 잔여 위험·권리·runner 통합·관문 화면·approved 큐 상태·CSP)은 §7·§8 결정 기록으로 옮겼다.
| # | 내용 | 구분 |
|---|---|---|
| 8 | 승인 카드 점검 줄(✓△✗)의 데이터 출처: 자동 검수는 개발 4. 그 전까지 "미실행"으로 표시하고 ✗ 비활성 규칙은 데이터가 있을 때만 | 미확인 |
| 9 | 영상 스트리밍 Range 지원: 설치된 Starlette `FileResponse` 동작 확인 필요 | 미확인(근거 필요) |
| 10 | `shot` 산출물 저장 위치·커밋 여부, 수치(폴링 3초는 docs/03 값) 외 SLA 없음 | 미확인 |
| 11 | T2 전제(로컬 에이전트·CLI가 approve 경로를 부를 수 있음)를 Claude Code 권한 거부 규칙으로 받칠지 | 사용자 확인 대기 |
| 12 | 환경설정(docs/14) 화면은 미구현 → Aside 로그인 경고·채널 업로드 방식 요약은 자리표시 | 범위 밖 확인 |

## 7. 팀장 결정 기록 (2026-11-02, 본문 §2~§6에 반영 완료)
- T1 토큰: `_tokens.css` 수동 사본 유지 + 정본(`design/tokens.css`)과 일치 테스트. 빌드 단계 없음.
- T2 CSRF 잔여 위험(같은 PC의 로컬 프로세스): 수용. 사용자 1명·로컬 전용 전제, ADR-003에 잔여 위험으로 기록.
- T3 권리 미확인 작업: 기획 승인은 허용(승인함 "권리 미확인 보관" 배리언트와 일치), **영상 승인·업로드는 차단**(서버가 다시 검사).
- T4 `source_video` 검사는 `core/approval.py` 한 곳으로 합치고 워커 runner도 이를 쓴다(U0에서 runner 호출부만 교체, 동작 동일 테스트 유지).
- T5 관문 ①② 화면은 범위 밖. `gate_ok` 확인으로 대체.
- T6 `approved`의 queue_state는 `held_human`(다음은 사람 업로드, 개발 8).
- T7 CSP 추가: `default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'` — 인라인 스크립트 금지, 시안의 인라인 JS는 `server/static/*.js`로 옮긴다. `security.py` 수정은 U0.

## 8. challenger 반영 결정 기록 (2026-11-02, REVISE 🔴2·🟡8, 본문 반영 완료)
- C1 산출물(🔴): 워커가 성공 시 `outputs` 행을 쓴다(U0에서 worker/runner.py 수정). 열: file_path(컷표 폴더 기준 상대), sha256, render_ver, mode, plan_hash. auto_render=mp4 파일 해시, manual·ai_assistant=패키지 폴더의 파일 목록+내용 해시. 승인함은 mp4면 재생, 폴더면 파일 목록. **영상 승인(R10)은 output_id+sha256을 폼으로 받고 서버가 파일 해시를 다시 계산해 다르면 409**(다시 렌더된 경우). 필요한 열이 없으면 `0003` 마이그레이션.
- C2 권리(🔴): 권리 정본은 DB `sources.rights_status`(컷표 `meta.rights_status`는 무시). R9·R10 공용 검사 = `source_id` NULL → 409, `meta.source_video` 실경로 ≠ `sources.file_path` 실경로 → 409, rights ∉ {own, licensed} → R10 409(R9는 T3대로 허용). 검사 함수는 `core/approval.py` 한 곳.
- C3 보류 탈출: `building/held_human` → `topic`(사람 "기획 수정으로 되돌리기") 전이 R15 추가. docs/01 §2와 job-states.mmd에 반영. 버튼은 U2 제작 현황, 핸들러는 U4.
- C4 동시 승인: R9는 엔진 호출 전 조건부 UPDATE로 선점(`queue_state` held_human→running, `slot_id='approve'` — 기존 슬롯 유일 인덱스로 직렬화). 성공 → building/waiting, 실패 → held_human 복귀. 활성 작업의 `plan_path` 유일성은 `0003` 부분 유일 인덱스.
- C5 R10 해시 대조는 **서브프로세스**(core.subproc로 엔진 gate를 부르는 작은 진입점). 서버 프로세스 안에서 엔진 import·redirect_stdout 금지. 워커도 같은 진입점으로 교체(U0).
- C6 본 것=승인한 것: R9·R10 폼은 화면 표시 시점의 plan_hash(·output sha256)를 hidden으로 받고 엔진 호출 전후로 대조, 다르면 409 "내용이 바뀌었습니다 — 다시 확인". R9 화면에 컷표 요약(컷 수·길이·제목)을 보여 준다.
- C7 파일 앞섬: 엔진 성공 후 DB 실패 → 작업은 topic에 남고 **자동 승격 금지**, 재시도만 허용(재시도 시 approve가 파일을 다시 씀). 잘린 기획승인.json은 gate 실패 = 미승인으로 취급.
- C8 U0 쓰기 범위에 worker/runner.py·server/security.py(CSP)·관련 테스트 추가. 워커 동작 동일 테스트 유지.
- C9 소유권: `_macros.html`·`_app.css`·`base.html`·공용 JS는 U0 소유(배지·진행바·카드·폼 매크로를 U0에서 모두 만든다). U1~U3은 자기 페이지 템플릿·페이지 JS만. R9 폼은 U2 완료 조건. §2에 폼 필드 이름 고정(R10 체크 6칸, R11 `targets`).
- C10 폼 파싱: python-multipart 추가 없이 `server/deps.py`에 `parse_qs` 기반 공용 파서(본문 상한 재사용). U1·U4는 이것만 쓴다.
- 사용자 확인 대기: T2 전제(로컬 에이전트·CLI가 approve 경로를 부를 수 있음)를 Claude Code 권한 거부 규칙으로 받칠지.

## 9. challenger 재점검 반영 (2026-11-02, REVISE 🔴2·🟡1) — §8 C4를 대체
- C11 선점 단순화(🔴-A 해소): **DB 슬롯 선점(`slot_id='approve'`)을 쓰지 않는다.** 서버 프로세스 안 잠금(`threading.Lock`, approval_service 한 곳)으로 R9·R10을 직렬화하고, 전이는 조건부 UPDATE(`state='topic' AND queue_state='held_human'`)로만 확정한다. 서버는 단일 프로세스(uvicorn workers=1, `server/__main__` 고정) 전제. 서버가 승인 도중 죽으면 DB 잔여 행 없음 → 파일 앞섬(C7) 규칙대로 재시도만.
- C12 불변식: `topic`의 queue_state는 항상 `held_human`(docs/14 §4:80). R3(신규 접수)·R11(review→topic)·R15(building→topic)는 `held_human`으로 넣는다. 워커는 topic을 집지 않는다.
- C13 전이 함수(🔴-B): `jobs_repo.transition_from`에 `expect_queue_state`(문자열 또는 튜플) 조건 추가. R14 취소는 `running`이면 409, R15는 `building`+`held_human`만, R10은 `review`+`held_human`만.
- C14 산출물: 워커 성공인데 산출물 없음 → `held_human`(사유: 산출물 없음), review로 보내지 않는다. R10은 `outputs.plan_hash = jobs.plan_hash`인 최신 행만 승인 대상(옛 승인 산출물 거부). runner의 미사용 `load_engine` 제거.
- C15 표시 해시: R9 폼의 plan_hash는 폴링 목록에서 계산하지 않고, [기획 승인] 폼을 열 때만 `core.approval.run_gate`(서브프로세스)로 계산. 대조는 plan_hash만(tts_hash는 엔진 approve가 묶는다).
- C16 U1: 활성 plan_path 중복 접수(IntegrityError) → 422 "이미 진행 중인 작업이 이 컷표를 씁니다".
- C17 승인함 폼 규약(U3 구현 확정, U4는 이에 맞춘다): 카드마다 폼 1개 + `formaction`으로 R10~R13 분기. 공통 필드 묶음 = `csrf_token`, `confirm=yes`(hidden), `plan_hash`(outputs.plan_hash, 없으면 jobs.plan_hash), `output_id`, `sha256`(outputs.sha256), `chk_1`~`chk_6`, `note`, `targets`. GET에서는 해시를 다시 계산하지 않는다 — R10 서버가 run_gate·sha256 재계산으로 대조(C1·C5). 사람의 확인은 체크 6칸이며 서버가 6칸 모두 'on'인지 다시 검사한다(JS 비활성만 믿지 않음).

## 10. 개발 3 리뷰 반영 결정 (2026-11-02, code·security·design)
- S1(🔴 R9 TOCTOU): 엔진 approve 뒤 승인 파일 해시 ≠ 화면 해시이면 `기획승인.json`을 `기획승인.무효-<시각>.json`으로 즉시 이름 바꿔 무효화하고 409. 엔진은 수정하지 않는다(엔진 gate가 파일만 보는 잔여 위험은 무효화로 창을 최소화).
- S2: `exec_mode=ai_assistant`(Aside 위임 패키지 → 사람이 외부 AI에 올림)는 R9에서 rights ∈ {own, licensed} 요구, 아니면 409. 그 외 모드는 T3대로 R9 허용.
- S3: 승인 잠금은 `acquire(timeout=2)` 실패 시 즉시 409 "다른 승인 처리 중". 서비스의 gate 호출 시간 제한 20초.
- S4: **개발 8 요구사항** — 업로드 직전에 `outputs.sha256`을 다시 계산해 승인 시점 값과 대조, 다르면 업로드 거부.
- S5: `ApprovalError` 사용자 문구는 고정 문구, 예외 repr·경로는 마스킹 로그로만.
- K1: `approved`도 R14 취소 가능 → canceled/finished(컷표 점유 해제). K2: 수정 요청 칩은 상수 `server.approval_service.CHANGE_TARGETS`(키·라벨) 하나, 이름은 docs/13 §14에서 확정된 것만(`미확인` 칩은 제외, 열린 질문), 서버는 그 키만 허용. K3: 제작 현황에 "최근 실패" 묶음(최근 10건, 마스킹 사유). K4: R13 보관 사유는 권리 상태에 맞춘 문구. K5: 워커 outputs 기록+review 전이를 한 트랜잭션.
- D1: 영상은 자막이 입혀져 렌더되므로 자막 트랙은 추가하지 않는다(새 라우트 불필요). `aria-label`만.
- 범위 밖·열린 질문: need_source → topic(원본 도착 폴더 감시)는 후속. 산출물 없음 보류의 탈출은 R15만(의도).
