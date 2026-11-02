# engine/ — 자리표 (코드는 요청 시 작성)

단계 1부터 필요한 처리 모듈의 입출력 계약만 적어 둔다.

| 모듈 | 입력 | 출력 | 규칙 |
|---|---|---|---|
| 받아쓰기 | 영상 경로 | `.srt` | faster-whisper, GPU면 medium, CPU면 small |
| 컷 계산 | 컷 표(원본 시작·끝) | 컷별 타임라인 시작, 총 길이 | C7-3 합계 검산 |
| 자막 재계산 | 원본 SRT + 컷 표 | `자막.srt`(타임라인 기준) | common §5 공식, C5-10~13 |
| SRT 검증 | `자막.srt` | 통과/위반 목록 | 번호 연속, 겹침, 0.8~4초, 8자/초 |
| 미리보기 | 원본 + 컷 표 | `cuts/*.mp4`, `미리보기.mp4` | ffmpeg, 디스크 검사 선행 |
| 디스크 검사 | 원본 크기 | 통과/`blocked_disk` | 여유 ≥ max(10GB, 원본×2) |
| 폴더 감시 | 원본 폴더 | `need_source → topic` 이벤트 | `원본/<작품>/E01.mp4+srt` 규칙 |

승인 범위(`shortsflow.py approve`): `기획승인.json` 에 `컷표.json` 해시(`plan_hash`)와 `tts_clips.json` 실측 길이 해시(`tts_hash`, 파일 없으면 고정 값)를 함께 기록한다. render·handoff·delegate 는 둘 다 현재와 같을 때만 진행하고, `tts_hash` 없는 예전 승인 기록은 재승인을 요구한다(P0-2). 순서: tts-measure → recalc → check → approve.

## 렌더 전 검사 (`check`·`recalc`, 원본 SRT 기준)

- C5-9: 자막이 그 컷의 소리 시작보다 먼저 뜨면 error.
- P8-3/C6-1: 대사 컷은 첫 음절 0.1초 전에 열고 끝 0.2초 뒤에 닫는다(이웃 줄 간격 0.3초 미만이면 중점까지). 검사만 한다.
- C3-3: 후킹(컷 1)이 대사면 첫 대사 줄 시작 − 컷 시작을 본다. 허용 상한은 P8-3 열기 여유 0.1초 + 1프레임(1/30초, 약 0.134초). 수동 SRT는 이를 넘으면 error. 자동 받아쓰기 SRT(`srt_kind` ≠ manual)는 경계가 ±0.5초(P8-2)라 0.134~0.5초 warn("확인 필요"), 0.5초 초과 error. 후킹 컷에 대사 줄이 없으면 error. 렌더 결과의 실제 첫 소리 측정은 A10-17(`inspect`)이 따로 한다.

## render 음량 보정 (A10-12·13·14)

최종 연결 영상 1개에만 `loudnorm` 2-pass(목표 −14 LUFS, true peak −1 dBTP, LRA 11)를 건다. 컷 단위 믹스(A10-8)는 그대로 둔다. 보정 뒤 ±1 LU 밖이거나 동적 모드로 바뀌면 warn만 내고, 필터와 전후 측정값은 `작업기록.json`의 `auto_fixes`에 남긴다. 오디오가 무음이면 보정하지 않고 warn.

## 렌더 후 검수 `inspect_av.py`

`python engine/inspect_av.py 컷표.json [--out 영상]` 또는 `shortsflow.py inspect 컷표.json`. 렌더 결과에서 음량(A10-12)·true peak(A10-13)·무음(A10-16)·첫 소리 0.1초 이내(A10-17)·검은 화면(A10-18)을 재고 `점검결과.json`을 쓴다. 종료 코드 0 통과(warn 허용) / 1 차단(fail ≥ 1, 파일은 씀) / 2 실행 불가(파일 안 씀). 워커는 render 뒤 이 검수를 부른다(`worker/runner.py`).
