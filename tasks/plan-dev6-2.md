# plan-dev6-2 — 개발 6 2차 설계 초안 (LLM 워커 단계·화면 연결)

상태: **초안, 사용자 확정 전**. 근거: `core/llm.py`(1차), `worker/runner.py`, `db/migrations/0001·0004`, `docs/13 §관문`, `docs/14 §6-4`.
코드는 이 초안 확정 뒤에 작성한다. 실제 API 호출은 사용자 키·비용 승인 후.

## 1. 현재 사실
- `core.llm.call(conn, job_id, step, prompt_parts, schema)`는 검증된 dict 를 돌려주거나 `LlmHold(queue_state, reason)`를 올린다. 상태 전이는 호출자(워커) 몫이다.
- 워커(`runner.run_job`)는 승인된 컷표만 엔진으로 실행한다. LLM 단계는 아직 없다.
- jobs.state: queued → matching → (need_source) → topic → building → review → approved … topics 테이블이 소재 후보·선택을 담는다.
- 사람 관문: ① 소재 3개 중 선택 ② 흐름·후킹·제목 선택 ③ 기획 승인(`approve`, 사람 전용).

## 2. 제안 흐름
| 단계 | 입력 | 출력(스키마) | 저장 | 이후 |
|---|---|---|---|---|
| topic | SRT 전문, 규칙 요약 | `candidates[3]: {start, end, hook_line, tone_primary, reason}` | `topics` 행 3 | held_human(관문 ①) |
| flow | 선택한 소재 구간 SRT | `{flow[4], hook_line, titles[]}` | 작업 폴더 `흐름.json` | held_human(관문 ②) |
| plan | 선택 흐름 + SRT | 컷표 초안(`meta.plan` 포함) | `컷표.json` 초안 | 엔진 `check`→`recalc` |
| subtitle | 컷표 | `sub_lines`·`tts_text` | 컷표 갱신 | 엔진 `recalc`, 사람 승인 대기 |

- 모든 단계 출력은 `llm.check_schema` 규칙(additionalProperties false, 권리·원본 필드 금지)을 따른다.
- plan·subtitle 결과는 **엔진 `check`를 통과해야** 저장 확정. 실패하면 `held_human`(사유 기록). 승인은 여전히 사람.
- SRT는 job 의 source(`sources.srt_path`)에서 직접 읽는다. 컷표·화면 입력으로 받은 SRT 경로는 신뢰하지 않는다(권리-내용 결속).
- 같은 입력 재호출은 `llm_calls` 중복 검사가 막는다(재시도는 입력이 바뀔 때만).

## 3. 워커 구조 변경안
1. `worker/steps.py` 신설: 단계별 `build_prompt(job)`·`SCHEMA`·`apply(result)` 를 모은다. 구성별 분기는 두지 않고 규칙 파일(`rules/`)에서 요약을 읽는다.
2. `runner.run_job` 앞에서 `jobs.state` 로 분기: `matching/topic` 이면 LLM 단계, `building` 이면 기존 엔진 경로.
3. `LlmHold` → `queue.transition(..., state 유지, queue_state=hold.queue_state, hold_reason=reason)`.
4. LLM 단계는 GPU 슬롯(`gpu0`)을 쓰지 않는다. 병렬 여부는 열린 결정 #1.
5. 마이그레이션 변경 없음(0004 로 충분). 선택 결과 저장용 `topics.used` 활용.

## 4. 화면 연결안
- 승인함/제작 현황에 관문 ①②용 카드 추가: 후보 3개·흐름 표시 → 선택 → 해당 job 을 `waiting` 으로 돌려 다음 단계 실행. 선택 POST 는 기존 CSRF·루프백 보안 골격을 따른다.
- `/status` 에 AI 사용량(월·일·작업당 사용액/상한) 읽기 전용 표시(docs/14 §6-4).

## 5. 테스트 계획
- `core.llm.call` 을 목으로 대체해 단계별 정상·`LlmHold` 3종(권리·상한·검증 실패)·중복 호출·엔진 `check` 실패 경로를 검증.
- 권리 미확인 소재는 어떤 경로로도 API 를 부르지 않음(🔒).
- 기존 495 테스트 무회귀.

## 6. 열린 결정 (사용자)
1. LLM 단계 병렬 여부(슬롯 수). 기본 제안: 직렬 1.
2. 작업당 상한 $2(잠정), `max_tokens` 8000(thinking 포함이라 잘림 위험), 기본 모델 `claude-opus-5-5` 유지 여부.
3. 소재 후보 수(3 고정?)와 plan 단계 AI 초안 허용 범위(컷표 전체 vs 비트별).
4. 실제 시험 호출 시점·키 준비.

## 7. 구현 순서 제안
steps.py 뼈대 + topic 단계(목 테스트) → 관문 ① 화면 → flow → 관문 ② → plan·subtitle + 엔진 check 연결 → 사용량 표시 → 실호출 시험(승인 후).
