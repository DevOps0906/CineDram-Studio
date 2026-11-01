# tests — engine/shortsflow.py 동작 고정

- 실행(표준 라이브러리만): `python -m unittest discover -s tests` · pytest 가 있으면 `python -m pytest tests -q` 도 같다.
- 모든 입력은 임시 폴더(`작품 폴더/1화/컷표.json`, 한국어·공백 경로)에 합성으로 만든다. 네트워크·실제 작품 데이터 없음.
- ffmpeg/ffprobe 가 없으면 handoff·render·실측 테스트는 skip. render(약 10초)만 빼려면 `SF_SKIP_RENDER=1`.
- `test_auto_review_pending.py` 의 skip 은 docs/13 §10 미구현 검사 목록이다. 구현 시 skip 을 풀고 채운다.
- 승인 게이트는 컷표.json(plan_hash)과 tts_clips.json 실측 길이(tts_hash)를 함께 본다 — 승인 뒤 둘 중 하나라도 바뀌면 재승인(`test_gate`).
