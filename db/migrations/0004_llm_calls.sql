-- 0004_llm_calls.sql — LLM 호출 기록 (docs/16 ADR-002 "기록", docs/14 §6-4 비용 한도, core/llm.py)
-- 호출 메타만 남긴다. 프롬프트·응답 본문·키는 저장하지 않는다(ADR-003 위협 메모 "로그").
-- cost_usd: status='started' 동안은 최악 비용 예약값, 응답 뒤 실제 사용량 기준 값으로 바꾼다.
CREATE TABLE llm_calls (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    step            TEXT    NOT NULL CHECK (step IN ('topic', 'flow', 'plan', 'subtitle')),
    provider        TEXT    NOT NULL DEFAULT 'anthropic',
    model           TEXT    NOT NULL,                 -- 요청 모델
    served_model    TEXT,                             -- 응답 모델(서버측 fallback 이면 다름)
    effort          TEXT,
    input_sha256    TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'started'
                    CHECK (status IN ('started', 'ok', 'refused', 'invalid', 'error')),
    stop_reason     TEXT,
    usage_in        INTEGER CHECK (usage_in IS NULL OR usage_in >= 0),     -- 입력 토큰 수(열 이름에 비밀 단어 금지 규칙)
    usage_out       INTEGER CHECK (usage_out IS NULL OR usage_out >= 0),   -- 출력 토큰 수
    cost_usd        REAL    NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    request_id      TEXT,
    error           TEXT,                             -- 마스킹·길이 제한한 오류 요지
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at     TEXT
);

CREATE INDEX idx_llm_calls_job ON llm_calls(job_id, step);
CREATE INDEX idx_llm_calls_created ON llm_calls(created_at);
-- 같은 입력의 진행 중·성공·거절 호출은 1건만(중복 과금 방지). invalid·error 뒤 재호출은 허용.
CREATE UNIQUE INDEX idx_llm_calls_dedup ON llm_calls(job_id, step, input_sha256)
    WHERE status IN ('started', 'ok', 'refused');
