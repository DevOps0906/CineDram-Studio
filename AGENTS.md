# AGENTS.md — CineDram-Studio (쇼츠 설계도)

모든 AI 코딩 도구가 읽는 단일 정본(80줄 이내). 원본 영상+SRT → 쇼츠 소재·제목·컷 지시서·자막·나레이션을 만드는 스킬+규칙 세트(운영 화면 개발 중). 스택·구현 순서 정본: `docs/16-기술설계ADR.md`.
구조: `README.md` · 정본 `docs/00-통합설계서.md`(§17 파일 지도) · 엔진 `engine/README.md`.

## 명령 (기준 파일 `컷표.json`)
`python engine/shortsflow.py {check|tts-plan|tts-measure|recalc|approve|render|handoff|delegate} 컷표.json` — 테스트: `q .venv/Scripts/python -m unittest discover -s tests`(린터 없음, 설치 `pip install --require-hashes -r requirements.txt -r requirements-dev.txt`). 서버 `python -m server.run`(127.0.0.1:8765), 워커 `python -m worker --db X --root R`. DB: `python db/migrate.py <db경로>`. 긴 출력은 `q <명령>`.

## 절대 규칙 (위반 = 리뷰 🔴)
- 업로드·`approve`는 사람만. 권리(`rights_status`) 미확인 소재는 업로드 차단. 남의 쇼츠는 분석 대상일 뿐 소스 아님.
- 규칙 ID(MUST/SHOULD/🔒)는 `rules/common.md`와 일치. 🔒는 예외 없음. 민감 장면은 삭제·우회·가림(`common` §11).
- 구성 추가 = `rules/` 파일 추가. 엔진에 구성별 분기 금지. `rules/indie.md`는 정의 전.
- 비밀 커밋·로그 금지. 문서·커밋·문구는 한국어, 식별자는 영어. `_원본_무료판/` 수정 금지.

## 작업 방식
`tasks/README.md` 순서대로 하나씩, 완료 조건을 실행으로 확인 후 커밋 `[task-NNN] 요약`. 엔진 변경은 `engine/examples/make_sample.py`로 `check→recalc→render` 확인. 영역 규칙: `docs/**`는 `.claude/rules/docs.md`.
읽지 않는 파일: `.env*` · lock · 영상(`*.mp4`) · 렌더 산출물.
