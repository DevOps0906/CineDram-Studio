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

자동 검수(`check`·`recalc`, `review_voice`, docs/13 §10): 원본 SRT 대사 구간과 나레이션 음성 구간을 소리 기준으로 본다(실제 오디오 분석 아님).
- C5-9 error: 소리 구간보다 먼저 뜨는 자막.
- P8-3 error: 대사 컷 열기 0.1초·닫기 0.2초 여유 부족, 이웃 줄 간격 0.3초 미만이면 중점 여유. 컷이 대사 중간을 자르면 끝음절 잘림(마지막 컷은 C6-1).
- C3-3 error: 후킹 컷 첫 대사가 컷 시작 0.1초(P8-3 열기 여유) 넘어서 시작. `meta.srt_kind == "auto"` 면 0.1초 초과~0.5초 이하는 warn("자동 받아쓰기 경계 — 확인 필요"), 0.5초 초과만 error(P8-2).
- `meta.srt_kind`: `"manual"`(사람이 준 SRT) | `"auto"`(자동 받아쓰기). P8-2: `"auto"` 면 `편집지시서_표.md` 머리에 "자동 받아쓰기 SRT — 컷 경계 ±0.5초 확인 필요".

렌더 산출물 검사(`render` 끝, `review_output`, ffmpeg 없으면 건너뜀). 기준값은 사람 확정 대기.
- C10-4 warn: `loudnorm` 측정만(보정 안 함) — 통합 −14 LUFS ±1 밖이거나 트루 피크 > −1 dBTP.
- C10-5 error: `blackdetect`(0.5초 이상, pix_th 0.10)를 템플릿의 영상 자리만 잘라 검사. error 면 render 종료 코드 1.
