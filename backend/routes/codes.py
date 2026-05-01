"""Access code routes."""

import logging
import uuid
import sqlite3
from fastapi import APIRouter, HTTPException, Query, Request, status

from ..models import (
    CodesResponse, CodeItem,
    CreateCodeRequest, CreateCodeResponse, CreatedCodeItem,
    OverwriteCodeRequest,
    DeleteCodesRequest, DeleteCodesResponse,
)
from ..database import db_cursor
from ..auth import get_current_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/codes", tags=["codes"])


def _discover_and_seed_jobs(session, group_id: int | None) -> None:
    """
    Pull live codes from Schlage for master locks and seed sync_jobs.

    For each group with a master lock:
    - NEW master code  → INSERT into access_codes + 'create' job per slave
    - CHANGED master code (same name, diff value/schedule) → 'delete'+'create' per slave
    - REMOVED from master (was in local DB, gone from cloud) → 'delete' per slave that has it

    This is called on every /codes refresh so jobs are always up to date.
    """
    def get_master_lock(gid: int):
        with db_cursor() as cur:
            cur.execute(
                "SELECT lock_id, lock_name FROM group_locks WHERE group_id = ? AND is_master = 1",
                (gid,),
            )
            row = cur.fetchone()
            return (row["lock_id"], row["lock_name"]) if row else (None, None)

    def get_slave_locks(gid: int, master_lock_id: str):
        with db_cursor() as cur:
            cur.execute(
                "SELECT id, lock_id FROM group_locks WHERE group_id = ? AND lock_id != ?",
                (gid, master_lock_id),
            )
            return [(r["id"], r["lock_id"]) for r in cur.fetchall()]

    # Get groups to process
    if group_id is not None:
        groups = [(group_id,)]
    else:
        with db_cursor() as cur:
            cur.execute("SELECT id FROM `groups`")
            groups = cur.fetchall()

    for (gid,) in groups:
        master_lock_id, master_lock_name = get_master_lock(gid)
        if not master_lock_id:
            continue

        # Pull live codes from Schlage for the master lock
        try:
            live_codes = session.get_access_codes(master_lock_id)
        except Exception as e:
            logger.warning("_discover_and_seed_jobs: failed to get codes from %s: %s", master_lock_id, e)
            continue

        # Build live_master dict from Schlage data (ground truth for parent lock)
        live_master = {c["name"]: c for c in live_codes if c.get("name")}
        live_names = set(live_master.keys())
        logger.info("DEBUG: Schlage returned for master %s: names=%r", master_lock_id, live_names)

        with db_cursor() as conn:
            conn.execute("BEGIN IMMEDIATE")

            def _upsert_job(conn, gid, access_code_id, target_lock_id, action, job_group, sequence, code_name):
                """Replace any completed/failed job with a fresh pending one. No silent failures."""
                existing = conn.execute(
                    "SELECT id, state FROM sync_jobs WHERE group_id=? AND access_code_id=? AND target_lock_id=? AND action=?",
                    (gid, access_code_id, target_lock_id, action),
                ).fetchone()
                if existing:
                    if existing['state'] in ('completed', 'failed', 'skipped'):
                        conn.execute("DELETE FROM sync_jobs WHERE id=?", (existing['id'],))
                        logger.info("_upsert_job: replaced %s job id=%s", action, existing['id'])
                    # else: pending/running — leave it alone
                logger.info("_upsert_job INSERT: gid=%s code=%s target=%s action=%s name=%s", gid, access_code_id, target_lock_id, action, code_name)
                try:
                    conn.execute(
                        """INSERT INTO sync_jobs
                           (group_id, access_code_id, target_lock_id, action, state, job_group, sequence, code_name)
                           VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
                        (gid, access_code_id, target_lock_id, action, job_group, sequence, code_name),
                    )
                except Exception as e:
                    logger.warning("_upsert_job INSERT failed: %s", e)

            # ── Get local master codes (from local DB) ────────────────────────
            cur = conn.execute(
                "SELECT id, name, code_value, is_always_valid, start_datetime, end_datetime, schlage_code_id "
                "FROM access_codes WHERE schlage_lock_id = ?",
                (master_lock_id,)
            )
            local_master = {row["name"]: dict(row) for row in cur}
            logger.info("DEBUG: local DB master records for %s: names=%r", master_lock_id, list(local_master.keys()))

            # ── Get slave locks ──────────────────────────────────────────────
            slave_locks = get_slave_locks(gid, master_lock_id)
            if not slave_locks:
                conn.execute("ROLLBACK")
                continue

            # ── Codes CHANGED on parent — update master record + queue jobs ─────────
            for cname, live_code in live_master.items():
                if cname in local_master:
                    master_rec = local_master[cname]
                    val_changed = master_rec["code_value"] != live_code["code"]
                    sched_changed = (
                        bool(master_rec["is_always_valid"]) != live_code["is_always_valid"]
                        or master_rec["start_datetime"] != live_code["start_datetime"]
                        or master_rec["end_datetime"] != live_code["end_datetime"]
                    )
                    logger.info("DEBUG: parent '%s': db_val=%r vs api_val=%r → val_changed=%s sched_changed=%s",
                                cname, master_rec["code_value"], live_code["code"], val_changed, sched_changed)
                    if val_changed or sched_changed:
                        logger.info("VALUE DRIFT: '%s' id=%s — updating master record", cname, master_rec["id"])
                        conn.execute(
                            """UPDATE access_codes SET code_value=?, is_always_valid=?,
                               start_datetime=?, end_datetime=?, schlage_code_id=?
                               WHERE schlage_lock_id=? AND name=?""",
                            (live_code["code"], live_code["is_always_valid"],
                             live_code["start_datetime"], live_code["end_datetime"],
                             live_code["access_code_id"], master_lock_id, cname),
                        )
                        # Refresh local_master in-place for subsequent slave comparison
                        local_master[cname]["code_value"] = live_code["code"]
                        local_master[cname]["is_always_valid"] = live_code["is_always_valid"]
                        local_master[cname]["start_datetime"] = live_code["start_datetime"]
                        local_master[cname]["end_datetime"] = live_code["end_datetime"]
                        local_master[cname]["schlage_code_id"] = live_code["access_code_id"]
                else:
                    # New master code — INSERT into local DB first, then queue CREATE on children
                    logger.info("DEBUG NEW MASTER: '%s' NOT in local DB — inserting into access_codes", cname)
                    try:
                        sc_id = live_code.get("access_code_id") or live_code.get("id")
                        cur.execute("""
                            INSERT INTO access_codes (name, code_value, group_id, is_always_valid,
                                start_datetime, end_datetime, schlage_lock_id, schlage_code_id, is_synced)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """, (
                            cname,
                            live_code["code"],
                            gid,
                            1 if live_code["is_always_valid"] else 0,
                            live_code.get("start_datetime"),
                            live_code.get("end_datetime"),
                            master_lock_id,
                            sc_id,
                        ))
                        new_master_id = cur.lastrowid
                        logger.info("DEBUG NEW MASTER: inserted id=%s for '%s'", new_master_id, cname)
                        # Add to local_master so slave comparison sees it
                        local_master[cname] = {
                            "id": new_master_id,
                            "code_value": live_code["code"],
                            "is_always_valid": live_code["is_always_valid"],
                            "start_datetime": live_code.get("start_datetime"),
                            "end_datetime": live_code.get("end_datetime"),
                            "schlage_code_id": sc_id,
                        }
                        # Queue CREATE jobs for all slave locks
                        for child_db_id, child_lock_id in slave_locks:
                            job_group = str(uuid.uuid4())
                            logger.info("DEBUG NEW MASTER: '%s' not on child %s — queueing CREATE (master_id=%s child_db_id=%s)",
                                        cname, child_lock_id, new_master_id, child_db_id)
                            _upsert_job(conn, gid, new_master_id, child_db_id, 'create', job_group, 1, cname)
                    except Exception as exc:
                        logger.error("DEBUG NEW MASTER: FAILED to insert '%s': %s", cname, exc)

            # ── Codes DELETED from parent — remove from local DB + queue delete jobs ──
            for cname in list(local_master.keys()):
                if cname not in live_names:
                    master_rec = local_master[cname]
                    master_local_id = master_rec["id"]
                    logger.info("DELETED FROM PARENT: '%s' id=%s — queuing delete on children", cname, master_local_id)
                    # Queue DELETE jobs BEFORE deleting parent code.
                    # Parent must exist in access_codes when jobs are created so
                    # list_sync_jobs JOIN can find and display the jobs.
                    for child_db_id, child_lock_id in slave_locks:
                        job_group = str(uuid.uuid4())
                        _upsert_job(conn, gid, master_local_id, child_db_id, 'delete', job_group, 0, cname)
                    # Delete parent from local DB (keep in local_master so DELETED FROM SLAVE finds it)
                    conn.execute(
                        "DELETE FROM access_codes WHERE schlage_lock_id = ? AND name = ?",
                        (master_lock_id, cname),
                    )
                    # NOTE: do NOT del local_master[cname] - DELETED FROM SLAVE needs it
            for slave_db_id, slave_lock_id in slave_locks:
                cur = conn.execute(
                    "SELECT id, name, code_value, is_always_valid, start_datetime, end_datetime, synced_from_lock_id "
                    "FROM access_codes WHERE schlage_lock_id = ?",
                    (slave_lock_id,)
                )
                slave_by_name = {row["name"]: dict(row) for row in cur}
                logger.info("DEBUG: child lock %s DB records: names=%r", slave_lock_id, list(slave_by_name.keys()))

                for cname, master_code in local_master.items():
                    master_local_id = master_code["id"]
                    schlage_code_id = master_code.get("schlage_code_id", "")

                    if cname in slave_by_name:
                        # Code exists on both master and child — check for value drift
                        slave_code = slave_by_name[cname]
                        val_changed = slave_code["code_value"] != master_code["code_value"]
                        sched_changed = (
                            slave_code["is_always_valid"] != master_code["is_always_valid"]
                            or slave_code["start_datetime"] != master_code["start_datetime"]
                            or slave_code["end_datetime"] != master_code["end_datetime"]
                        )
                        logger.info("DEBUG CHILD: '%s' on child %s: child_val=%r vs master_val=%r → val_changed=%s",
                                    cname, slave_lock_id, slave_code["code_value"], master_code["code_value"], val_changed)
                        if val_changed or sched_changed:
                            paired_group = str(uuid.uuid4())
                            logger.info("CHILD VALUE DRIFT: '%s' on child %s — queueing DELETE+CREATE (group=%s)",
                                        cname, slave_lock_id, paired_group)
                            # DELETE: use child's code ID so it finds and deletes the stale child copy
                            # CREATE: use master's code ID so it reads parent's updated code_value
                            child_code_id = slave_code["id"]
                            # Upsert DELETE job — replace completed/failed jobs with fresh ones
                            _upsert_job(conn, gid, child_code_id, slave_db_id, 'delete', paired_group, 0, cname)
                            # Upsert CREATE job — replace completed/failed jobs with fresh ones (updated code_value)
                            _upsert_job(conn, gid, master_local_id, slave_db_id, 'create', paired_group, 1, cname)
                    else:
                        # NEW master code not on this child → queue CREATE job
                        logger.info("DEBUG NEW: '%s' not on child %s — queueing CREATE", cname, slave_lock_id)
                        new_group = str(uuid.uuid4())
                        _upsert_job(conn, gid, master_local_id, slave_db_id, 'create', new_group, 1, cname)

                # DELETED from parent but still on child — queue DELETE job
                for sname, slave_code in slave_by_name.items():
                    if sname not in live_names and sname in local_master and slave_code.get("synced_from_lock_id") != master_lock_id:
                        master_local_id = local_master[sname]["id"]
                        logger.info("DELETED FROM SLAVE: '%s' on child %s — queueing DELETE", sname, slave_lock_id)
                        conn.execute(
                            "DELETE FROM access_codes WHERE schlage_lock_id = ? AND name = ?",
                            (slave_lock_id, sname),
                        )
                        del_group = str(uuid.uuid4())
                        _upsert_job(conn, gid, master_local_id, slave_db_id, 'delete', del_group, 0, sname)

            conn.execute("COMMIT")



def _require_auth(request: Request):
    session = get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


def _lock_name_from_db(lock_id: str) -> str | None:
    with db_cursor() as cur:
        cur.execute(
            "SELECT lock_name FROM group_locks WHERE lock_id = ? LIMIT 1",
            (lock_id,),
        )
        row = cur.fetchone()
        return row["lock_name"] if row else None


def _group_name_from_db(group_id: int) -> str | None:
    with db_cursor() as cur:
        cur.execute("SELECT name FROM `groups` WHERE id = ?", (group_id,))
        row = cur.fetchone()
        return row["name"] if row else None


def _fetch_all_codes(
    cur: sqlite3.Cursor, group_id: int | None = None
) -> list[CodeItem]:
    """Fetch code records from DB, optionally filtered by group_id."""
    query = """
        SELECT ac.id, ac.name, ac.code_value, ac.group_id,
               ac.is_always_valid, ac.start_datetime, ac.end_datetime,
               ac.schlage_lock_id, ac.schlage_code_id, ac.created_at,
               g.name AS group_name,
               gl.lock_name
        FROM access_codes ac
        LEFT JOIN `groups` g ON g.id = ac.group_id
        LEFT JOIN group_locks gl ON gl.lock_id = ac.schlage_lock_id
    """
    params = []
    if group_id is not None:
        query += " WHERE ac.group_id = ?"
        params.append(group_id)
    query += " ORDER BY ac.created_at DESC"

    cur.execute(query, params)
    codes = []
    for row in cur:
        codes.append(CodeItem(
            id=row["id"],
            name=row["name"],
            code_value=row["code_value"],
            group_id=row["group_id"],
            group_name=row["group_name"],
            is_always_valid=bool(row["is_always_valid"]),
            start_datetime=row["start_datetime"],
            end_datetime=row["end_datetime"],
            schlage_lock_id=row["schlage_lock_id"],
            lock_name=row["lock_name"],
            schlage_code_id=row["schlage_code_id"],
            created_at=row["created_at"],
        ))
    return codes


@router.get("", response_model=CodesResponse)
def list_codes(request: Request, group_id: int | None = Query(default=None)) -> CodesResponse:
    """
    List all access codes, optionally filtered by group_id.

    Merges codes from the local DB with live Schlage API codes for each lock.
    Schlage API codes that aren't in the local DB are shown with the lock name
    and '---' as placeholder code value (API doesn't expose the actual code).
    """
    _require_auth(request)
    session = get_current_session(request)
    session.login()

    # Get all lock IDs (from groups if group_id specified, otherwise all locks)
    all_locks = session.get_locks()
    if group_id is not None:
        with db_cursor() as cur:
            cur.execute(
                "SELECT lock_id, lock_name FROM group_locks WHERE group_id = ?",
                (group_id,),
            )
            group_lock_ids = {row["lock_id"] for row in cur}
        locks_to_query = [l for l in all_locks if l["device_id"] in group_lock_ids]
    else:
        locks_to_query = all_locks

    # ── Discover pass 1: runs BEFORE merge — new codes not yet in DB ──
    try:
        _discover_and_seed_jobs(session, group_id)
    except Exception as e:
        logger.warning("discover/seed pass 1 failed: %s", e)

    # Fetch live Schlage codes for each lock
    schlage_codes: dict[tuple, dict] = {}
    for lock_info in locks_to_query:
        device_id = lock_info["device_id"]
        lock_name = lock_info["name"]
        try:
            api_codes = session.get_access_codes(device_id)
            for code in api_codes:
                key = (device_id, code["name"])
                schlage_codes[key] = {
                    "name": code["name"],
                    "code_value": code["code"],
                    "schlage_lock_id": device_id,
                    "lock_name": lock_name,
                    "is_always_valid": code["is_always_valid"],
                    "start_datetime": code.get("start_datetime"),
                    "end_datetime": code.get("end_datetime"),
                    "schlage_code_id": code["access_code_id"] if code.get("access_code_id") else getattr(code, "id", None),
                    "source": "schlage",
                }
        except Exception as exc:
            logger.warning("Failed to fetch codes from lock %s: %s", device_id, exc)

    # Fetch local DB codes
    with db_cursor() as cur:
        local_codes = _fetch_all_codes(cur, group_id)

    # Merge: local codes override schlage codes (local has code_value, more complete)
    seen: dict[tuple, CodeItem] = {}
    for code in local_codes:
        key = (code.schlage_lock_id, code.name)
        seen[key] = CodeItem(
            id=code.id,
            name=code.name,
            code_value=code.code_value,
            group_id=code.group_id,
            group_name=code.group_name,
            is_always_valid=code.is_always_valid,
            start_datetime=code.start_datetime,
            end_datetime=code.end_datetime,
            schlage_lock_id=code.schlage_lock_id,
            lock_name=code.lock_name,
            schlage_code_id=code.schlage_code_id,
            created_at=code.created_at,
        )

    # Add/update Schlage codes — merge live Schlage values into local DB
    for key, sc in schlage_codes.items():
        sc_id = sc.get("schlage_code_id")
        if key in seen:
            # Local record already exists for this (lock, name) — check for value/code drift
            existing = seen[key]
            if existing.code_value != sc["code_value"] or existing.schlage_code_id != sc_id:
                # Schlage has updated value or new schlage_code_id — update local DB and seen
                try:
                    with db_cursor() as cur:
                        cur.execute(
                            """UPDATE access_codes SET code_value=?, is_always_valid=?,
                               start_datetime=?, end_datetime=?, schlage_code_id=?
                               WHERE schlage_lock_id=? AND name=?""",
                            (sc["code_value"],
                             1 if sc["is_always_valid"] else 0,
                             sc["start_datetime"],
                             sc["end_datetime"],
                             sc_id,
                             sc["schlage_lock_id"],
                             sc["name"]),
                        )
                except Exception as exc:
                    logger.warning("Merge: failed to update code %s: %s", sc_id, exc)
                logger.info("Merge: updated '%s' code_value=%s (was %s)",
                             sc["name"], sc["code_value"], existing.code_value)
                seen[key] = CodeItem(
                    id=existing.id,
                    name=sc["name"],
                    code_value=sc["code_value"],
                    group_id=existing.group_id,
                    group_name=existing.group_name,
                    is_always_valid=sc["is_always_valid"],
                    start_datetime=sc["start_datetime"],
                    end_datetime=sc["end_datetime"],
                    schlage_lock_id=sc["schlage_lock_id"],
                    lock_name=sc["lock_name"],
                    schlage_code_id=sc_id,
                    created_at=existing.created_at,
                )
            # If values match, keep local record as-is (seen already has it)
        else:
            # Key not in seen — genuinely new code from Schlage
            real_id = -1
            if sc_id:
                try:
                    with db_cursor() as cur:
                        cur.execute("SELECT id FROM access_codes WHERE schlage_code_id = ?", (sc_id,))
                        row = cur.fetchone()
                        if row:
                            real_id = row["id"]
                except Exception:
                    pass

            # Fallback: schlage_code_id may have changed (Schlage re-issued ID on value change)
            # Match by (schlage_lock_id, name) to find existing record and update the ID
            if real_id == -1:
                try:
                    with db_cursor() as cur:
                        cur.execute(
                            """SELECT id FROM access_codes
                               WHERE schlage_lock_id=? AND name=?""",
                            (sc["schlage_lock_id"], sc["name"]),
                        )
                        row = cur.fetchone()
                        if row:
                            real_id = row["id"]
                            # Update schlage_code_id to the new ID
                            try:
                                with db_cursor() as cur:
                                    cur.execute(
                                        "UPDATE access_codes SET schlage_code_id=? WHERE id=?",
                                        (sc_id, real_id),
                                    )
                            except Exception as exc:
                                logger.warning("Merge: failed to update schlage_code_id for %s: %s", sc_id, exc)
                except Exception:
                    pass

            if real_id == -1:
                try:
                    with db_cursor() as cur:
                        cur.execute('''
                            INSERT INTO access_codes (name, code_value, group_id, is_always_valid,
                                start_datetime, end_datetime, schlage_lock_id, schlage_code_id, is_synced)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                        ''', (
                            sc.get("name", ""),
                            sc.get("code_value", ""),
                            None,
                            1 if sc.get("is_always_valid") else 0,
                            sc.get("start_datetime"),
                            sc.get("end_datetime"),
                            sc.get("schlage_lock_id", ""),
                            sc_id,
                        ))
                        real_id = cur.lastrowid
                except Exception as exc:
                    logger.warning("Failed to insert Schlage-only code %s: %s", sc_id, exc)

            seen[key] = CodeItem(
                id=real_id,
                name=sc["name"],
                code_value=sc["code_value"],
                group_id=None,
                group_name=None,
                is_always_valid=sc["is_always_valid"],
                start_datetime=sc["start_datetime"],
                end_datetime=sc["end_datetime"],
                schlage_lock_id=sc["schlage_lock_id"],
                lock_name=sc["lock_name"],
                schlage_code_id=sc_id,
                created_at=None,
            )

    # ── Discover pass 2: runs AFTER merge — new codes now in DB, seed jobs ──
    try:
        _discover_and_seed_jobs(session, group_id)
    except Exception as e:
        logger.warning("discover/seed pass 2 failed: %s", e)

    return CodesResponse(codes=list(seen.values()))


@router.post("", response_model=CreateCodeResponse, status_code=status.HTTP_201_CREATED)
def create_codes(request: Request, body: CreateCodeRequest) -> CreateCodeResponse:
    """
    Create an access code for all locks in a group.

    Loops over each lock in the group, calls the Schlage API to create
    the access code on each lock, and stores local records.
    """
    import sqlite3
    session = _require_auth(request)

    with db_cursor() as cur:
        cur.execute(
            "SELECT lock_id, lock_name FROM group_locks WHERE group_id = ?",
            (body.group_id,),
        )
        group_locks = [(row["lock_id"], row["lock_name"]) for row in cur]
        if not group_locks:
            raise HTTPException(status_code=400, detail="Group has no locks")

    session.login()
    created = []
    import time
    with db_cursor() as cur:
        for lock_id, lock_name in group_locks:
            schlage_code_id = None
            last_exc = None
            for attempt in range(3):
                try:
                    result = session.create_access_code(
                        lock_id,
                        name=body.name,
                        code=body.code_value,
                        is_always_valid=body.is_always_valid,
                        start_datetime=body.start_datetime,
                        end_datetime=body.end_datetime,
                    )
                    schlage_code_id = result.get("code_id")
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt < 2 and ("Bad Gateway" in str(exc) or "502" in str(exc)):
                        time.sleep(2)
                        continue
                    error_detail = str(exc)
                    if hasattr(exc, 'response'):
                        error_detail = f"Schlage API error {exc.response.status_code}: {exc.response.text}"
                    elif 'Bad Gateway' in error_detail:
                        error_detail = (
                            f"Schlage cloud returned 502 for lock {lock_id}. "
                            "The lock may be offline or the cloud API may be temporarily unavailable."
                        )
                    logger.error("Failed to create code on lock %s: %s", lock_id, exc)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Failed to create code on lock {lock_id}: {error_detail}",
                    )

            cur.execute(
                """
                INSERT INTO access_codes
                    (name, code_value, group_id, is_always_valid,
                     start_datetime, end_datetime, schlage_lock_id, schlage_code_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    body.name,
                    body.code_value,
                    body.group_id,
                    int(body.is_always_valid),
                    body.start_datetime,
                    body.end_datetime,
                    lock_id,
                    schlage_code_id,
                ),
            )
            local_id = cur.lastrowid
            created.append(CreatedCodeItem(
                local_id=local_id,
                schlage_lock_id=lock_id,
                schlage_code_id=schlage_code_id,
            ))

    return CreateCodeResponse(message="Codes created", codes=created)


@router.put("/{code_id}", response_model=CreateCodeResponse)
def overwrite_code(code_id: int, body: OverwriteCodeRequest) -> CreateCodeResponse:
    """
    Overwrite an access code.

    Finds all local records with the SAME name as the code being overwritten
    (across all locks), deletes them via Schlage API, then recreates new
    codes on all locks in the NEW target group.

    Name is the overwrite key, not group_id.
    """
    import sqlite3
    session = _require_auth(request)

    with db_cursor() as cur:
        cur.execute(
            "SELECT name FROM access_codes WHERE id = ?",
            (code_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Code not found")
        original_name = row["name"]

    with db_cursor() as cur:
        cur.execute(
            "SELECT id, schlage_lock_id, schlage_code_id FROM access_codes WHERE name = ?",
            (original_name,),
        )
        old_records = [(r["id"], r["schlage_lock_id"], r["schlage_code_id"]) for r in cur]

    session.login()

    deleted_lock_ids = set()
    for _, lock_id, schlage_code_id in old_records:
        if schlage_code_id:
            try:
                session.delete_access_code(lock_id, schlage_code_id)
                deleted_lock_ids.add(lock_id)
            except Exception as exc:
                logger.warning(
                    "Failed to delete code %s from lock %s: %s",
                    schlage_code_id, lock_id, exc,
                )

    with db_cursor() as cur:
        placeholders = ",".join("?" * len(old_records))
        cur.execute(
            f"DELETE FROM access_codes WHERE id IN ({placeholders})",
            [r[0] for r in old_records],
        )

    with db_cursor() as cur:
        cur.execute(
            "SELECT lock_id, lock_name FROM group_locks WHERE group_id = ?",
            (body.group_id,),
        )
        group_locks = [(row["lock_id"], row["lock_name"]) for row in cur]
        if not group_locks:
            raise HTTPException(status_code=400, detail="Target group has no locks")

    created = []
    with db_cursor() as cur:
        for lock_id, lock_name in group_locks:
            try:
                result = session.create_access_code(
                    lock_id,
                    name=body.name,
                    code=body.code_value,
                    is_always_valid=body.is_always_valid,
                    start_datetime=body.start_datetime,
                    end_datetime=body.end_datetime,
                )
                schlage_code_id = result.get("code_id")
            except Exception as exc:
                logger.error(
                    "Failed to create code on lock %s during overwrite: %s",
                    lock_id, exc,
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to create code on lock {lock_id}: {exc}",
                )

            cur.execute(
                """
                INSERT INTO access_codes
                    (name, code_value, group_id, is_always_valid,
                     start_datetime, end_datetime, schlage_lock_id, schlage_code_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    body.name,
                    body.code_value,
                    body.group_id,
                    int(body.is_always_valid),
                    body.start_datetime,
                    body.end_datetime,
                    lock_id,
                    schlage_code_id,
                ),
            )
            local_id = cur.lastrowid
            created.append(CreatedCodeItem(
                local_id=local_id,
                schlage_lock_id=lock_id,
                schlage_code_id=schlage_code_id,
            ))

    return CreateCodeResponse(message="Codes overwritten", codes=created)


@router.delete("/{code_id}")
def delete_code(request: Request, code_id: int) -> dict:
    """Delete a single access code (local record + Schlage API)."""
    session = _require_auth(request)

    with db_cursor() as cur:
        cur.execute(
            "SELECT schlage_lock_id, schlage_code_id FROM access_codes WHERE id = ?",
            (code_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Code not found")
        lock_id = row["schlage_lock_id"]
        schlage_code_id = row["schlage_code_id"]

    try:
        session.login()
    except Exception as exc:
        raise HTTPException(
            status_code=401,
            detail="Schlage session expired. Please log out and log back in.",
        ) from exc

    try:
        if schlage_code_id:
            session.delete_access_code(lock_id, schlage_code_id)
    except (AttributeError, RuntimeError) as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc) or "Failed to reach Schlage API. Please try again.",
        ) from exc

    with db_cursor() as cur:
        cur.execute("DELETE FROM access_codes WHERE id = ?", (code_id,))

    return {"message": "Code deleted"}


@router.post("/delete-batch", response_model=DeleteCodesResponse)
def delete_codes_batch(request: Request, body: DeleteCodesRequest) -> DeleteCodesResponse:
    """Bulk delete access codes by ID."""
    session = _require_auth(request)
    deleted = 0

    for code_id in body.ids:
        with db_cursor() as cur:
            cur.execute(
                "SELECT schlage_lock_id, schlage_code_id FROM access_codes WHERE id = ?",
                (code_id,),
            )
            row = cur.fetchone()
        if not row:
            continue

        lock_id = row["schlage_lock_id"]
        schlage_code_id = row["schlage_code_id"]

        try:
            session.login()
        except Exception as exc:
            logger.warning("Batch delete auth failed for code %s: %s", schlage_code_id, exc)
            raise HTTPException(
                status_code=401,
                detail="Schlage session expired. Please log out and log back in.",
            ) from exc

        try:
            if schlage_code_id:
                session.delete_access_code(lock_id, schlage_code_id)
        except (AttributeError, RuntimeError) as exc:
            logger.warning("Batch delete for code %s on lock %s: %s", schlage_code_id, lock_id, exc)

        with db_cursor() as cur:
            cur.execute("DELETE FROM access_codes WHERE id = ?", (code_id,))
            if cur.rowcount:
                deleted += 1

    return DeleteCodesResponse(message="Codes deleted", deleted=deleted)
