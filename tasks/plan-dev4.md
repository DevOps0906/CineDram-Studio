# 개발 4 계획 — 자동 검수 6가지 (task-011 정본)

기준일 2026-11-02. 근거: docs/13 §10·§10-1·§10-2·§11, rules/common.md §10-3(A10-12~18)·C5-9·P8-2·P8-3·C3-3·A10-6·A10-8·A10-9·§11, docs/16 구현 순서 개발 4행, tests/test_auto_review_pending.py(skip 6건).
규칙 수치는 바꾸지 않는다. 규칙에 없는 값은 `미확인`.

## 1. 결정 (팀장·사용자 확정, 그대로)
- 두 줄 자막은 현 규칙 유지(16자 초과 경고). 음량 -14 LUFS · -1 dBTP. 무음 0.3초 초과 · 첫 소리 0.1초 · 검은 화면 1프레임은 차단.
- 렌더 전 = `check` 확장(컷표·측정값만으로 되는 것). 렌더 후 = 엔진 새 명령 `inspect`(ffmpeg 9.0.2 측정) → `점검결과.json`.
- 음량 보정은 `render` 안 loudnorm 2-pass(선형). 전후 값은 `작업기록.json`에 기록(§10-1).
- 워커: render 다음 inspect. 차단 있으면 review로 보내지 않고 held_human(사유=실패 항목). 승인함 `inspect_for`가 `점검결과.json`을 읽어 표시.

## 2. 항목별 구현 표
| 항목 | 규칙 ID | 단계 | 측정 방법 | 판정 | 예외 | 테스트 |
|---|---|---|---|---|---|---|
| 자막 싱크 | C5-9 · #13 | check | 컷마다 자막 시작 ≥ 그 컷의 소리 시작(나레이션=`tl_start`, 대사=원본 SRT 줄 시작을 타임라인으로 환산). 환산 오차 0.01초 허용(반올림) | 어긋나면 error | 자동 받아쓰기 SRT의 경계는 ±0.5초 확인(P8-2)이라 경고 문구만 | 자막이 컷 소리보다 0.05초 먼저 → error, 정시 → 통과 |
| 말끝 여유 | P8-3 · C6-1 · S11-5 · #11·#20 | check | 대사 컷마다 겹치는 원본 SRT 줄의 첫 음절 0.1초 전 ≤ `src_start`, 끝 0.2초 후 ≤ `src_end`. 이웃 줄 간격 0.3초 미만이면 간격 중점까지 허용 | 부족하면 error | 삭제(S11-5) 경계는 문장·호흡 경계에 맞았는지만 본다. **자동 적용은 이번 범위 밖**(기획 단계 몫) | 끝 0.1초 → error, 0.2초 → 통과, 간격 0.2초 이웃 → 중점 기준 통과 |
| 한 줄 자막 | C5-9 · #14 | check | 기존 16자 초과 경고(`wrap` 줄 기준) 유지 | warn(차단 아님, 사용자 확정) | — | 경계 16·17자, 두 줄 허용 확인 |
| 컷 간격 | A10-6 · #26 | check | 기존 `tl_end`~`tl_start` 간격 검사 유지 | error | — | 간격 0.01초 → error(회귀) |
| 지시서 머리 문구 | P8-2 · #16 | recalc | 자동 받아쓰기 SRT를 썼으면(`meta` 표시 필드는 `미확인`, §6) `편집지시서_표.md` 머리에 "자동 받아쓰기 SRT — 컷 경계 ±0.5초 확인 필요" 기록 | 없으면 recalc 실패 | 수동 SRT면 쓰지 않음 | 두 경우 머리 문구 유무 |
| 음량 보정 | A10-12·14·15 · #29·#30 | render | pass1: 합친 `flat.mkv` 오디오를 `loudnorm=I=-14:TP=-1:print_format=json`으로 측정 → pass2: 같은 필터에 `measured_*`·`offset`·`linear=true`로 최종 인코딩(`-ar 48000` 명시). 원음 15%·대사 100%(A10-8)는 컷 단위 믹스에서 이미 적용, 보정은 최종 1개에만 | 보정 자체는 판정 없음. pass2 결과가 `dynamic`이면 `작업기록.json`에 전후 값 기록 + inspect가 warn | 민감 mute 구간은 무음이므로 측정에서 제외되지 않음(전체 통합 값) | 작은 입력 → 보정 후 -14±오차, 기록 필드 존재, 선형 유지 |
| 음량·true peak 확인 | A10-12·13 | inspect | 완성 mp4를 `loudnorm=print_format=json`(출력 버리기)으로 측정 → `input_i`, `input_tp`. 표본 피크(`volumedetect`) 쓰지 않음 | true peak > -1.0 dBTP → **fail**. 통합 음량이 -14에서 벗어나면 **warn**(허용 오차 `미확인`, §6) | — | 합성 음원 2종: TP -3 통과, TP 0 fail |
| 무음 | A10-16 · C3-3·A10-9 · #31 | inspect | `silencedetect=noise=-60dB:d=0.3`. 구간 길이 > 0.3초(+0.001 여유)면 후보 | **fail** | 제외: ① mute 구간(컷표 `audio_mode`/`sensitive.audio`=mute 컷의 `tl_start~tl_end`) ② 나레이션 컷 꼬리(A10-5 TAIL, `tl_end`−0.2~`tl_end`) | 0.5초 무음 삽입 fail, 0.3초 통과, mute 컷 안 무음 통과 |
| 첫 소리 | A10-17 · C3-3 | inspect | 위 silencedetect에서 0초에서 시작한 무음의 끝 = 첫 소리 시각 | 0.1초 초과 **fail** | 첫 컷이 mute면 건너뛰고 warn | 0.2초 앞 무음 fail, 0.1초 통과 |
| 검은 화면 | A10-18 · #32 | inspect | `crop=<영상 영역>,blackdetect=d=0:pix_th=0.10:pic_th=0.98`. 영상 영역은 템플릿 `video`(x·y·w·h)만 — 위·아래 색 띠(레터박스·템플릿 배경)는 영역 밖이라 제외 | 1프레임 이상 **fail**(튀는 프레임 포함). 고치지 않고 기획 컷 경계 수정 → 재승인(§10-1) | 소스 자체의 검은 띠는 `pic_th` 0.98 미만이라 걸리지 않음 | 검은 1프레임 삽입 fail, 어두운 장면·레터박스 통과 |

