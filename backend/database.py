"""SQLite database setup and helpers."""

import sqlite3
import os
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "schlage.db"


def get_db() -> sqlite3.Connection:
    """Return a raw sqlite3 connection to the database."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_cursor():
    """Context manager yielding a cursor; commits on success, rolls back on error."""
    conn = get_db()
    try:
        yield conn.cursor()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize the database schema."""
    with db_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                encrypted_password BLOB NOT NULL,
                nonce BLOB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_token   TEXT    NOT NULL UNIQUE,
                username       TEXT    NOT NULL,
                created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
                expires_at     TEXT    NOT NULL,
                last_active_at TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (username) REFERENCES credentials(username)
            )
        """)
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_token ON user_sessions(session_token)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_username ON user_sessions(username)"
        )
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `groups` (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS group_locks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                lock_id TEXT NOT NULL,
                lock_name TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(group_id, lock_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS access_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                code_value TEXT NOT NULL,
                group_id INTEGER REFERENCES groups(id) ON DELETE SET NULL,
                is_always_valid INTEGER NOT NULL DEFAULT 0,
                start_datetime TEXT,
                end_datetime TEXT,
                schlage_lock_id TEXT NOT NULL,
                schlage_code_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, schlage_lock_id)
            )
        """)


def migrate_sync_schema() -> None:
    """Run sync-system migrations. Safe to call repeatedly (uses IF NOT EXISTS / IF NOT EXISTS)."""
    with db_cursor() as cur:
        # ── Add is_master to group_locks (needed by groups.py routes) ─────────
        cur.execute("PRAGMA table_info(group_locks)")
        existing_cols = {r[1] for r in cur.fetchall()}
        if "is_master" not in existing_cols:
            cur.execute("ALTER TABLE group_locks ADD COLUMN is_master INTEGER NOT NULL DEFAULT 0")

        # ── Add sync columns to access_codes ──────────────────────────────────
        cur.execute("PRAGMA table_info(access_codes)")
        ac_cols = {r[1] for r in cur.fetchall()}
        if "is_synced" not in ac_cols:
            cur.execute("ALTER TABLE access_codes ADD COLUMN is_synced INTEGER NOT NULL DEFAULT 0")
        if "sync_opt_out" not in ac_cols:
            cur.execute("ALTER TABLE access_codes ADD COLUMN sync_opt_out INTEGER NOT NULL DEFAULT 0")
        if "synced_from_code_id" not in ac_cols:
            cur.execute("ALTER TABLE access_codes ADD COLUMN synced_from_code_id INTEGER")
        if "synced_from_lock_id" not in ac_cols:
            cur.execute("ALTER TABLE access_codes ADD COLUMN synced_from_lock_id TEXT")

        # ── Create sync_schedules ────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_schedules (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id           INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                enabled            INTEGER NOT NULL DEFAULT 1,
                check_times        TEXT NOT NULL DEFAULT '[]',
                master_lock_id     TEXT,
                cronspec           TEXT NOT NULL DEFAULT '0 */15 * * * *',
                last_run_at        TEXT,
                next_run_at        TEXT,
                created_at         TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(group_id)
            )
        """)

        # ── Create sync_runs ───────────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_runs (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id        INTEGER REFERENCES sync_schedules(id) ON DELETE SET NULL,
                group_id           INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                status             TEXT NOT NULL DEFAULT 'running',
                triggered_by       TEXT NOT NULL DEFAULT 'manual',
                started_at         TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at       TEXT,
                result_summary     TEXT,
                master_lock_id     TEXT NOT NULL,
                codes_checked      INTEGER NOT NULL DEFAULT 0,
                codes_created      INTEGER NOT NULL DEFAULT 0,
                codes_updated      INTEGER NOT NULL DEFAULT 0,
                codes_deleted      INTEGER NOT NULL DEFAULT 0,
                codes_skipped      INTEGER NOT NULL DEFAULT 0,
                errors             TEXT,
                dry_run            INTEGER NOT NULL DEFAULT 0
            )
        """)

        # ── Create sync_code_history ─────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sync_code_history (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_run_id              INTEGER REFERENCES sync_runs(id) ON DELETE CASCADE,
                group_id                 INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                source_lock_id           TEXT NOT NULL,
                source_code_id           INTEGER,
                source_code_name         TEXT NOT NULL,
                source_code_value        TEXT NOT NULL,
                target_lock_id           TEXT NOT NULL,
                target_code_id           INTEGER,
                action                   TEXT NOT NULL,
                status                   TEXT NOT NULL,
                skip_reason              TEXT,
                source_is_always_valid   INTEGER NOT NULL DEFAULT 1,
                source_start_datetime    TEXT,
                source_end_datetime      TEXT,
                created_at               TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # ── Indexes ─────────────────────────────────────────────────────────────────
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sch_target ON sync_code_history(target_lock_id, source_code_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sch_source ON sync_code_history(source_lock_id, source_code_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sch_identity ON sync_code_history(source_code_name, source_code_value)")


def delete_expired_sessions() -> int:
    """Delete all sessions whose expires_at < now. Returns count deleted."""
    with db_cursor() as cur:
        cur.execute(
            "DELETE FROM user_sessions WHERE expires_at < datetime('now')"
        )
        return cur.rowcount


def migrate_session_auth() -> None:
    """Run once. Adds user_sessions table and is_owner column. Safe to call repeatedly."""
    with db_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_token   TEXT    NOT NULL UNIQUE,
                username       TEXT    NOT NULL,
                created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
                expires_at     TEXT    NOT NULL,
                last_active_at TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (username) REFERENCES credentials(username)
            )
        """)
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_token "
            "ON user_sessions(session_token)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_username "
            "ON user_sessions(username)"
        )
        cur.execute("PRAGMA table_info(credentials)")
        cols = {r[1] for r in cur.fetchall()}
        if "is_owner" not in cols:
            cur.execute(
                "ALTER TABLE credentials ADD COLUMN is_owner INTEGER NOT NULL DEFAULT 0"
            )
            cur.execute(
                "UPDATE credentials SET is_owner = 1 "
                "WHERE id = (SELECT MIN(id) FROM credentials)"
            )


# Ensure schema exists on import
init_db()
