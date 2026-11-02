---
paths:
  - "db/**"
---
# DB 영역 규칙 (db/** 작업 때만 로드)
- SQLite. 정본은 `docs/16-기술설계ADR.md` ADR-004.
- 마이그레이션 파일명 `NNNN_요약.sql`. **적용된 파일은 수정 금지**(체크섬 불일치 → 서버 기동 거부). 고칠 때는 새 번호 파일.
- 적용은 서버만(`server/__main__.prepare_db`). 워커는 `worker.REQUIRED_SCHEMA` 미만이면 기동 거부 → 새 번호를 만들면 이 값과 `tests/test_db_schema.py` 기대값을 함께 갱신.
- ERD는 손으로 그리지 않는다: `sql2erd` → `docs/diagrams/erd.mmd` → `mmd-check`(ALTER TABLE 미반영은 알려진 도구 한계).
