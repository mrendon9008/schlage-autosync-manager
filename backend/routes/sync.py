"""
Sync management API routes.

Provides endpoints for:
  - Managing sync schedules (create, list, delete)
  - Previewing and executing sync runs for a group
  - Viewing sync run history and detailed results
  - Toggling per-code opt-out flags
"""

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional, List

from backend.database import init_db, get_db

from fastapi import APIRouter, HTTPException, Query, Request, status, Body

from ..auth import get_current_session, decrypt_password
from ..database import db_cursor, DB_PATH

logger = logging.getLogger(__name__)
router = APIRouter(tags=["sync"])


# ─── Auth helper ──────────────────────────────────────────────────────────────

def _require_auth(request: Request):
    session = get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if session._encrypted_password is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    session.login()
    return session


def _get_session_creds(session) -> dict:
    """Build a creds dict from the current session for SyncEngine."""
    password = decrypt_password(
        session._encrypted_password,
        session._nonce,
    )
    # Get username from stored credentials (Auth object may not have it)
    init_db()
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT username FROM credentials ORDER BY id DESC LIMIT 1"
        ).fetchone()
        username = row["username"] if row else session._username
    finally:
        conn.close()
    return {"username": username, "password": password}


# ─── Response models ──────────────────────────────────────────────────────────

class ScheduleItem(dict):
    """Dict-based sync schedule item."""
    pass


class SyncRunItem(dict):
    """Dict-based sync run summary item."""
    pass


class SyncDetailItem(dict):
    """Dict-based individual code sync history item."""
    pass


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_sync_engine(session):
    """Build and return a SyncEngine wired to the current session."""
    from ..sync_logic import SyncEngine
    import pyschlage
    creds = _get_session_creds(session)
    return SyncEngine(str(DB_PATH), pyschlage, creds)


def _load_schedule(cur: sqlite3.Cursor, schedule_id: int) -> Optional[sqlite3.Row]:
    cur.execute(
        "SELECT id, group_id, enabled, cronspec, last_run_at, next_run_at, created_at "
        "FROM sync_schedules WHERE id = ?",
        (schedule_id,),
    )
    return cur.fetchone()