## 3. `점검결과.json` 스키마 (v1)
위치: 컷표와 같은 폴더. `inspect`가 쓰고 워커·승인함은 읽기만 한다. 비밀·절대경로 저장 금지(상대 파일명).
```json
{ "schema": 1, "plan_hash": "<jobs.plan_hash와 같은 값>", "inspected_at": "ISO8601Z", "ffmpeg": "9.0.2",
  "output": {"file": "숏폼_상하템플릿.mp4", "size": 0, "sha256": "", "duration": 0.0},
  "summary": {"ok": 0, "warn": 0, "fail": 0, "auto_fixes": 0, "fail_reason": null},
  "items": [{"id": "silence", "rule": "A10-16", "level": "ok|warn|fail", "measured": 0.0, "limit": 0.3,
             "unit": "s", "at": [{"start": 0.0, "end": 0.0}], "excluded": [{"why": "mute", "start": 0.0, "end": 0.0}],
             "msg": "한 줄 사유"}] }
```
- `items.id` 고정 5개: `loudness`(A10-12) · `true_peak`(A10-13) · `silence`(A10-16) · `first_sound`(A10-17) · `black_frames`(A10-18). 항목마다 1개, 건너뛴 항목은 `level: "warn"`+사유.
- `summary.fail_reason`: fail 항목의 `msg`를 `; `로 이은 200자 이내(없으면 null). `auto_fixes`: `작업기록.json`의 보정 건수.
- 종료 코드: 0 = fail 0(warn 허용) · 1 = fail ≥ 1(파일은 항상 씀) · 2 = 실행 불가(mp4 없음·ffmpeg 없음·컷표 읽기 실패, 파일 안 씀).
- `작업기록.json`(render가 씀, 신규 파일): `{"schema":1,"auto_fixes":[{"item":"loudness","rule":"A10-12","filter":"<적용 필터 문자열>","before":{"i":0,"tp":0,"lra":0,"thresh":0},"mode":"linear|dynamic","target":{"i":-14,"tp":-1},"at":"ISO8601Z"}]}`. 항목을 끄고(`auto_fixes` 비움) 다시 render하면 원래대로(§10-1).
- 신선도: `inspect_for`는 `plan_hash`가 `jobs.plan_hash`와 다르거나 `output.size`가 실제 파일과 다르면 "미실행"으로 본다(오래된 점검을 통과로 표시하지 않음).

