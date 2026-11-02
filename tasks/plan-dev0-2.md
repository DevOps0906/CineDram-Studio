# 개발 0·2 수정 계획 (task-005·006·007 정본)

기준일 2026-11-02. challenger 점검(REVISE, 🔴2·🟡7)을 반영한 계획이다. 근거: docs/16 ADR-001·003·004·005·006.

## 팀장 결정 (challenger 질문)
- D1 의존성 고정: `requirements.txt`에 `pip freeze` **정확 고정**(`==`), 해시 고정(`--require-hashes`)은 하지 않는다(pip-tools 추가 없이 수동 해시 관리 비용이 큼). 공급망 확인은 커밋 전 `secscan`. Python은 실제 환경 **3.12**로 표기를 고친다(ADR-001).
- D2 워커 중복 방지: lease 대신 **단일 인스턴스 잠금**(OS 파일 잠금) + 기동 시 남은 `running` 복구. heartbeat 없음.
- D3 공용 모듈: FastAPI에 의존하지 않는 `core/` 패키지를 task-005에서 먼저 만들고 인터페이스를 고정한다. server·worker 둘 다 `core/`만 import한다(워커는 server를 import하지 않는다).
- D4 마이그레이션 적용은 **서버만**. 워커는 `schema_migrations` 최신 버전이 필요 버전 미만이면 기동을 거부한다.
- D5 승인 해시 재검사: 정본은 `기획승인.json`(plan_hash·tts_hash). 해시 계산은 다시 구현하지 않고 엔진 모듈 함수(`load_plan`, `tts_hash`, `gate`)를 import해 재사용(엔진 코드 변경 없음). 검사는 **render·handoff·delegate 단계만**.

## task-005 개발 0 — 골격 (builder)
1. `.gitignore`에 `.venv/`, `var/`, `*.db`, `*.db-wal`, `*.db-shm`, `*.bak` 추가. DB 기본 경로 `var/studio.db`(무시 목록 안).
2. `.venv` + `requirements.in`(fastapi, uvicorn, jinja2, httpx) → `requirements.txt` 정확 고정(D1). 버전은 설치 시점 최신 stable, `docs/16` ADR-001 표에 적는다.
3. `core/`: `paths.py`(작업 루트·`원본/` 안으로 제한: 정규화·대소문자·UNC·`C:상대`·`\\?\`·junction/심볼릭 실경로·ADS `:` 거부), `subproc.py`(인자 목록 실행·셸 없음·env 허용 목록 — Windows `SYSTEMROOT`·`PATH`·`TEMP`·`TMP` 포함 — ·시간 초과 시 **프로세스 트리 종료**(Windows `taskkill /T /F`)), `logmask.py`(키 패턴 마스킹 필터 + 지정 로거들에 부착 함수).
4. `server/app.py` `create_app()`: `docs_url`·`redoc_url`·`openapi_url` = None, `GET /healthz`. `server/__main__.py`: `127.0.0.1:8765` 고정(설정으로 변경 불가), 기동 전 `db/migrate.py` 적용·체크섬 불일치면 기동 거부(D4).
5. 테스트: 서버 기동·/healthz, /docs·/openapi.json 404, 바인딩 주소 고정, core 3개 모듈 단위 테스트(경로 탈출 형식 전부, 트리 종료 — 자식이 띄운 손자 프로세스까지 종료 확인).
- 완료: `q python -m unittest discover -s tests` 통과(기존 71 포함) + `secscan` ⚠️ 없음.

## task-006 개발 2a — 보안 골격 (builder, task-005 뒤, 007과 병렬)
- Host 허용 목록: `127.0.0.1:8765`, `localhost:8765` **포트 포함** 비교(Starlette TrustedHostMiddleware는 포트를 떼므로 직접 구현).
- 상태 변경은 POST만. Origin은 scheme·host·port **정확 일치**, Origin 없으면 거부. CORS 미들웨어 없음.
- CSRF: 기동 시 비밀로 서명한 토큰, 쿠키 `SameSite=Strict; HttpOnly`, 폼 필드 또는 헤더 값과 대조(쿠키만으로 통과 금지).
- 키·토큰 평문 반환 금지: 전 라우트 응답 검사 테스트. 로그 마스킹은 root + `uvicorn.access`·`uvicorn.error`에 부착(propagate 꺼짐 대응).
- 완료: ADR-003 체크리스트 1~7 각 거부 테스트 통과.

## task-007 개발 2b — 상태머신·워커 (builder, task-005 뒤, 006과 병렬)
- `db/migrations/0002_*.sql`: `jobs.plan_path`(작업 루트 기준 상대 경로). 쓸 때·읽을 때 모두 `core.paths`로 검사.
- `worker/`: 포트 없음, 단일 인스턴스 잠금(D2), 기동 시 스키마 버전 확인(D4)·남은 `running` → `waiting` 복구 + `job_events` 기록.
- 집기: `BEGIN IMMEDIATE` + 조건부 UPDATE, GPU 슬롯 1. 상태 전이와 `job_events`는 한 트랜잭션.
- 해시 재검사(D5): render·handoff·delegate만, 불일치 → `held_human`.
- 엔진 호출: `core.subproc`(시간 제한·트리 종료). 종료 코드 대응: 0 → 다음 상태 / 1 + 출력에 `[P0-` 표지 → `held_human` / 1 그 외 → `failed`(점검 위반·크래시, 로그 보관) / 2 → `failed`.
- 완료: 동시 집기 테스트(1개만 성공), 슬롯 동시 점유 1, 해시 불일치 → held_human, 종료 코드 대응 표 테스트, 워커 2개 기동 → 두 번째 거부, 스키마 미달 → 기동 거부.

## 리뷰 반영 결정 (2026-11-02, 개발 2 리뷰)
- 성공 → `review` + `held_human`(승인함 대기, 슬롯 비점유. docs/14 §4). `finished`는 done·failed·canceled 전용.
- 승인된 작업의 `jobs.plan_hash`가 NULL → `held_human`. `meta.source_video`가 `원본/` 밖 → `held_human`.
- **개발 3 책임:** 승인함 `approve` 경로에서 `jobs.plan_hash`를 새 승인 해시로 갱신하고, approve 시점에도 `source_video` 경로를 검사한다(안 하면 재승인 뒤 영구 보류).

## 범위 밖
로그인(사용자 1명), LLM 연동(개발 6), 업로드(개발 8), 화면(개발 3).
