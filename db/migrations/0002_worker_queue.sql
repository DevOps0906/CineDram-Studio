-- 0002_worker_queue.sql — 포트 없는 워커가 큐를 실행하는 데 필요한 jobs 열 (docs/16 ADR-005)
-- 적용된 뒤에는 이 파일을 고치지 않는다(체크섬 검사). 바꿀 때는 0003_*.sql 새 파일.
-- 시각은 UTC ISO-8601 문자열(0001과 같은 형식이라 문자열 비교로 순서를 잰다).

-- 실행 대상: 작업 루트 기준 컷표 경로(워커가 server.security.resolve_within 으로 다시 검사)
ALTER TABLE jobs ADD COLUMN plan_path TEXT;

-- 엔진 명령: 허용 목록. approve 는 사람만 하므로 넣지 않는다(워커 코드에서도 거부)
ALTER TABLE jobs ADD COLUMN engine_cmd TEXT
    CHECK (engine_cmd IS NULL OR engine_cmd IN ('check', 'tts-plan', 'tts-measure', 'recalc',
                                                'render', 'handoff', 'delegate'));

-- 슬롯 종류: gpu(GPU·ffmpeg, 1 고정) 또는 llm(설정값, 확정 전 1)
ALTER TABLE jobs ADD COLUMN slot_kind TEXT NOT NULL DEFAULT 'gpu'
    CHECK (slot_kind IN ('gpu', 'llm'));

-- 임대: 집은 워커 ID와 만료 시각. heartbeat 로 만료를 늘린다. 만료된 running 행은 잠금 보유 워커가 회수
ALTER TABLE jobs ADD COLUMN lease_owner TEXT;
ALTER TABLE jobs ADD COLUMN lease_expires_at TEXT;
ALTER TABLE jobs ADD COLUMN heartbeat_at TEXT;

-- 재시도: 집을 때마다 attempts+1. 회수 때 attempts >= max_attempts 면 failed
ALTER TABLE jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0);
ALTER TABLE jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts >= 1);

CREATE INDEX idx_jobs_slot ON jobs(queue_state, slot_kind);