## 4. 워커·승인함 계약
| 위치 | 변경 |
|---|---|
| worker/runner.py | `exec_mode == auto_render`이고 render 종료 코드 0일 때만 `engine inspect 컷표 --out <mp4>`를 `subproc.run`(같은 `should_stop`·`ENGINE_TIMEOUT`). rc 0 → 기존 `_record_and_review`. rc 1 → outputs 기록 없이 `hold("점검 실패", fail_reason)` = building/held_human(사유는 `점검결과.json`의 `fail_reason`, 없으면 로그 꼬리). rc 2·시간 초과 → 기존 failed 경로. handoff·delegate는 mp4가 없어 inspect 생략 |
| server/routes/inbox.py `inspect_for(conn, root, job)` | `jobs.plan_path` 폴더의 `점검결과.json` 읽기 → `{ok, warn, fail, fail_reason, auto_fixes}`. 파일 없음·깨짐·오래됨 → None(템플릿이 "자동 점검 미실행" 표시). `core.paths`로 경로 재검사. 호출부 1곳(`build_card`)도 새 인자로 |
| 템플릿 | `_macros.html` `inspect_line`에 보정 건수 표시만 추가(§10-1 "승인함 표시"). 레이아웃 변경 없음 |
- held_human 작업은 승인함(state in review·approved)에 안 뜬다 → 사유는 제작 현황(개발 3 화면)의 `hold_reason`으로 보인다. 재시도 경로는 §6.

## 5. 작업 분해 (병렬, 쓰기 경로 겹침 없음)
| 단위 | 담당 | 쓰기 경로 | 완료 조건 |
|---|---|---|---|
| E1 엔진 check·recalc·render | builder | engine/shortsflow.py(check·do_recalc·cmd_render·main에 `inspect` 선택지 추가), tests/test_engine_check_av.py | `q python -m unittest tests.test_engine_check_av` 통과(싱크·말끝·한 줄·간격·P8-2·음량 보정 6묶음). make_sample로 `check→recalc→render` 통과, `작업기록.json` 생성, 최종 mp4 true peak ≤ -1 |
| E2 엔진 inspect | builder | engine/inspect_av.py(신규, `inspect_output(plan, out_path) -> int`와 `loudnorm_stats(path, i, tp, lra) -> dict` 공개), tests/test_engine_inspect.py | 합성 mp4(무음·검은 프레임·큰 피크 삽입)로 항목별 fail/통과 테스트, 종료 코드 0·1·2, 스키마 키 검증. make_sample 산출물에 `inspect` 실행 → 0 |
| W 워커 | builder | worker/runner.py, tests/test_worker_inspect.py | 가짜 엔진으로 rc 0/1/2/시간 초과 4경우: rc 1은 review 아님·outputs 없음·hold_reason=fail_reason, rc 0만 review. 기존 `tests/test_worker_*` 회귀 통과 |
| I 승인함 | builder | server/routes/inbox.py, server/templates/_macros.html, tests/test_inbox_inspect.py | 손으로 만든 `점검결과.json`(정상·fail·깨짐·오래됨·없음) 5경우가 카드에 표시/미실행으로 나옴, 경로 이탈 거부, 기존 `tests/test_routes_contract.py` 회귀 통과 |
| X 통합(E1+E2+W+I 후, 직렬) | 팀장→builder | tests/test_auto_review_pending.py(삭제), tests/test_integration_dev4.py | pending 6건이 위 테스트로 모두 대체됨. 실제 워커+엔진으로 정상 1건(review)·검은 프레임 1건(held_human) 통합 통과. `q python -m unittest discover -s tests` 전체 통과 |
- E1↔E2 접점은 `inspect_av` 공개 함수 시그니처 2개뿐(위 표 고정). E1은 `from engine import inspect_av` 대신 `shortsflow.py`와 같은 폴더 import 방식을 따라 `main`에서 호출. 접점 확인은 X에서.
- 단위 W·I는 E 완료를 기다리지 않는다(§3 계약의 가짜 엔진·손 파일로 검증).