def _load_runs_for_group(cur: sqlite3.Cursor, group_id: int, limit: int = 50) -> list:
    cur.execute(
        "SELECT id, schedule_id, group_id, started_at, completed_at, status, "
        "       master_lock_id, codes_checked, codes_created, codes_updated, "
        "       codes_deleted, codes_skipped, errors, dry_run "
        "FROM sync_runs WHERE group_id = ? "
        "ORDER BY started_at DESC LIMIT ?",
        (group_id, limit),
    )
    return [dict(row) for row in cur.fetchall()]


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.get("/schedules")
def list_schedules(request: Request) -> dict:
    """Return all sync schedules."""
    _require_auth(request)
    with db_cursor() as cur:
        cur.execute(
            "SELECT id, group_id, enabled, cronspec, check_times, master_lock_id, last_run_at, next_run_at, created_at "
            "FROM sync_schedules ORDER BY created_at DESC"
        )
        rows = cur.fetchall()
    return {
        "schedules": [
            {
                "id": r["id"],
                "group_id": r["group_id"],
                "enabled": bool(r["enabled"]),
                "cronspec": r["cronspec"],
                "check_times": r["check_times"] or "[]",
                "master_lock_id": r["master_lock_id"],
                "last_run_at": r["last_run_at"],
                "next_run_at": r["next_run_at"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
def upsert_schedule(request: Request,
    group_id: int = Query(...),
    cronspec: str = Query(default="0 */15 * * * *"),
    enabled: bool = Query(default=True),
    check_time: str = Query(default=None),
    check_times: Optional[dict] = Body(default=None),
    master_lock_id: str = Query(default=None),
) -> dict:
    """
    Create or update a sync schedule for a group.

    One schedule per group — re-creating updates the existing schedule.
    Pass check_time to add a single time to the schedule's check_times list.
    Pass check_times (JSON array) to set/replace all check_times at once.
    Pass master_lock_id to set the master lock for this group.
    """
    import json
    session = _require_auth(request)

    # Verify group exists
    with db_cursor() as cur:
        cur.execute("SELECT id FROM `groups` WHERE id = ?", (group_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Group not found")

    with db_cursor() as cur:
        if check_times is not None and isinstance(check_times, dict) and "check_times" in check_times:
            # Bulk set — replace all check_times with the provided array
            check_times_json = json.dumps(check_times["check_times"])
        elif check_time:
            # Add single time to existing or new schedule
            cur.execute("SELECT check_times FROM sync_schedules WHERE group_id = ?", (group_id,))
            row = cur.fetchone()
            times = json.loads(row["check_times"]) if row and row["check_times"] else []
            if check_time not in times:
                times.append(check_time)
            check_times_json = json.dumps(times)
        else:
            # Load existing (no change requested)
            cur.execute("SELECT check_times FROM sync_schedules WHERE group_id = ?", (group_id,))
            row = cur.fetchone()
            check_times_json = row["check_times"] if row and row["check_times"] else "[]" if row else "[]"

        cur.execute(
            """
            INSERT INTO sync_schedules (group_id, enabled, cronspec, check_times, master_lock_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                enabled = excluded.enabled,
                cronspec = excluded.cronspec,
                check_times = COALESCE(excluded.check_times, sync_schedules.check_times),
                master_lock_id = CASE WHEN excluded.master_lock_id IS NOT NULL THEN excluded.master_lock_id ELSE sync_schedules.master_lock_id END
            """,
            (group_id, int(enabled), cronspec, check_times_json, master_lock_id),
        )
        cur.execute(
            "SELECT id, group_id, enabled, cronspec, check_times, master_lock_id, last_run_at, next_run_at, created_at "
            "FROM sync_schedules WHERE group_id = ?",
            (group_id,),
        )
        row = cur.fetchone()

    return {
        "message": "Schedule saved",
        "schedule": {
            "id": row["id"],
            "group_id": row["group_id"],
            "enabled": bool(row["enabled"]),
            "cronspec": row["cronspec"],
            "check_times": row["check_times"] or "[]",
            "master_lock_id": row["master_lock_id"],
            "last_run_at": row["last_run_at"],
            "next_run_at": row["next_run_at"],
            "created_at": row["created_at"],
        },
    }


@router.delete("/schedules/{group_id}")
def delete_schedule(request: Request, group_id: int, time: str = Query(default=None)) -> dict:
    """Remove a specific time from a sync schedule, or delete the whole schedule if no time given."""
    _require_auth(request)

    if time:
        # Remove just this time from check_times JSON array
        import json
        with db_cursor() as cur:
            cur.execute("SELECT check_times FROM sync_schedules WHERE group_id = ?", (group_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Schedule not found")
            times = json.loads(row["check_times"]) if row["check_times"] else []
            if time in times:
                times.remove(time)
            cur.execute(
                "UPDATE sync_schedules SET check_times = ? WHERE group_id = ?",
                (json.dumps(times), group_id),
            )
        return {"message": f"Time {time} removed"}
    else:
        with db_cursor() as cur:
            cur.execute(
                "DELETE FROM sync_schedules WHERE group_id = ?",
                (group_id,),
            )
            deleted = cur.rowcount

        if not deleted:
            raise HTTPException(status_code=404, detail="Schedule not found")

        return {"message": "Schedule deleted"}


@router.get("/preview/{group_id}")
def preview_sync(request: Request, group_id: int) -> dict:
    """
    Dry-run: show what a sync would do without making any changes.

    Returns specific codes to be created/updated/deleted, plus counts.
    """
    session = _require_auth(request)

    with db_cursor() as cur:
        cur.execute("SELECT id FROM `groups` WHERE id = ?", (group_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Group not found")

    engine = _get_sync_engine(session)
    result = engine.preview(group_id)
    run_id = result.get("run_id") or result.to_dict().get("run_id")

    # Fetch specific code changes from the dry-run sync_code_history records
    to_create = []
    to_update = []
    to_delete = []
    if run_id and run_id > 0:
        with db_cursor() as cur:
            rows = cur.execute(
                """SELECT source_code_name, source_code_value, target_lock_id,
                             action, status, skip_reason
                      FROM sync_code_history WHERE sync_run_id = ? ORDER BY action""",
                (run_id,),
            ).fetchall()
            for r in rows:
                item = {
                    "code_name": r["source_code_name"],
                    "code_value": r["source_code_value"],
                    "target_lock_id": r["target_lock_id"],
                    "status": r["status"],
                    "skip_reason": r["skip_reason"],
                }
                if r["action"] == "created":
                    to_create.append(item)
                elif r["action"] == "updated":
                    to_update.append(item)
                elif r["action"] == "deleted":
                    to_delete.append(item)

    result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result)

    # Fetch newly discovered codes (from this dry-run's discover_codes call) to include
    # in to_create — these are codes in access_codes with is_synced=0 that belong
    # to the master lock but have no synced_from values yet
    to_create_new = []
    master_lock_id = None
    with db_cursor() as cur:
        cur.execute(
            "SELECT lock_id FROM group_locks WHERE group_id = ? AND is_master = 1 LIMIT 1",
            (group_id,),
        )
        row = cur.fetchone()
        master_lock_id = row["lock_id"] if row else None
    if master_lock_id:
        with db_cursor() as cur:
            cur.execute(
                """SELECT name, code_value, schlage_code_id, is_always_valid,
                           start_datetime, end_datetime
                    FROM access_codes
                    WHERE schlage_lock_id = ?
                      AND synced_from_code_id IS NULL
                      AND sync_opt_out = 0
                      AND is_synced = 0""",
                (master_lock_id,),
            )
            for r in cur.fetchall():
                to_create_new.append({
                    "code_name": r["name"],
                    "code_value": r["code_value"],
                    "schlage_code_id": r["schlage_code_id"],
                    "target_lock_id": master_lock_id,
                    "status": "new",
                    "skip_reason": None,
                })

    return {
        **result_dict,
        "to_create": to_create_new,
        "to_update": to_update,
        "to_delete": to_delete,
    }


@router.post("/run/{group_id}")
def run_sync(request: Request, 
    group_id: int,
    dry_run: bool = Query(default=False),
    body: dict = Body(default={}),
) -> dict:
    """
    Execute pending sync_jobs for a group (Force Sync).

    Set dry_run=true to preview without making any actual changes.
    Body can include opt_outs: list of {code_name, target_lock_id} to skip.
    """
    # Delegate to the sync_jobs-based implementation
    return run_sync_from_jobs(request, group_id, dry_run, body)


@router.get("/history/{group_id}")
def sync_history(request: Request, group_id: int, limit: int = Query(default=50)) -> dict:
    """Return sync run history for a group."""
    _require_auth(request)

    rows = []
    with db_cursor() as cur:
        cur.execute("SELECT id FROM `groups` WHERE id = ?", (group_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Group not found")
        rows = _load_runs_for_group(cur, group_id, limit)

    return {
        "group_id": group_id,
        "runs": [
            {
                "id": r["id"],
                "schedule_id": r["schedule_id"],
                "group_id": r["group_id"],
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
                "status": r["status"],
                "master_lock_id": r["master_lock_id"],
                "codes_checked": r["codes_checked"],
                "codes_created": r["codes_created"],
                "codes_updated": r["codes_updated"],
                "codes_deleted": r["codes_deleted"],
                "codes_skipped": r["codes_skipped"],
                "errors": r["errors"],
                "dry_run": bool(r["dry_run"]),
            }
            for r in rows
        ],
    }


@router.get("/history/{group_id}/details/{run_id}")
def sync_run_details(request: Request, group_id: int, run_id: int) -> dict:
    """Return detailed per-code sync history for a specific run."""
    _require_auth(request)

    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM sync_runs WHERE id = ? AND group_id = ?",
            (run_id, group_id),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Sync run not found")

    with db_cursor() as cur:
        cur.execute(
            "SELECT id, sync_run_id, group_id, source_lock_id, source_code_id, "
            "       source_code_name, source_code_value, target_lock_id, target_code_id, "
            "       action, status, skip_reason, "
            "       source_is_always_valid, source_start_datetime, source_end_datetime, "
            "       created_at "
            "FROM sync_code_history "
            "WHERE sync_run_id = ? "
            "ORDER BY created_at ASC",
            (run_id,),
        )
        rows = cur.fetchall()

    return {
        "run_id": run_id,
        "group_id": group_id,
        "details": [
            {
                "id": r["id"],
                "sync_run_id": r["sync_run_id"],
                "source_lock_id": r["source_lock_id"],
                "source_code_id": r["source_code_id"],
                "source_code_name": r["source_code_name"],
                "source_code_value": r["source_code_value"],
                "target_lock_id": r["target_lock_id"],
                "target_code_id": r["target_code_id"],
                "action": r["action"],
                "status": r["status"],
                "skip_reason": r["skip_reason"],
                "source_is_always_valid": bool(r["source_is_always_valid"]),
                "source_start_datetime": r["source_start_datetime"],
                "source_end_datetime": r["source_end_datetime"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


@router.put("/codes/{code_id}/opt-out")
def toggle_code_opt_out(request: Request, code_id: int, opt_out: bool = Query(...)) -> dict:
    """
    Set or clear the sync_opt_out flag on an access code.

    When opt_out=True, the code will be skipped during sync operations.
    """
    _require_auth(request)

    with db_cursor() as cur:
        cur.execute(
            "SELECT id, name, sync_opt_out FROM access_codes WHERE id = ?",
            (code_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Code not found")

        cur.execute(
            "UPDATE access_codes SET sync_opt_out = ? WHERE id = ?",
            (int(opt_out), code_id),
        )

    return {
        "message": "Opt-out updated",
        "code_id": code_id,
        "code_name": row["name"],
        "sync_opt_out": opt_out,
    }


# ─── Sync Jobs ─────────────────────────────────────────────────────────────────

@router.get("/jobs/{group_id}")
def list_sync_jobs(request: Request, group_id: int) -> dict:
    """
    Return all sync_jobs for a group, with access code name, code value,
    target lock name, action, and state.
    """
    _require_auth(request)

    with db_cursor() as cur:
        cur.execute("SELECT id FROM `groups` WHERE id = ?", (group_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Group not found")

        cur.execute(
            """SELECT
                  sj.id,
                  sj.group_id,
                  sj.access_code_id,
                  sj.target_lock_id,
                  sj.action,
                  sj.state,
                  sj.error_message,
                  sj.created_at,
                  sj.updated_at,
                  sj.completed_at,
                  COALESCE(sj.code_name, ac.name) AS code_name,
                  ac.code_value  AS code_value,
                  gl.lock_name   AS target_lock_name
               FROM sync_jobs sj
               LEFT JOIN access_codes ac ON ac.id = sj.access_code_id
               JOIN group_locks  gl ON gl.id = sj.target_lock_id
               WHERE sj.group_id = ? AND sj.state != 'deleted'
               ORDER BY sj.created_at DESC""",
            (group_id,),
        )
        rows = cur.fetchall()

    return {
        "group_id": group_id,
        "jobs": [
            {
                "id": r["id"],
                "group_id": r["group_id"],
                "access_code_id": r["access_code_id"],
                "target_lock_id": r["target_lock_id"],
                "action": r["action"],
                "state": r["state"],
                "error_message": r["error_message"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "completed_at": r["completed_at"],
                "code_name": r["code_name"],
                "code_value": r["code_value"],
                "target_lock_name": r["target_lock_name"],
            }
            for r in rows
        ],
    }


@router.delete("/jobs/{job_id}")
def delete_sync_job(request: Request, job_id: int) -> dict:
    """Set a sync_job state to 'deleted'."""
    _require_auth(request)

    with db_cursor() as cur:
        cur.execute(
            "UPDATE sync_jobs SET state = 'deleted', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND state != 'deleted'"
            "RETURNING id",
            (job_id,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found or already deleted")

    return {"message": "Job deleted", "job_id": job_id}


@router.post("/jobs/bulk-delete")
def bulk_delete_sync_jobs(request: Request, body: dict = Body(...)) -> dict:
    """Set multiple sync_jobs to state='deleted'. Body: {job_ids: [1, 2, 3]}"""
    _require_auth(request)

    job_ids = body.get("job_ids", [])
    if not job_ids:
        return {"message": "No jobs specified", "deleted": 0}

    placeholders = ",".join("?" * len(job_ids))
    with db_cursor() as cur:
        cur.execute(
            f"UPDATE sync_jobs SET state = 'deleted', updated_at = CURRENT_TIMESTAMP "
            f"WHERE id IN ({placeholders}) AND state != 'deleted'",
            job_ids,
        )
        deleted = cur.rowcount

    return {"message": f"{deleted} job(s) deleted", "deleted": deleted}


def run_sync_from_jobs(
    request: Request,
    group_id: int,
    dry_run: bool = Query(default=False),
    body: dict = Body(default={}),
) -> dict:

    # ── Acquire sync lock to prevent concurrent runs ─────────────────────
    import os, uuid
    lock_path = f"/tmp/schlage_sync_lock_{group_id}.lock"
    lock_acquired = False
    try:
        # Try to create lock file (exclusive)
        with open(lock_path, 'w') as lockf:
            lockf.write(str(os.getpid()))
        lock_acquired = True
    except IOError:
        # Lock file exists — another sync is running
        return {"status": "locked", "message": "Sync already in progress for this group"}
    finally:
        if lock_acquired and os.path.exists(lock_path):
            os.remove(lock_path)
    # ───────────────────────────────────────────────────────────────────

    """
    Execute pending sync_jobs for a group using the sync_jobs queue.

    For each pending job:
      - 'create': call Schlage API to add code to target lock
      - 'update': delete old code from target lock, then create new code
      - 'delete': call Schlage API to delete code from target lock

    On success: state='completed' | On failure: state='failed' with error_message.
    After each job, writes to sync_code_history with sync_job_id.

    opt_outs in body: list of {code_name, target_lock_id} to skip.
    """
    session = _require_auth(request)

    with db_cursor() as cur:
        cur.execute("SELECT id FROM `groups` WHERE id = ?", (group_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Group not found")

    # Build excluded set from opt_outs
    excluded_codes = set()
    for opt in (body.get("opt_outs") or []):
        code_name = opt.get("code_name", "")
        target_id = opt.get("target_lock_id", "")
        if code_name and target_id:
            excluded_codes.add(f"{code_name}:{target_id}")

    # Get all pending jobs for this group (jobs are marked completed after execution)
    with db_cursor() as cur:
        cur.execute(
            """SELECT
                  sj.id            AS job_id,
                  sj.access_code_id,
                  sj.target_lock_id,
                  sj.action,
                  COALESCE(sj.code_name, ac.name) AS code_name,
                  ac.code_value    AS code_value,
                  ac.is_always_valid,
                  ac.start_datetime,
                  ac.end_datetime,
                  ac.synced_from_code_id,
                  gl.lock_id       AS target_device_id,
                  ac.schlage_lock_id AS source_lock_id
               FROM sync_jobs sj
               LEFT JOIN access_codes ac ON ac.id = sj.access_code_id
               JOIN group_locks  gl ON gl.id = sj.target_lock_id
               WHERE sj.group_id = ? AND sj.state = 'pending'
               ORDER BY sj.target_lock_id, sj.sequence""",
            (group_id,),
        )
        jobs = [dict(r) for r in cur.fetchall()]

    if not jobs:
        return {"message": "No pending jobs", "executed": 0, "created": 0, "updated": 0, "deleted": 0, "skipped": 0, "failed": 0}

    results = {
        "executed": 0,
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }

    # Authenticate with Schlage
    try:
        session.login()
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Schlage auth failed: {e}")

    # Keep a DB connection open for the whole transaction
    from backend.database import get_db
    conn = get_db()

    try:
        for job in jobs:
            job_id       = job["job_id"]
            access_code_id = job["access_code_id"]
            target_device_id = job["target_lock_id"]
            target_device_id = job["target_device_id"]
            action        = job["action"]
            code_name     = job["code_name"]
            code_value    = job["code_value"]
            is_always     = bool(job["is_always_valid"])
            start_dt      = job["start_datetime"]
            end_dt        = job["end_datetime"]
            source_lock_id = job["source_lock_id"]

            # Check opt-out
            if f"{code_name}:{target_device_id}" in excluded_codes:
                results["skipped"] += 1
                conn.execute(
                    "UPDATE sync_jobs SET state = 'skipped', updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (job_id,),
                )
                conn.execute(
                    """INSERT INTO sync_code_history
                       (group_id, source_lock_id, source_code_id, source_code_name,
                        source_code_value, target_lock_id, action, status,
                        skip_reason, source_is_always_valid, source_start_datetime,
                        source_end_datetime, sync_job_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'skipped', 'opted out', ?, ?, ?, ?)""",
                    (group_id, source_lock_id, access_code_id, code_name,
                     code_value, target_device_id, action, int(is_always),
                     start_dt, end_dt, job_id),
                )
                conn.commit()
                continue

            if dry_run:
                results["executed"] += 1
                if action == "create":
                    results["created"] += 1
                elif action == "update":
                    results["updated"] += 1
                elif action == "delete":
                    results["deleted"] += 1
                conn.execute(
                    "UPDATE sync_jobs SET state = 'pending', updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (job_id,),
                )
                conn.commit()
                continue

            # Mark job as running
            conn.execute(
                "UPDATE sync_jobs SET state = 'running', updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (job_id,),
            )
            conn.commit()

            error_msg = None
            sync_success = False
            target_code_id_record = None

            try:
                if action == "create":
                    # Clean up any existing unsynced code with the same name on this lock
                    # (was created directly on the lock, not through sync — no synced_from set)
                    existing_unsynced = conn.execute(
                        """SELECT id, schlage_code_id FROM access_codes
                           WHERE schlage_lock_id = ?
                             AND name = ?
                             AND synced_from_code_id IS NULL""",
                        (target_device_id, code_name),
                    ).fetchone()
                    if existing_unsynced and existing_unsynced["schlage_code_id"]:
                        try:
                            session.delete_access_code(
                                device_id=target_device_id,
                                code_id=existing_unsynced["schlage_code_id"],
                            )
                            conn.execute(
                                "DELETE FROM access_codes WHERE id = ?",
                                (existing_unsynced["id"],),
                            )
                            logger.info(
                                "create job %s: removed existing unsynced code '%s' (schlage_code_id=%s)",
                                job_id, code_name, existing_unsynced["schlage_code_id"],
                            )
                        except Exception as del_err:
                            logger.warning(
                                "create job %s: failed to remove existing code: %s",
                                job_id, del_err,
                            )


                    # ── UC3 guard: skip CREATE if parent code was deleted from master ──
                    parent_row = conn.execute(
                        "SELECT id FROM access_codes WHERE id = ?",
                        (access_code_id,),
                    ).fetchone()
                    if not parent_row:
                        logger.info("run_sync_from_jobs CREATE: job_id=%s skipped — parent code %s was deleted from master",
                                   job_id, access_code_id)
                        conn.execute(
                            "UPDATE sync_jobs SET state = 'skipped', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (job_id,),
                        )
                        conn.commit()
                        results["skipped"] += 1
                        continue
                    # ──────────────────────────────────────────────────────────────

                    logger.info("run_sync_from_jobs CREATE: job_id=%s device=%s name=%s code=%s",
                               job_id, target_device_id, code_name, code_value)
                    result = session.create_access_code(
                        device_id=target_device_id,
                        name=code_name,
                        code=code_value,
                        is_always_valid=is_always,
                        start_datetime=start_dt,
                        end_datetime=end_dt,
                    )
                    target_code_id_record = result.get("code_id")
                    logger.info("run_sync_from_jobs CREATE SUCCESS: job_id=%s device=%s code_id=%s",
                               job_id, target_device_id, target_code_id_record)

                    # ── Insert child code into access_codes so our DB tracks it ─
                    # Delete any existing orphan for this (master_code, target_lock) pair
                    conn.execute(
                        """DELETE FROM access_codes
                           WHERE schlage_lock_id = ?
                             AND name = ?
                             AND (synced_from_code_id = ? OR synced_from_code_id IS NULL)""",
                        (target_device_id, code_name, access_code_id),
                    )
                    # Use the group_id from the job context
                    group_id_for_child = group_id
                    # Insert child code record with linkage to master
                    conn.execute(
                        """INSERT INTO access_codes
                           (name, code_value, group_id, is_always_valid,
                            start_datetime, end_datetime, schlage_lock_id, schlage_code_id,
                            is_synced, sync_opt_out, synced_from_code_id, synced_from_lock_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)""",
                        (code_name, code_value, group_id_for_child,
                         int(is_always), start_dt, end_dt,
                         target_device_id, target_code_id_record,
                         access_code_id, source_lock_id),
                    )
                    logger.info("run_sync_from_jobs: inserted child code record for '%s' on device %s",
                               code_name, target_device_id)

                    sync_success = True

                elif action == "update":
                    # Find the existing synced code on this target lock
                    existing = conn.execute(
                        """SELECT id, schlage_code_id FROM access_codes
                           WHERE schlage_lock_id = ?
                             AND synced_from_code_id = ?
                             AND synced_from_lock_id = ?""",
                        (target_device_id, access_code_id, source_lock_id),
                    ).fetchone()

                    if existing and existing["schlage_code_id"]:
                        try:
                            session.delete_access_code(
                                device_id=target_device_id,
                                code_id=existing["schlage_code_id"],
                            )
                        except Exception as del_err:
                            logger.warning("update: delete old code failed: %s", del_err)

                    # Create new code with updated name
                    logger.info("run_sync_from_jobs CREATE: job_id=%s device=%s name=%s code=%s",
                               job_id, target_device_id, code_name, code_value)
                    result = session.create_access_code(
                        device_id=target_device_id,
                        name=code_name,
                        code=code_value,
                        is_always_valid=is_always,
                        start_datetime=start_dt,
                        end_datetime=end_dt,
                    )
                    target_code_id_record = result.get("code_id")
                    logger.info("run_sync_from_jobs CREATE SUCCESS: job_id=%s device=%s code_id=%s",
                               job_id, target_device_id, target_code_id_record)

                    # ── Update child code in access_codes ──────────────────────
                    # Find any existing code for this (lock, name) regardless of sync linkage
                    existing_child = conn.execute(
                        """SELECT id FROM access_codes
                           WHERE schlage_lock_id = ?
                             AND name = ?""",
                        (target_device_id, code_name),
                    ).fetchone()
                    if existing_child:
                        conn.execute(
                            """UPDATE access_codes SET code_value=?, is_always_valid=?,
                               start_datetime=?, end_datetime=?, schlage_code_id=?,
                               synced_from_code_id=?, synced_from_lock_id=?, is_synced=1
                               WHERE id = ?""",
                            (code_value, int(is_always), start_dt, end_dt,
                             target_code_id_record, access_code_id, source_lock_id,
                             existing_child["id"]),
                        )
                    else:
                        # No existing child — insert new one
                        group_id_for_child = group_id
                        conn.execute(
                            """INSERT INTO access_codes
                               (name, code_value, group_id, is_always_valid,
                                start_datetime, end_datetime, schlage_lock_id, schlage_code_id,
                                is_synced, sync_opt_out, synced_from_code_id, synced_from_lock_id)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)""",
                            (code_name, code_value, group_id_for_child,
                             int(is_always), start_dt, end_dt,
                             target_device_id, target_code_id_record,
                             access_code_id, source_lock_id),
                        )
                    logger.info("run_sync_from_jobs: updated child code record for '%s' on device %s",
                               code_name, target_device_id)

                    sync_success = True

                elif action == "delete":
                    # Resolve lock UUID from group_locks integer id before querying access_codes
                    gl_row = conn.execute(
                        "SELECT lock_id FROM group_locks WHERE lock_id = ?",
                        (target_device_id,),
                    ).fetchone()
                    if not gl_row:
                        raise Exception(f"group_locks id {target_device_id} not found")
                    resolved_lock_id = gl_row["lock_id"]
                    # Find the existing code on target — match by (lock, name) regardless of sync linkage
                    existing = conn.execute(
                        """SELECT id, schlage_code_id FROM access_codes
                           WHERE schlage_lock_id = ?
                             AND name = ?""",
                        (resolved_lock_id, code_name),
                    ).fetchone()

                    logger.info("run_sync_from_jobs DELETE: job_id=%s device=%s code_id=%s name=%s",
                               job_id, resolved_lock_id, existing["schlage_code_id"] if existing else None, code_name)
                    if existing and existing["schlage_code_id"]:
                        session.delete_access_code(
                            device_id=resolved_lock_id,
                            code_id=existing["schlage_code_id"],
                        )
                        logger.info("run_sync_from_jobs DELETE SUCCESS: job_id=%s device=%s",
                                   job_id, resolved_lock_id)
                        # Remove the local record
                        conn.execute(
                            "DELETE FROM access_codes WHERE id = ?",
                            (existing["id"],),
                        )
                    sync_success = True

            except Exception as e:
                error_msg = str(e)
                logger.error("sync job %s failed: %s", job_id, e)

            if sync_success:
                conn.execute(
                    """UPDATE sync_jobs
                       SET state = 'completed',
                           completed_at = CURRENT_TIMESTAMP,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (job_id,),
                )
                results["executed"] += 1
                if action == "create":
                    results["created"] += 1
                    # Set is_synced=1 on the master code after successful child creation
                    conn.execute(
                        "UPDATE access_codes SET is_synced = 1 WHERE id = ?",
                        (access_code_id,),
                    )
                elif action == "update":
                    results["updated"] += 1
                    conn.execute(
                        "UPDATE access_codes SET is_synced = 1 WHERE id = ?",
                        (access_code_id,),
                    )
                elif action == "delete":
                    results["deleted"] += 1
            else:
                conn.execute(
                    """UPDATE sync_jobs
                       SET state = 'failed',
                           error_message = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (error_msg, job_id),
                )
                results["failed"] += 1
                results["errors"].append(f"[job {job_id}] {error_msg}")

            # Write sync_code_history with sync_job_id
            history_status = "success" if sync_success else "failed"
            try:
                conn.execute(
                    """INSERT INTO sync_code_history
                       (group_id, source_lock_id, source_code_id, source_code_name,
                        source_code_value, target_lock_id, target_code_id,
                        action, status, skip_reason,
                        source_is_always_valid, source_start_datetime,
                        source_end_datetime, sync_job_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (group_id, source_lock_id, access_code_id, code_name,
                     code_value, target_device_id, target_code_id_record,
                     action, history_status, error_msg,
                     int(is_always), start_dt, end_dt, job_id),
                )
            except Exception as e:
                logger.warning("sync_code_history INSERT failed for job %s: %s", job_id, e)
            conn.commit()

    finally:
        conn.close()

    return {
        "message": "Sync complete",
        "dry_run": dry_run,
        **results,
    }

# ── Scheduler-friendly sync (no Request object) ─────────────────────────────


def _scheduler_run_sync_jobs(group_id: int, dry_run: bool = False) -> dict:
    """
    Standalone version of run_sync_from_jobs for scheduler use.
    No Request object — uses stored session token + encrypted credentials from DB.
    Auth flow: get token → get encrypted creds → build session → login.
    """
    import os
    from backend.database import get_db
    from backend.auth import get_session_by_token, decrypt_password, SchlageSession

    logger.info("_scheduler_run_sync_jobs: group=%s dry_run=%s", group_id, dry_run)

    # ── Acquire sync lock ──────────────────────────────────────────────
    lock_path = f"/tmp/schlage_sync_lock_{group_id}.lock"
    lock_acquired = False
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        lock_acquired = True
        logger.info("_scheduler_run_sync_jobs: lock acquired %s", lock_path)
    except FileExistsError:
        logger.warning("_scheduler_run_sync_jobs: lock already held for group %s", group_id)
        return {"status": "locked", "message": "Sync already in progress for this group"}
    except Exception as e:
        logger.error("_scheduler_run_sync_jobs: lock error: %s", e)
        return {"status": "error", "message": str(e)}

    try:
        # ── Auth: get token + encrypted creds in one DB connection ─────
        conn = get_db()
        try:
            row = conn.execute(
                """SELECT us.session_token, us.username
                   FROM user_sessions us
                   JOIN credentials c ON c.username = us.username
                   WHERE c.is_owner = 1
                   ORDER BY us.last_active_at DESC LIMIT 1"""
            ).fetchone()
            if not row:
                logger.error("_scheduler_run_sync_jobs: no active owner session")
                return {"status": "error", "message": "no active session found"}
            session_token = row["session_token"]
            username = row["username"]

            enc_row = conn.execute(
                "SELECT encrypted_password, nonce FROM credentials WHERE username = ?",
                (username,)
            ).fetchone()
            if not enc_row:
                logger.error("_scheduler_run_sync_jobs: no credentials found for %s", username)
                return {"status": "error", "message": "No credentials found"}
            encrypted_password = enc_row["encrypted_password"]
            nonce = enc_row["nonce"]
        finally:
            conn.close()

        # Build session with encrypted creds so login() works
        session = SchlageSession(username)
        session._encrypted_password = encrypted_password
        session._nonce = nonce
        session._session_token = session_token
        session.login()
        logger.info("_scheduler_run_sync_jobs: authenticated as %s", username)

        # ── Verify group exists ─────────────────────────────────────────
        with db_cursor() as cur:
            cur.execute("SELECT id FROM `groups` WHERE id = ?", (group_id,))
            if not cur.fetchone():
                logger.error("_scheduler_run_sync_jobs: group %s not found", group_id)
                return {"status": "error", "message": "Group not found"}

        # ── Get all pending jobs (same query as HTTP version) ───────────
        with db_cursor() as cur:
            cur.execute(
                """SELECT
                      sj.id            AS job_id,
                      sj.access_code_id,
                      sj.target_lock_id,
                      sj.action,
                      COALESCE(sj.code_name, ac.name) AS code_name,
                      ac.code_value    AS code_value,
                      ac.is_always_valid,
                      ac.start_datetime,
                      ac.end_datetime,
                      gl.lock_id       AS target_device_id,
                      ac.schlage_lock_id AS source_lock_id
                   FROM sync_jobs sj
                   LEFT JOIN access_codes ac ON ac.id = sj.access_code_id
                   JOIN group_locks  gl ON gl.id = sj.target_lock_id
                   WHERE sj.group_id = ? AND sj.state = 'pending'
                   ORDER BY sj.target_lock_id, sj.sequence""",
                (group_id,),
            )
            jobs = [dict(r) for r in cur.fetchall()]

        if not jobs:
            logger.info("_scheduler_run_sync_jobs: no pending jobs for group %s", group_id)
            return {"message": "No pending jobs", "executed": 0, "created": 0,
                   "updated": 0, "deleted": 0, "skipped": 0, "failed": 0}

        logger.info("_scheduler_run_sync_jobs: %d pending jobs for group %s", len(jobs), group_id)
        results = {"executed": 0, "created": 0, "updated": 0,
                   "deleted": 0, "skipped": 0, "failed": 0, "errors": []}

        # ── Execute jobs (identical to run_sync_from_jobs) ──────────────
        conn = get_db()
        try:
            for job in jobs:
                job_id            = job["job_id"]
                access_code_id    = job["access_code_id"]
                target_device_id = job["target_device_id"]
                action           = job["action"]
                code_name        = job["code_name"]
                code_value       = job["code_value"]
                is_always        = bool(job["is_always_valid"])
                start_dt         = job["start_datetime"]
                end_dt           = job["end_datetime"]
                source_lock_id   = job["source_lock_id"]

                if dry_run:
                    results["executed"] += 1
                    if action == "create":   results["created"] += 1
                    elif action == "update": results["updated"] += 1
                    elif action == "delete": results["deleted"] += 1
                    conn.execute(
                        "UPDATE sync_jobs SET state='pending', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                        (job_id,))
                    conn.commit()
                    continue

                conn.execute(
                    "UPDATE sync_jobs SET state='running', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (job_id,))
                conn.commit()

                error_msg          = None
                sync_success       = False
                target_code_record = None

                try:
                    if action == "create":
                        # Clean up any existing unsynced code with the same name on this lock
                        existing_unsynced = conn.execute(
                            """SELECT id, schlage_code_id FROM access_codes
                               WHERE schlage_lock_id = ? AND name = ?
                                 AND synced_from_code_id IS NULL""",
                            (target_device_id, code_name),
                        ).fetchone()
                        if existing_unsynced and existing_unsynced["schlage_code_id"]:
                            try:
                                session.delete_access_code(
                                    device_id=target_device_id,
                                    code_id=existing_unsynced["schlage_code_id"],
                                )
                                conn.execute("DELETE FROM access_codes WHERE id = ?",
                                            (existing_unsynced["id"],))
                                logger.info("create job %s: removed existing unsynced code '%s'",
                                           job_id, code_name)
                            except Exception as del_err:
                                logger.warning("create job %s: failed to remove existing code: %s",
                                               job_id, del_err)

                        # UC3 guard: skip CREATE if parent code was deleted from master
                        parent_row = conn.execute(
                            "SELECT id FROM access_codes WHERE id = ?",
                            (access_code_id,),
                        ).fetchone()
                        if not parent_row:
                            logger.info("_scheduler_run_sync_jobs CREATE: job_id=%s skipped — "
                                       "parent code %s was deleted from master",
                                       job_id, access_code_id)
                            conn.execute(
                                "UPDATE sync_jobs SET state='skipped', updated_at=CURRENT_TIMESTAMP "
                                "WHERE id=?", (job_id,))
                            conn.commit()
                            results["skipped"] += 1
                            continue

                        logger.info("_scheduler_run_sync_jobs CREATE: job_id=%s device=%s name=%s",
                                   job_id, target_device_id, code_name)
                        result = session.create_access_code(
                            device_id=target_device_id, name=code_name, code=code_value,
                            is_always_valid=is_always, start_datetime=start_dt, end_datetime=end_dt)
                        target_code_record = result.get("code_id")
                        logger.info("_scheduler_run_sync_jobs CREATE SUCCESS: job_id=%s code_id=%s",
                                   job_id, target_code_record)

                        conn.execute(
                            """DELETE FROM access_codes
                               WHERE schlage_lock_id = ? AND name = ?
                                 AND (synced_from_code_id = ? OR synced_from_code_id IS NULL)""",
                            (target_device_id, code_name, access_code_id))
                        conn.execute(
                            """INSERT INTO access_codes
                               (name, code_value, group_id, is_always_valid,
                                start_datetime, end_datetime, schlage_lock_id, schlage_code_id,
                                is_synced, sync_opt_out, synced_from_code_id, synced_from_lock_id)
                               VALUES (?,?,?,?,?,?,?,?,1,0,?,?)""",
                            (code_name, code_value, group_id, int(is_always),
                             start_dt, end_dt, target_device_id, target_code_record,
                             access_code_id, source_lock_id))
                        sync_success = True

                    elif action == "update":
                        existing = conn.execute(
                            """SELECT id, schlage_code_id FROM access_codes
                               WHERE schlage_lock_id = ? AND synced_from_code_id = ?
                                 AND synced_from_lock_id = ?""",
                            (target_device_id, access_code_id, source_lock_id),
                        ).fetchone()
                        if existing and existing["schlage_code_id"]:
                            try:
                                session.delete_access_code(
                                    device_id=target_device_id,
                                    code_id=existing["schlage_code_id"],
                                )
                            except Exception as del_err:
                                logger.warning("update: delete old code failed: %s", del_err)

                        logger.info("_scheduler_run_sync_jobs CREATE: job_id=%s device=%s name=%s",
                                   job_id, target_device_id, code_name)
                        result = session.create_access_code(
                            device_id=target_device_id, name=code_name, code=code_value,
                            is_always_valid=is_always, start_datetime=start_dt, end_datetime=end_dt)
                        target_code_record = result.get("code_id")
                        logger.info("_scheduler_run_sync_jobs CREATE SUCCESS: job_id=%s code_id=%s",
                                   job_id, target_code_record)

                        existing_child = conn.execute(
                            """SELECT id FROM access_codes
                               WHERE schlage_lock_id = ? AND name = ?""",
                            (target_device_id, code_name),
                        ).fetchone()
                        if existing_child:
                            conn.execute(
                                """UPDATE access_codes SET code_value=?, is_always_valid=?,
                                   start_datetime=?, end_datetime=?, schlage_code_id=?,
                                   synced_from_code_id=?, synced_from_lock_id=?, is_synced=1
                                   WHERE id = ?""",
                                (code_value, int(is_always), start_dt, end_dt,
                                 target_code_record, access_code_id, source_lock_id,
                                 existing_child["id"]))
                        else:
                            conn.execute(
                                """INSERT INTO access_codes
                                   (name, code_value, group_id, is_always_valid,
                                    start_datetime, end_datetime, schlage_lock_id, schlage_code_id,
                                    is_synced, sync_opt_out, synced_from_code_id, synced_from_lock_id)
                                   VALUES (?,?,?,?,?,?,?,?,1,0,?,?)""",
                                (code_name, code_value, group_id, int(is_always),
                                 start_dt, end_dt, target_device_id, target_code_record,
                                 access_code_id, source_lock_id))
                        sync_success = True

                    elif action == "delete":
                        # Resolve lock UUID from group_locks integer id before querying access_codes
                        gl_row = conn.execute(
                            "SELECT lock_id FROM group_locks WHERE lock_id = ?",
                            (target_device_id,),
                        ).fetchone()
                        if not gl_row:
                            raise Exception(f"group_locks id {target_device_id} not found")
                        resolved_lock_id = gl_row["lock_id"]
                        existing = conn.execute(
                            """SELECT id, schlage_code_id FROM access_codes
                               WHERE schlage_lock_id = ? AND name = ?""",
                            (resolved_lock_id, code_name),
                        ).fetchone()
                        logger.info("_scheduler_run_sync_jobs DELETE: job_id=%s device=%s code=%s name=%s",
                                   job_id, resolved_lock_id,
                                   existing["schlage_code_id"] if existing else None, code_name)
                        if existing and existing["schlage_code_id"]:
                            session.delete_access_code(
                                device_id=resolved_lock_id,
                                code_id=existing["schlage_code_id"],
                            )
                            conn.execute("DELETE FROM access_codes WHERE id = ?",
                                        (existing["id"],))
                        sync_success = True

                except Exception as e:
                    error_msg = str(e)
                    logger.error("_scheduler_run_sync_jobs: job %s failed: %s", job_id, e)

                if sync_success:
                    conn.execute(
                        """UPDATE sync_jobs
                           SET state='completed', completed_at=CURRENT_TIMESTAMP,
                               updated_at=CURRENT_TIMESTAMP WHERE id=?""", (job_id,))
                    results["executed"] += 1
                    if action == "create":
                        results["created"] += 1
                        conn.execute(
                            "UPDATE access_codes SET is_synced=1 WHERE id=?", (access_code_id,))
                    elif action == "update":
                        results["updated"] += 1
                        conn.execute(
                            "UPDATE access_codes SET is_synced=1 WHERE id=?", (access_code_id,))
                    elif action == "delete":
                        results["deleted"] += 1
                else:
                    conn.execute(
                        """UPDATE sync_jobs
                           SET state='failed', error_message=?, updated_at=CURRENT_TIMESTAMP
                           WHERE id=?""", (error_msg, job_id))
                    results["failed"] += 1
                    results["errors"].append(f"[job {job_id}] {error_msg}")

                # Write sync_code_history
                h_status = "success" if sync_success else "failed"
                try:
                    conn.execute(
                        """INSERT INTO sync_code_history
                           (group_id, source_lock_id, source_code_id, source_code_name,
                            source_code_value, target_lock_id, target_code_id, action, status,
                            skip_reason, source_is_always_valid, source_start_datetime,
                            source_end_datetime, sync_job_id)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (group_id, source_lock_id, access_code_id, code_name,
                         code_value, target_device_id, target_code_record, action, h_status,
                         error_msg, int(is_always), start_dt, end_dt, job_id))
                except Exception as e:
                    logger.warning("_scheduler_run_sync_jobs history INSERT failed job %s: %s",
                                 job_id, e)
                conn.commit()

        finally:
            conn.close()

        logger.info("_scheduler_run_sync_jobs: group %s done — %s", group_id, results)
        return {"message": "Sync complete", **results}

    finally:
        if lock_acquired:
            try:
                os.remove(lock_path)
                logger.info("_scheduler_run_sync_jobs: lock released")
            except Exception as e:
                logger.warning("_scheduler_run_sync_jobs: lock release failed: %s", e)

from fastapi import Form
from backend.sync_logic import SyncEngine

@router.post("/admin/test-sync")
async def test_sync(request: Request, group_id: int = Form(...)):
    """Trigger discover + force sync for a group (bypasses scheduler)."""
    import os
    from backend.sync_logic import SyncEngine
    from backend.database import DB_PATH
    import pyschlage
    from backend.auth import get_session_by_token, decrypt_password

    logger.info("test_sync: group=%s", group_id)

    # Get session and credentials
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT us.session_token, us.username
               FROM user_sessions us JOIN credentials c ON c.username = us.username
               WHERE c.is_owner = 1 ORDER BY us.last_active_at DESC LIMIT 1"""
        ).fetchone()
        if not row:
            return {"status": "error", "message": "No active session"}
        session_token = row["session_token"]
        username = row["username"]
        enc_row = conn.execute(
            "SELECT encrypted_password, nonce FROM credentials WHERE username = ?",
            (username,)
        ).fetchone()
    finally:
        conn.close()

    encrypted_creds = {
        "_encrypted_password": enc_row["encrypted_password"],
        "_nonce": enc_row["nonce"],
    }
    decrypted_password = decrypt_password(enc_row["encrypted_password"], enc_row["nonce"])
    creds = {"username": username, "password": decrypted_password}
    engine = SyncEngine(str(DB_PATH), pyschlage, creds, encrypted_creds=encrypted_creds)

    try:
        engine.discover_codes(group_ids=[group_id])
    except Exception as e:
        logger.error("test_sync discover_codes failed: %s", e, exc_info=True)
        return {"status": "error", "message": f"discover_codes failed: {e}"}

    logger.info("test_sync: discover_codes done, running sync_jobs")
    result = _scheduler_run_sync_jobs(group_id)
    return {"status": "ok", **result}

