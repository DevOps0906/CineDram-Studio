-- 0001_init.sql — 1단계 스키마 (docs/16 ADR-004 "1단계 확정 범위")
-- 적용된 뒤에는 이 파일을 고치지 않는다(체크섬 검사). 바꿀 때는 0002_*.sql 새 파일.
-- 연결 설정(PRAGMA foreign_keys=ON, WAL, busy_timeout)은 db/migrate.py connect()가 건다.
-- 비밀·토큰·API 키는 어떤 테이블에도 저장하지 않는다(참조 ID만). 시각은 UTC ISO-8601 문자열.
-- schema_migrations 는 db/migrate.py 가 만든다.

-- 작품 (docs/01 §3 works)
CREATE TABLE works (
    id              INTEGER PRIMARY KEY,
    title           TEXT    NOT NULL,                 -- 작품명
    aliases         TEXT    NOT NULL DEFAULT '[]',    -- 별칭[] (JSON 배열)
    genre           TEXT,
    source_dir      TEXT,                             -- 원본 폴더 경로
    episode_count   INTEGER CHECK (episode_count IS NULL OR episode_count >= 0),
    srt_episodes    TEXT    NOT NULL DEFAULT '[]',    -- 자막 보유 회차[] (JSON 배열)
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 원본 파일 (docs/01 §3 sources, R9-1: unverified 는 업로드 예약 차단)
CREATE TABLE sources (
    id              INTEGER PRIMARY KEY,
    work_id         INTEGER NOT NULL REFERENCES works(id) ON DELETE RESTRICT,
    episode         INTEGER,
    file_path       TEXT    NOT NULL,
    srt_path        TEXT,
    duration_sec    REAL    CHECK (duration_sec IS NULL OR duration_sec >= 0),
    origin          TEXT    NOT NULL
                    CHECK (origin IN ('local_file', 'own_youtube', 'licensed', 'external_youtube')),
    rights_status   TEXT    NOT NULL DEFAULT 'unverified'
                    CHECK (rights_status IN ('own', 'licensed', 'unverified')),
    rights_note     TEXT,                             -- 라이선스 출처·계약 메모(비밀 금지)
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 제작 작업 (docs/01 §2·§3 jobs, docs/14 §4 큐 필드)
CREATE TABLE jobs (
    id              INTEGER PRIMARY KEY,
    link_id         INTEGER,                          -- links 테이블은 후속 단계(FK 없음, nullable)
    source_id       INTEGER REFERENCES sources(id) ON DELETE RESTRICT,
    rule_set        TEXT    NOT NULL DEFAULT '[]',    -- 규칙 세트[] (JSON 배열)
    topic_mode      TEXT,                             -- 소재 모드(값 목록 미확정이라 CHECK 없음)
    channel_id      TEXT,                             -- 채널 참조 문자열(nullable, 토큰 아님)
    speed           TEXT    CHECK (speed IS NULL OR speed IN ('fast', 'review')),
    exec_mode       TEXT    NOT NULL DEFAULT 'auto_render'
                    CHECK (exec_mode IN ('auto_render', 'manual', 'ai_assistant')),  -- docs/13 §16
    upload_mode     TEXT    NOT NULL DEFAULT 'review_first'
                    CHECK (upload_mode IN ('review_first', 'auto_schedule')),  -- docs/14 §3·docs/13 §13 안 A, 기본은 검수 후
    state           TEXT    NOT NULL DEFAULT 'queued'
                    CHECK (state IN ('queued', 'matching', 'need_source', 'topic', 'building',
                                     'review', 'approved', 'scheduled', 'uploading', 'blocked_auth',
                                     'done', 'blocked_disk', 'failed', 'canceled')),
    -- 대기 큐 필드: 제안(docs/14 §4). queue_state 는 작업 상태와 별개의 실행 차례.
    -- 중복 실행 방지: UPDATE jobs SET queue_state='running', slot_id=? WHERE id=? AND queue_state='waiting'
    queue_state     TEXT    NOT NULL DEFAULT 'waiting'   -- 제안
                    CHECK (queue_state IN ('waiting', 'running', 'held_human', 'held_resource',
                                           'paused', 'finished')),
    priority        INTEGER NOT NULL DEFAULT 0,          -- 제안: 순서 변경은 이 값만 바꾼다
    slot_id         TEXT,                                -- 제안: 점유 슬롯(running 일 때만)
    hold_reason     TEXT,                                -- 제안: held_* 사유
    plan_hash       TEXT,                                -- 승인된 컷표 해시(정본은 엔진 기획승인.json)
    tts_hash        TEXT,                                -- 승인 시점 TTS 해시(정본은 엔진 기획승인.json)
    started_at      TEXT,
    finished_at     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_jobs_queue ON jobs(queue_state, priority);

-- 상태 전이 기록 (docs/01 §3 job_events). 메시지·오류에 비밀·토큰을 쓰지 않는다.
CREATE TABLE job_events (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    at              TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    from_state      TEXT,                             -- 첫 기록은 NULL
    to_state        TEXT    NOT NULL,
    message         TEXT,
    render_count    INTEGER CHECK (render_count IS NULL OR render_count >= 0),
    error           TEXT
);

CREATE INDEX idx_job_events_job ON job_events(job_id);

-- 소재 기록 (docs/01 §3 topics)
CREATE TABLE topics (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source_start    REAL,                             -- 원본 구간 시작(초)
    source_end      REAL,                             -- 원본 구간 끝(초)
    hook_line       TEXT,                             -- 후킹 대사
    title           TEXT,
    tone_primary    TEXT,                             -- 결 태그(주)
    tone_secondary  TEXT,                             -- 결 태그(보조)
    score           REAL,
    used            INTEGER NOT NULL DEFAULT 0 CHECK (used IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (source_start IS NULL OR source_end IS NULL OR source_end >= source_start)
);

CREATE INDEX idx_topics_job ON topics(job_id);

-- 기획 승인 기록. 정본은 엔진 gate 가 읽는 기획승인.json 이고, 이 테이블은 조회·감사용 사본이다.
-- 불일치하면 파일을 따른다(docs/16 ADR-004). 승인은 사람만.
CREATE TABLE approvals (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    plan_hash       TEXT    NOT NULL,
    tts_hash        TEXT,
    approved_by     TEXT    NOT NULL,
    approved_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_approvals_job ON approvals(job_id);

-- 산출물 (docs/00 §9 outputs)
CREATE TABLE outputs (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    work_code       TEXT,
    render_ver      INTEGER NOT NULL DEFAULT 1 CHECK (render_ver >= 1),
    file_path       TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_outputs_job ON outputs(job_id);