## 6. 위험도·리뷰
| 단위 | 위험 | 사유 | 검증 |
|---|---|---|---|
| E1·E2 | 보통 | 새 의존성 없음(ffmpeg 기존). 외부 프로세스 인자는 리스트로만 전달, 경로는 컷표 폴더 안 | codex-reviewer(code) |
| W | 보통~높음 | 상태 전이·승인 후 경로. 단 새 노출 면·키 없음 | codex-reviewer(code) + reviewer(contract): rc별 전이·outputs 미기록·`logmask` 적용 |
| I | 보통 | 파일 읽기(경로 재검사)·화면 표시 | codex-reviewer(code), 화면 변경분은 design |
- challenger는 생략(승인 게이트·권한 변경 없음). 단 W가 `approve`/해시 검사 순서를 건드리면 높음으로 올리고 challenger 추가.
- 보안 영향: `점검결과.json`은 신뢰하지 않는 입력으로 취급(크기 상한 1MB, 타입 검증, 문자열은 템플릿 자동 이스케이프).

## 7. 미확인 (근거·결정 필요)
| 항목 | 상태 |
|---|---|
| 음량 허용 오차 · 목표 LRA · dynamic 허용 여부 | 규칙 `미확인`(A10-12·14). 구현은 LRA·오차를 상수 한 곳에 두고, 입력 LRA가 커서 loudnorm이 dynamic으로 바뀌는지 make_sample로 확인. 오차 값은 **팀장 결정 필요**(그 전까지 벗어남은 warn만) |
| 싱크 허용 오차 | 0.01초(환산 반올림)는 구현상 잠정값. 규칙 수치 아님 — 팀장 확인 |
| 쇼츠용 `pix_th`/`pic_th` | ffmpeg 기본 사용(A10-18). `d=0`으로 1프레임이 잡히는지 합성 영상으로 확인 필요 |
| 무음 -60 dBFS | 잠정(A10-16). 배경 소음 영상에서 오탐 가능 → 첫 실물 샘플 후 재검토 |
| 자동 받아쓰기 SRT 표시 필드(`meta`) | 컷표 스키마에 있는지 미확인 — 없으면 필드 추가가 규칙·스키마 변경이라 **팀장 결정 필요** |
| held_human(building) 재시도 | 개발 5의 R20은 failed만 다룸. 점검 실패 후 기획 수정→재승인→재큐 경로가 화면에서 이어지는지 확인 필요 |
| 말끝 여유 자동 적용 | 이번 범위는 검사만(P8-3 위반 error). 자동 적용 여부는 팀장 결정 |

## 8. 팀장 결정 (2026-11-02)
- D1 음량 허용 오차: 잠정 ±1 LU. 벗어나면 warn(차단 아님). 실제 소스 검증(개발 1) 뒤 사용자 확정.
- ~~D2 `meta.srt_origin`~~ → **D2' (리뷰 반영):** P8-2는 🔒라 기본값에서 꺼지면 안 된다. 기존 필드 `meta.srt_kind`를 쓰고 `manual`일 때만 문구를 뺀다(없음·auto·그 외 = 문구 넣음, 안전 쪽). `srt_origin`은 폐기.
- D6 (리뷰 반영): inspect 전에 이전 점검결과.json 삭제 + 결과의 plan_hash 대조(크래시 rc 1 오인 방지). 승인함은 결과 sha256을 outputs.sha256과 대조(재해시 없음), fail_reason 200자 상한. blackdetect d는 0.5/fps.
- D3 말끝 여유(P8-3)는 검사만. 자동 적용은 타임라인 변경이라 §10-1(구조 변경 보정 금지) 위반.
- D4 점검 실패 held_human의 탈출은 기존 R15(→ topic) → 컷표 수정 → R9 재승인. 새 라우트 없음.
- D5 순서: W(워커)는 개발 5 Q-A의 worker/runner.py 수정이 끝난 뒤 착수(쓰기 충돌 방지). E1·E2·I는 즉시 병렬.
