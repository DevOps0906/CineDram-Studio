# 초안: 렌더 자동 검수 — 음량 보정·무음·검은 화면 규칙 (사용자 확정 전)

상태: **초안.** `rules/common.md`는 아직 고치지 않았다. 확정되면 아래 ID로 `common`에 옮기고, 엔진은 그다음에 바꾼다(docs/13 §10 "규칙이 먼저").
근거 문서: `docs/13-원클릭제작흐름.md` §10·§10-1·§10-2 · `rules/common.md` §0·§3·§8·§10-2·§11·§12 · `engine/shortsflow.py` render(31·495~520행). 조사일 2026-11-02.

## 1. ID 제안

- 기존 체계: 접두사 + 장 번호 + 순번(`C3-3`, `A10-9`, `P8-3`). 음성 관련은 §10-2의 `A10-n`이 이미 있고 마지막이 `A10-11`이다.
- 제안: §10-2 아래에 **§10-3 렌더 자동 검수**를 새로 두고, 음량·무음은 `A10-12`부터 이어 쓴다. 검은 화면은 소리가 아니지만 같은 검수 단계라 같은 장에 두고 `A10-` 순번을 쓴다.
  - 대안: 검은 화면만 §8(시각 정밀도)의 `P8-7`로 둔다. 단 §8은 🔒 장이라 그 규칙도 잠금이 된다 → 사용자 선택.
- 점검표(§12) 새 번호: 마지막이 28이므로 **29~32**를 제안한다.

## 2. 제안 규칙 표

| ID | 규칙 문장 | 임계값 | 측정 방법(ffmpeg) | 판정 | 출처 |
|---|---|---|---|---|---|
| A10-12 | **MUST** 최종 영상(컷을 이은 뒤)의 통합 음량을 측정해 목표로 맞춘다. 컷별로 따로 맞추지 않는다(A10-8 원음/나레이션 비율이 깨진다). | 목표 **-14 LUFS**(YouTube 재생 기준, 2차 출처). 허용 오차 `미확인` | 1차: `loudnorm=I=-14:TP=-1:LRA=<미정>:print_format=json -f null -` → `input_i·input_tp·input_lra·input_thresh·target_offset` 받기. 2차: 같은 목표 + `measured_I/LRA/TP/thresh`·`offset`·`linear=true`, 출력 `-ar 48k` | 자동 보정 | [1][2][5][6] |
| A10-13 | **MUST** 최대 true peak를 목표 이하로 둔다. 표본 피크(`volumedetect max_volume`)로 판정하지 않는다. | **-1 dBTP**(AES TD1008, 2차 출처). 일부 실무자는 -2~-3 dBTP 권장 → `미확인`(사용자 선택) | `ebur128=peak=true` 요약의 True peak, 또는 loudnorm 1차의 `input_tp` | 자동 보정(A10-12와 한 번에) | [3][4][7] |
| A10-14 | **SHOULD** 보정은 선형 이득으로만 한다. loudnorm이 동적 모드로 바뀌면(측정 LRA > 목표 LRA, 또는 이득 후 피크가 TP 초과) 그 사실과 전후 값을 기록하고 승인함에 표시한다. | 목표 LRA `미확인`(loudnorm 기본 7 LU) | loudnorm 2차 `print_format=summary`의 `Normalization Type`(Linear/Dynamic) | 자동 보정 + 표시 | [5][6] |
| A10-15 | **SHOULD** 너무 작은 영상을 그대로 올리지 않는다. YouTube는 기준보다 큰 영상은 줄이지만 작은 영상은 키우지 않는다(2차 출처). | 하한 `미확인` | A10-12 측정값 | 자동 보정(A10-12가 처리) | [1][2] |
| A10-16 (A10-9 보강) | **MUST** 렌더 결과에 의도하지 않은 무음 구간이 없어야 한다. 민감 장면 무음 처리(`audio_mode=mute`, §11)와 컷 경계 꼬리는 제외한다. | 무음 레벨 `미확인`(ffmpeg 기본 0.001 진폭 = -60 dBFS). 길이: 기존 규칙에서 유도한 **0.3초 초과**(P8-3 닫기 0.2초 + 다음 컷 열기 0.1초, A10-5 꼬리 0.2초) — 유도값, 확정 필요 | `silencedetect=noise=-60dB:d=0.3`(기본은 `d=2`라 쇼츠엔 너무 김) → `silence_start/end`를 컷표 시각과 대조, mute 구간 제외 | 차단 | [8] |
| A10-17 (C3-3 측정) | **MUST** 첫 소리는 0초에 시작한다. 렌더 결과 0초부터의 무음 길이를 잰다. | **0.1초 이하**(P8-3의 "첫 음절 0.1초 전 열기"에서 유도). 무음 레벨은 A10-16과 같음(`미확인`) | `atrim=0:3,silencedetect=noise=-60dB:d=0.1` → `silence_start: 0`이 나오면 그 `silence_end`가 첫 소리 시각 | 차단 | [8] |
| A10-18 | **MUST** 렌더 결과에 의도하지 않은 검은 화면(튀는 프레임 포함)이 없어야 한다. 보정(프레임 제거)은 하지 않고 기획의 컷 경계를 고친다(docs/13 §10-1). | 길이: **1프레임 이상**(= `1/fps`초, 쇼츠는 튀는 1프레임도 대상이라 기본 2초 대신) — 제안값, 확정 필요. 밝기: ffmpeg 기본 `pix_th=0.10`, `pic_th=0.98` 유지 제안(근거는 기본값뿐, 쇼츠용 값은 `미확인`) | `blackdetect=d=<1/fps>:pic_th=0.98:pix_th=0.10` → `black_start/end/duration`을 컷 번호에 매핑 | 차단 → 기획 수정 → 재승인 | [9] |

