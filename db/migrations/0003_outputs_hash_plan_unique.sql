-- 0003_outputs_hash_plan_unique.sql — 산출물 해시 기록 + 활성 작업 plan_path 유일 (개발 3 U0a, C1·C4)
-- outputs.file_path: 컷표 폴더 기준 상대 경로. sha256: mp4 파일 해시 또는 폴더(파일 목록+내용) 해시.
ALTER TABLE outputs ADD COLUMN sha256 TEXT;
ALTER TABLE outputs ADD COLUMN mode TEXT
    CHECK (mode IS NULL OR mode IN ('auto_render', 'manual', 'ai_assistant'));
ALTER TABLE outputs ADD COLUMN plan_hash TEXT;      -- 이 산출물을 만든 승인 컷표 해시

-- 같은 컷표를 가리키는 활성(finished 아님) 작업은 1건만. 동시 승인·중복 접수를 DB가 막는다.
CREATE UNIQUE INDEX idx_jobs_active_plan_path ON jobs(plan_path)
    WHERE plan_path IS NOT NULL AND queue_state <> 'finished';
