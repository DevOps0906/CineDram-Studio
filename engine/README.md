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