점검표 제안: 29 통합 음량·true peak가 목표인가(A10-12·13) / 30 자동 보정 기록이 남았나(A10-14) / 31 의도하지 않은 무음이 없고 첫 소리가 0.1초 이내인가(A10-16·17) / 32 검은 화면 0개인가(A10-18).

## 3. ffmpeg 필터 사실 (소스 확인)

| 필터 | 인자 | 기본값 | 뜻 |
|---|---|---|---|
| blackdetect | `d`(black_min_duration) | 2초 | 검은 화면으로 볼 최소 길이 |
| | `pic_th`(picture_black_ratio_th) | 0.98 | 화면에서 "검은 픽셀" 비율이 이 이상이면 검은 화면 |
| | `pix_th`(pixel_black_th) | 0.10 | 픽셀 밝기 기준. 제한 범위 영상은 `16 + 0.10×(235-16)` ≈ 38(8비트) 이하를 검은 픽셀로 셈 |
| silencedetect | `noise`(n) | 0.001(선형 진폭, ≈ -60 dB). `-50dB`처럼 dB로도 줄 수 있음 | 이 이하를 무음으로 봄 |
| | `d`(duration) | 2초 | 무음으로 볼 최소 길이 |
| loudnorm | `I` / `LRA` / `TP` | -24 LUFS / 7 LU / -2 dBTP (범위 I -70~-5, TP -9~0) | 목표값. 기본은 방송 기준이라 쇼츠엔 직접 지정 |
| | `linear` | true | 선형 가능 조건(측정값 4개 제공, 이득 후 TP ≤ 목표, 측정 LRA ≤ 목표)이 안 되면 동적 모드로 바뀜 |
| | 표본율 | 동적 모드는 192 kHz 입력만 받음 | 출력에 `-ar 48k`를 꼭 붙인다(엔진은 48 kHz 사용) |
| ebur128 | `peak` | none | `peak=true`라야 true peak를 출력. 끝에 I·LRA·임계값·피크 요약 |
| volumedetect | — | — | `mean_volume`(평균 제곱 기반), `max_volume`(**표본 피크**, true peak 아님), 히스토그램 |

## 4. 플랫폼 수치의 확실성

- **YouTube(쇼츠 포함) -14 LUFS:** 공식 도움말 페이지를 찾지 못했다. 2019년 기준 변경을 다룬 실무자 글(Ian Shepherd)과 다수 2차 자료가 일치한다. 쇼츠에 같은 기준이 적용된다는 것도 2차 자료뿐 → **"2차 출처 일치, 공식 미확인"**으로 적는다.
- **true peak -1 dBTP:** AES TD1008(스트리밍 권고) 내용으로 인용되나 원문 PDF는 열지 못했다(2차 출처).
- **TikTok·Instagram Reels:** 공식 자료 없음. 2차 자료끼리도 -10~-16 LUFS로 갈린다 → `미확인`. 표에 넣지 않았다.

