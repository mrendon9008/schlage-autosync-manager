"""
Sync system database schema and operations.
Designed for Schlage lock code mirroring from master locks to peer locks within groups.
"""

# ─── SCHEMA ─────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- Tracks user-configured sync schedules (one per group)
CREATE TABLE IF NOT EXISTS sync_schedules (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id           INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    enabled            INTEGER NOT NULL DEFAULT 1,
    -- cronspec e.g. "0 */15 * * * *"  (minutely/hourly/daily)
    -- Keep it simple: stored as raw text, interpreted by the scheduler
    cronspec           TEXT NOT NULL DEFAULT '0 */15 * * * *',
    last_run_at        TEXT,   -- ISO8601 of last successful execution
    next_run_at        TEXT,   -- ISO8601 of next scheduled run
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(group_id)
);

-- Log of every sync execution
CREATE TABLE IF NOT EXISTS sync_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id        INTEGER REFERENCES sync_schedules(id) ON DELETE SET NULL,
    group_id           INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    started_at         TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at       TEXT,
    status             TEXT NOT NULL DEFAULT 'running',  -- running|success|failed|partial'
    master_lock_id     TEXT NOT NULL,
    codes_checked      INTEGER NOT NULL DEFAULT 0,
    codes_created     INTEGER NOT NULL DEFAULT 0,
    codes_updated     INTEGER NOT NULL DEFAULT 0,
    codes_deleted     INTEGER NOT NULL DEFAULT 0,
    codes_skipped     INTEGER NOT NULL DEFAULT 0,
    errors             TEXT,  -- JSON array of error messages
    dry_run            INTEGER NOT NULL DEFAULT 0  -- preview mode, no actual changes
);

-- Maps master-code + target-lock pairs; the record of what was synced
CREATE TABLE IF NOT EXISTS sync_code_history (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_run_id              INTEGER REFERENCES sync_runs(id) ON DELETE CASCADE,
    group_id                 INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    -- Which master lock this code came from
    source_lock_id           TEXT NOT NULL,
    source_code_id           INTEGER,   -- FK to access_codes.id on master (NULL if master code was deleted)
    -- Stable identity: code name + code value hashed. Even if master code is deleted,
    -- we can still identify which target codes came from this source.
    source_code_name         TEXT NOT NULL,
    source_code_value        TEXT NOT NULL,  -- actual code value at time of sync
    -- Which target lock this code was synced to
    target_lock_id           TEXT NOT NULL,
    target_code_id           INTEGER,   -- FK to access_codes.id on target lock (filled after create)
    action                   TEXT NOT NULL,  -- created|updated|deleted|synced
    status                   TEXT NOT NULL,  -- success|failed|skipped
    skip_reason              TEXT,           -- populated when status=skipped
    -- Mirror of the code's schedule at sync time (for reference/auditing)
    source_is_always_valid   INTEGER NOT NULL DEFAULT 1,
    source_start_datetime    TEXT,
    source_end_datetime      TEXT,
    created_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Index for fast lookup of what was synced to a given target
CREATE INDEX IF NOT EXISTS idx_sch_target
    ON sync_code_history(target_lock_id, source_code_name);

-- Index for fast lookup of what came from a given source lock
CREATE INDEX IF NOT EXISTS idx_sch_source
    ON sync_code_history(source_lock_id, source_code_name);

-- Index for finding history by (name, value) identity
CREATE INDEX IF NOT EXISTS idx_sch_identity
    ON sync_code_history(source_code_name, source_code_value);
"""

# ─── ACCESS_CODES TABLE MIGRATIONS ──────────────────────────────────────────
# These columns must be added to the existing `access_codes` table:

ACCESS_CODES_ALTER = """
-- Add to access_codes table:
ALTER TABLE access_codes ADD COLUMN is_synced         INTEGER NOT NULL DEFAULT 0;
ALTER TABLE access_codes ADD COLUMN sync_opt_out      INTEGER NOT NULL DEFAULT 0;  -- per-code opt-out
ALTER TABLE access_codes ADD COLUMN synced_from_code_id  INTEGER;                   -- FK to master access_codes.id
ALTER TABLE access_codes ADD COLUMN synced_from_lock_id  TEXT;                       -- lock_id of source
"""

# ─── COLUMN REFERENCES (for use in INSERT/SELECT queries) ───────────────────
COLS_SYNC_RUN = [
    "id", "schedule_id", "group_id", "started_at", "completed_at",
    "status", "master_lock_id", "codes_checked", "codes_created",
    "codes_updated", "codes_deleted", "codes_skipped", "errors", "dry_run",
]

COLS_SYNC_CODE_HISTORY = [
    "id", "sync_run_id", "group_id", "source_lock_id", "source_code_id",
    "source_code_name", "source_code_value", "target_lock_id", "target_code_id",
    "action", "status", "skip_reason",
    "source_is_always_valid", "source_start_datetime", "source_end_datetime",
    "created_at",
]
