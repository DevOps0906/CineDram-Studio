-- 0002_jobs_plan_path.sql — 워커용 컷표 경로 + 슬롯 점유 유일성 (task-007, docs/16 ADR-004·005)
-- plan_path: 작업 루트 기준 상대 경로. 쓸 때·읽을 때 모두 core.paths.resolve_in 으로 검사한다.
ALTER TABLE jobs ADD COLUMN plan_path TEXT;

-- 슬롯당 running 은 1건만(GPU 슬롯 1). 집기 조건과 별개로 DB가 중복 점유를 막는다.
CREATE UNIQUE INDEX idx_jobs_running_slot ON jobs(slot_id) WHERE queue_state = 'running';
