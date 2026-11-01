# db — SQLite 스키마와 마이그레이션

근거: [docs/16 ADR-004](../docs/16-기술설계ADR.md). 표준 라이브러리 `sqlite3`만 쓴다(ORM 없음).

## 사용
```
python db/migrate.py <db경로>
```
- `migrations/NNNN_요약.sql`을 번호순으로 적용하고 `schema_migrations(version, checksum, applied_at)`에 기록한다. 다시 실행하면 아무것도 하지 않는다.
- **적용된 파일은 수정 금지.** 체크섬(줄바꿈 무시 sha256)이 다르면 중단(종료 코드 1). 고칠 때는 새 번호 파일을 만든다.
- 적용할 파일이 있고 DB가 이미 있으면 적용 전에 `<db>.pre-<번호>-<시각>.bak` 복사본을 남긴다.
- 연결 설정은 `connect()`: 외래 키 켬, WAL, busy timeout 5000ms.

## 규칙
- 비밀·토큰·API 키 컬럼 금지(참조 ID만). `job_events` 메시지에도 비밀을 쓰지 않는다.
- 실존 작품 데이터 시드 금지.
- `approvals`는 조회·감사용 사본, 정본은 엔진 `기획승인.json`.
- 큐 필드(`queue_state`·`priority`·`slot_id`·`hold_reason`)는 제안 단계([docs/14 §4](../docs/14-환경설정과대기큐.md)). 작업 집기는 `BEGIN IMMEDIATE` 안에서 `... WHERE id=? AND queue_state='waiting'` 조건부 UPDATE.

## ERD
손으로 그리지 않는다. 마이그레이션을 바꾸면 다시 생성한다.
```
~/.claude/nomad/bin/sql2erd db/migrations -o docs/diagrams/erd.mmd
~/.claude/nomad/bin/mmd-check docs/diagrams/erd.mmd
```

## 테스트
```
python -m unittest discover -s tests -p "test_db_schema.py"
```