## 5. docs/13 §10-1 원칙과의 대조 (어긋나는 점·주의)

1. **원본 불변:** 모두 렌더 필터로만 적용 → 맞다.
2. **구조를 바꾸는 보정 금지:** 음량 보정은 타임라인을 바꾸지 않는다 → 허용 범위. 단 loudnorm **동적 모드는 압축기처럼 구간별 이득을 바꾼다**. 길이는 그대로지만 A10-8(나레이션 구간 원음 15%)의 상대 비율이 바뀔 수 있다 → A10-14로 선형 우선·동적 전환은 기록·표시로 막았다. 동적 모드를 아예 금지(차단)할지는 `미확인`(사용자 선택).
3. **무음 차단과 §11 충돌 가능:** 민감 음성 무음 처리(`audio_mode=mute` → `volume=0`, 엔진 500행)와 엔진의 `apad`(음성 뒤를 무음으로 채움, 505·518행)는 의도된 무음이다. 제외 목록 없이 검사하면 🔒 규칙을 지킨 영상이 차단된다 → A10-16에 제외 조건을 넣었다.
4. **검은 화면과 레터박스:** 엔진의 검은 여백은 화면 일부라 `pic_th=0.98`(98%)에 걸리지 않는다(16:9 원본을 9:16에 넣으면 여백 ≈ 68%). 단 원본 화면이 어두운 장면이면 여백과 합쳐 98%를 넘을 수 있다 → 오탐 가능, 임계값 확정 때 실제 영상으로 확인 필요.
5. **기록:** §10-1의 기록 필드명은 아직 미정. 최소 기록 항목 제안: 측정 전 `input_i/tp/lra/thresh`, 보정 후 값, `Normalization Type`, 적용 필터 문자열. 검출(무음·검은 화면)은 시작·끝·컷 번호.
6. **측정 위치:** 현재 렌더는 컷별 파일을 만든 뒤 잇는다. 음량은 **이은 결과 1개**에서 2-pass로 해야 하므로 렌더 마지막 단계가 하나 늘어난다(재인코딩 1회 추가) — 엔진 설계 때 판단.

## 6. 미확인 목록 (사용자 확정 필요)

음량 허용 오차(±LU) · 목표 LRA · true peak -1 vs -2·-3 dBTP · 동적 모드 허용 여부 · 무음 레벨(dB) · 무음 길이 0.3초(유도값) · 첫 소리 0.1초(유도값) · 검은 화면 길이 1프레임·밝기 기준 · 검은 화면 규칙의 장 위치(A10 vs P8 🔒) · TikTok/Reels 기준.

## 출처

1. Meterplugs(Ian Shepherd), "YouTube changes loudness reference to -14 LUFS" — https://www.meterplugs.com/blog/2019/09/18/youtube-changes-loudness-reference-to-14-lufs.html
2. Production Advice, "YouTube loudness" — https://productionadvice.co.uk/youtube-loudness/
3. Production Advice, "AES TD1008" — https://productionadvice.co.uk/td1008/
4. Production Expert, "AES update loudness recommendations for audio-only streaming" — https://www.production-expert.com/production-expert-1/aes-update-loudness-recommendations-for-audio-only-streaming
5. FFmpeg 소스 `af_loudnorm.c`(옵션 기본값·선형 전환 조건·192 kHz) — https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavfilter/af_loudnorm.c
6. Kyle Swanson(loudnorm 작성자), 2-pass 사용법 — http://k.ylo.ph/2016/04/04/loudnorm.html
7. FFmpeg 소스 `f_ebur128.c`·`af_volumedetect.c` — https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavfilter/f_ebur128.c · https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavfilter/af_volumedetect.c
8. FFmpeg 소스 `af_silencedetect.c` — https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavfilter/af_silencedetect.c
9. FFmpeg 소스 `vf_blackdetect.c` — https://raw.githubusercontent.com/FFmpeg/FFmpeg/master/libavfilter/vf_blackdetect.c
- 참고(채택 안 함, 공식 아님): TikTok·Reels 수치 2차 자료 — https://openclip.app/learn/audio-normalization · https://filmandflow.beehiiv.com/p/the-ultimate-lufs-guide-perfect-audio-for-social-media
- 비고: ffmpeg.org 문서 페이지는 접속 거부되어 FFmpeg GitHub 소스(master)로 기본값을 확인했다.
