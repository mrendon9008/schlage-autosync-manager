"""
Sync logic for the Schlage lock code mirror system.

Terminology
──────────
• Master lock  — one lock per group managed by Guesty; all other locks in the
                group are "targets" that must mirror the master's codes.
• Target lock — any other lock in the same group; receives mirrored codes.
• Code identity — (code_name, code_value) is the stable key used to detect
                 additions, updates, and deletions even after a master code
                 record has been deleted from access_codes.
• Opt-out      — a flag on each target code (sync_opt_out=1) that prevents the
                 sync process from ever touching it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Data transfer objects ─────────────────────────────────────────────────────

@dataclass
class CodeSnapshot:
    """A point-in-time view of an access_code record."""
    id: int
    name: str
    code_value: str
    is_always_valid: bool
    start_datetime: Optional[str]
    end_datetime: Optional[str]
    schlage_lock_id: str
    schlage_code_id: Optional[str]
    is_synced: bool
    sync_opt_out: bool
    synced_from_code_id: Optional[int]
    synced_from_lock_id: Optional[str]


@dataclass
class SyncAction:
    """One action the sync engine plans or took."""
    action: str           # created | updated | deleted | skipped | conflict
    target_lock_id: str
    target_code_id: Optional[int] = None
    reason: Optional[str] = None
    dry_run: bool = True
    # Per-code detail for preview/sync display
    code_name: str = ''
    code_value: str = ''
    old_code_value: str = ''


@dataclass
class SyncResult:
    """Outcome of a single sync execution."""
    run_id: int
    dry_run: bool
    checked: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    # Per-code detail lists (populated even in dry_run)
    details_created: list[SyncAction] = field(default_factory=list)
    details_updated: list[SyncAction] = field(default_factory=list)
    details_deleted: list[SyncAction] = field(default_factory=list)
    details_skipped: list[SyncAction] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "dry_run": self.dry_run,
            "checked": self.checked,
            "created": self.created,
            "updated": self.updated,
            "deleted": self.deleted,
            "skipped": self.skipped,
            "errors": self.errors,
        }


# ─── Core sync logic ───────────────────────────────────────────────────────────

class SyncEngine:
    """
    Coordinates code sync between a master lock and all target locks in its group.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database.
    pyschlage_module : module
        The pyschlage library (lazy-loaded by caller).
    creds : dict
        {'username': ..., 'password': ...} for Schlage API auth.
    """

    def __init__(self, db_path: str, pyschlage_module, creds: dict, encrypted_creds=None):
        self.db_path = db_path
        self.pyschlage = pyschlage_module
        self.creds = creds.copy()
        if encrypted_creds:
            self.creds.update(encrypted_creds)
        # In-memory store for pending sync jobs (group_id -> list of job dicts)
        self._pending_jobs: dict[int, list[dict]] = {}

    # ── Database helpers ──────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_master_lock(self, group_id: int) -> Optional[str]:
        """Return the lock_id of the master lock for this group (is_master=1)."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT lock_id FROM group_locks "
                "WHERE group_id = ? AND is_master = 1 LIMIT 1",
                (group_id,),
            )
            row = cur.fetchone()
        return row["lock_id"] if row else None

    def _get_lock_ids_in_group(self, group_id: int) -> list[str]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT lock_id FROM group_locks WHERE group_id = ?",
                (group_id,),
            )
            return [r["lock_id"] for r in cur.fetchall()]

    def _get_codes_for_lock(self, lock_id: str) -> list[CodeSnapshot]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT id, name, code_value, is_always_valid, start_datetime, "
                "       end_datetime, schlage_lock_id, schlage_code_id, "
                "       is_synced, sync_opt_out, synced_from_code_id, "
                "       synced_from_lock_id "
                "FROM access_codes WHERE schlage_lock_id = ?",
                (lock_id,),
            )
            return [
                CodeSnapshot(
                    id=r["id"],
                    name=r["name"],
                    code_value=r["code_value"],
                    is_always_valid=bool(r["is_always_valid"]),
                    start_datetime=r["start_datetime"],
                    end_datetime=r["end_datetime"],
                    schlage_lock_id=r["schlage_lock_id"],
                    schlage_code_id=r["schlage_code_id"],
                    is_synced=bool(r["is_synced"]),
                    sync_opt_out=bool(r["sync_opt_out"]),
                    synced_from_code_id=r["synced_from_code_id"],
                    synced_from_lock_id=r["synced_from_lock_id"],
                )
                for r in cur.fetchall()
            ]

    # ── Discovery: pull from Schlage cloud, populate local DB ──────────────────

    def discover_codes(self, group_ids: list[int] | None = None) -> dict[int, list[dict]]:
        """
        Pull access codes from Schlage cloud for master locks and insert
        newly discovered codes into the local DB.

        For each group:
          1. Find master lock (is_master=1 in group_locks)
          2. Call Schlage API to get live access codes for that lock
          3. Compare against local access_codes — new = schlage_code_id in cloud
             but NOT already in local DB for that group
          4. Insert new codes into local DB with is_synced=0
          5. Store pending sync jobs in self._pending_jobs[group_id]

        Returns:
            {group_id: [{"code_name": ..., "code_value": ..., "lock_id": ...}, ...]}
        """
        from backend.auth import SchlageSession

        # Authenticate with Schlage
        try:
            session = SchlageSession(self.creds["username"])
            enc_pw = self.creds.get("_encrypted_password")
            nonce = self.creds.get("_nonce")
            if enc_pw is not None and nonce is not None:
                session._encrypted_password = enc_pw
                session._nonce = nonce
            session.login()
        except Exception as e:
            logger.error("discover_codes: auth failed: %s", e)
            return {}

        pending: dict[int, list[dict]] = {}

        # Get groups to check
        with self._conn() as conn:
            if group_ids:
                placeholders = ",".join("?" * len(group_ids))
                cur = conn.execute(
                    f"SELECT id FROM `groups` WHERE id IN ({placeholders})",
                    group_ids,
                )
            else:
                cur = conn.execute("SELECT id FROM `groups`")
            groups = [r["id"] for r in cur.fetchall()]

        for group_id in groups:
            master_lock_id = self._get_master_lock(group_id)
            if not master_lock_id:
                logger.warning("discover_codes: no master lock for group %s", group_id)
                continue
            logger.info("discover_codes: checking group %s master=%s", group_id, master_lock_id)
            try:
                live_codes = session.get_access_codes(master_lock_id)
            except Exception as e:
                logger.warning("discover_codes: failed to get codes from Schlage for %s: %s",
                               master_lock_id, e)
                continue

            # Build lookup maps for existing local codes on this master lock
            local_codes = self._get_codes_for_lock(master_lock_id)
            local_code_ids = {c.schlage_code_id for c in local_codes if c.schlage_code_id}
            local_names   = {c.name: c for c in local_codes if c.schlage_code_id}

            # ── Process each live Schlage code ──────────────────────────────────
            group_pending = []
            with self._conn() as conn:
                for code in live_codes:
                    schlage_code_id = code.get("access_code_id") or code.get("code_id")
                    code_value = code.get("code") or ""
                    name = code.get("name") or ""
                    is_always = code.get("is_always_valid", True)
                    start_dt = code.get("start_datetime")
                    end_dt = code.get("end_datetime")

                    if not schlage_code_id or not name:
                        continue

                    if schlage_code_id not in local_code_ids and name not in local_names:
                        print(f"[DISCOVER] NEW FROM SCHLAGE: name={name} code={schlage_code_id} val={code_value[:4]}...")
                        # ── Truly new code — insert + queue create jobs for children ──
                        try:
                            cur = conn.execute(
                                """INSERT INTO access_codes
                                    (name, code_value, group_id, is_always_valid,
                                     start_datetime, end_datetime, schlage_lock_id,
                                     schlage_code_id, is_synced, sync_opt_out)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)""",
                                (name, code_value, group_id, int(is_always),
                                 start_dt, end_dt, master_lock_id, schlage_code_id),
                            )
                            local_id = cur.lastrowid
                        except sqlite3.IntegrityError:
                            continue

                        group_pending.append({
                            "code_name": name,
                            "code_value": code_value,
                            "source_code_id": local_id,
                            "source_lock_id": master_lock_id,
                            "schlage_code_id": schlage_code_id,
                            "is_always_valid": is_always,
                            "start_datetime": start_dt,
                            "end_datetime": end_dt,
                        })

                        slave_cur = conn.execute(
                            "SELECT id, lock_id FROM group_locks "
                            "WHERE group_id = ? AND lock_id != ?",
                            (group_id, master_lock_id),
                        )
                        for slave_row in slave_cur.fetchall():
                            paired_group = str(uuid.uuid4())
                            try:
                                conn.execute(
                                    """INSERT OR IGNORE INTO sync_jobs
                                       (group_id, access_code_id, target_lock_id, action, state, job_group, sequence)
                                       VALUES (?, ?, ?, 'create', 'pending', ?, 1)""",
                                        (group_id, local_id, slave_row['id'], paired_group),
                                    )
                            except (sqlite3.IntegrityError, sqlite3.OperationalError):
                                pass
                            except (sqlite3.IntegrityError, sqlite3.OperationalError):
                                pass

                    elif name in local_names and (
                        local_names[name].code_value != code_value or
                        local_names[name].is_always_valid != is_always or
                        local_names[name].start_datetime != start_dt or
                        local_names[name].end_datetime != end_dt
                    ):
                        print(f"[DISCOVER] VALUE/SCHEDULE DRIFT DETECTED: name={name} code_val: {local_names[name].code_value}->{code_value} is_always: {local_names[name].is_always_valid}->{is_always} start: {local_names[name].start_datetime}->{start_dt} end: {local_names[name].end_datetime}->{end_dt}")
                        # ── Value or schedule drift: name exists locally but value or schedule differs.
                        #    Fires whether schlage_code_id is the same OR new (Schlage re-issues ID on value change).
                        #    Queue delete + create jobs for each child BEFORE updating the parent's DB record.
                        #    This ensures the drift condition is still true when we check it below.
                        old_code = local_names[name]
                        slave_cur = conn.execute(
                            "SELECT id, lock_id FROM group_locks "
                            "WHERE group_id = ? AND lock_id != ?",
                            (group_id, master_lock_id),
                        )
                        for slave_row in slave_cur.fetchall():
                            paired_group = str(uuid.uuid4())
                            try:
                                conn.execute(
                                    """INSERT OR IGNORE INTO sync_jobs
                                       (group_id, access_code_id, target_lock_id, action, state,
                                        job_group, sequence)
                                       VALUES (?, ?, ?, 'delete', 'pending', ?, 0)""",
                                    (group_id, old_code.id, slave_row["id"], paired_group),
                                )
                            except (sqlite3.IntegrityError, sqlite3.OperationalError):
                                pass
                            try:
                                conn.execute(
                                    """INSERT OR IGNORE INTO sync_jobs
                                       (group_id, access_code_id, target_lock_id, action, state,
                                        job_group, sequence)
                                       VALUES (?, ?, ?, 'create', 'pending', ?, 1)""",
                                    (group_id, old_code.id, slave_row["id"], paired_group),
                                )
                            except (sqlite3.IntegrityError, sqlite3.OperationalError):
                                pass

                        # Now update the local master record (local_names refreshed below)
                        try:
                            conn.execute(
                                """UPDATE access_codes
                                   SET code_value = ?, schlage_code_id = ?,
                                       is_always_valid = ?, start_datetime = ?, end_datetime = ?
                                   WHERE id = ?""",
                                (code_value, schlage_code_id, int(is_always), start_dt, end_dt, old_code.id),
                            )
                            logger.info(
                                "discover_codes: value drift on '%s' (id=%s): '%s' -> '%s'",
                                name, old_code.id, old_code.code_value, code_value,
                            )
                        except Exception as e:
                            logger.warning(
                                "discover_codes: failed to update drifted code %s: %s",
                                name, e,
                            )
                            continue

                # No separate commit needed — conn context manager handles it

                # ── Codes DELETED from parent — queue delete jobs for children ──────
                live_names = {code.get("name", "") for code in live_codes}
                for cname, master_code in local_names.items():
                    if cname not in live_names:
                        logger.info("discover_codes: %s deleted from parent — queueing delete on children", cname)
                        slave_cur = conn.execute(
                            "SELECT id, lock_id FROM group_locks "
                            "WHERE group_id = ? AND lock_id != ?",
                            (group_id, master_lock_id),
                        )
                        for slave_row in slave_cur.fetchall():
                            paired_group = str(uuid.uuid4())
                            try:
                                conn.execute(
                                    """INSERT OR IGNORE INTO sync_jobs
                                       (group_id, access_code_id, target_lock_id, action, state,
                                        job_group, sequence, code_name)
                                       VALUES (?, ?, ?, 'delete', 'pending', ?, 0, ?)""",
                                    (group_id, master_code.id, slave_row["id"], paired_group, cname),
                                )
                            except (sqlite3.IntegrityError, sqlite3.OperationalError):
                                pass
                            conn.execute(
                                "DELETE FROM access_codes WHERE schlage_lock_id = ? AND name = ?",
                                (slave_row["lock_id"], cname),
                            )

                # ── Delete orphan local codes (deleted from Schlage cloud) ─────────
                live_schlage_ids = {
                    (code.get("access_code_id") or code.get(code_id))
                    for code in live_codes
                }
                orphan_cur = conn.execute(
                    """SELECT id, name, schlage_code_id FROM access_codes
                       WHERE schlage_lock_id = ? AND schlage_code_id IS NOT NULL""",
                    (master_lock_id,),
                )
                for orphan in orphan_cur.fetchall():
                    if orphan["schlage_code_id"] not in live_schlage_ids:
                        conn.execute(
                            "DELETE FROM access_codes WHERE id = ?",
                            (orphan["id"],),
                        )
                        print(f"[DISCOVER] ORPHAN DELETED: name={orphan["name"]} schlage_code_id={orphan["schlage_code_id"]} from group {group_id}")
                        logger.info(
                            "discover_codes: deleted orphan code '%s' (schlage_code_id=%s) from group %s",
                            orphan["name"], orphan["schlage_code_id"], group_id,
                        )

                # ── After processing all codes, detect rename updates ──────────────
                self._detect_updates_for_group(conn, group_id, master_lock_id, live_codes)

                if group_pending:
                    pending[group_id] = group_pending
                    self._pending_jobs[group_id] = group_pending
                    print(f"[DISCOVER] Group {group_id}: queued {len(group_pending)} pending jobs")
                    logger.info("discover_codes: group %s discovered %d new codes",
                               group_id, len(group_pending))

        return pending

    def _detect_updates_for_group(self, conn: sqlite3.Connection, group_id: int,
                                   master_lock_id: str, live_codes: list[dict]) -> None:
        """
        Check each live master code against already-synced slave copies.
        If a slave code's name differs from the current master name (rename),
        INSERT a sync_jobs row with action='update', state='pending'.
        """
        # Build a map of schlage_code_id -> name from live codes
        live_map: dict[str, str] = {}
        for code in live_codes:
            cid = code.get("access_code_id") or code.get("code_id") or ""
            live_map[cid] = code.get("name", "")

        # Find slave locks in this group (not the master)
        slave_cur = conn.execute(
            "SELECT id, lock_id FROM group_locks "
            "WHERE group_id = ? AND lock_id != ?",
            (group_id, master_lock_id),
        )
        slave_locks = {r["lock_id"]: r["id"] for r in slave_cur.fetchall()}

        if not slave_locks:
            return

        lock_ids = list(slave_locks.keys())
        placeholders = ",".join("?" * len(lock_ids))

        # For each master code (by schlage_code_id), find slave codes synced from it
        for schlage_code_id, master_name in live_map.items():
            # Find the master access_code record for this schlage_code_id
            master_ac = conn.execute(
                "SELECT id, name FROM access_codes "
                "WHERE schlage_lock_id = ? AND (schlage_code_id = ? OR name = ?)",
                (master_lock_id, schlage_code_id, master_name),
            ).fetchone()
            if not master_ac:
                continue
            master_ac_id = master_ac["id"]

            # Find slave codes synced from this master (via synced_from_code_id)
            synced_cur = conn.execute(
                f"SELECT id, name, schlage_lock_id FROM access_codes "
                f"WHERE synced_from_code_id = ? AND synced_from_lock_id = ? "
                f"  AND schlage_lock_id IN ({placeholders})",
                [master_ac_id, master_lock_id] + lock_ids,
            )
            for synced_row in synced_cur.fetchall():
                slave_lock_local_id = slave_locks.get(synced_row["schlage_lock_id"])
                if not slave_lock_local_id:
                    continue
                if synced_row["name"] != master_name:
                    # Name differs — queue an update (rename) job
                    try:
                        conn.execute(
                            """INSERT OR IGNORE INTO sync_jobs
                               (group_id, access_code_id, target_lock_id, action, state)
                               VALUES (?, ?, ?, 'update', 'pending')""",
                            (group_id, master_ac_id, slave_lock_local_id),
                        )
                    except sqlite3.IntegrityError:
                        pass

    def get_pending_jobs(self, group_id: int) -> list[dict]:
        """Return pending sync jobs for a group (from last discover_codes call)."""
        return self._pending_jobs.get(group_id, [])


    def _code_identity(self, code: CodeSnapshot) -> str:
        """Stable hash key for (name, value)."""
        raw = f"{code.name}|{code.code_value}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    # ── Conflict / opt-out rules ─────────────────────────────────────────────

    def _classify_target_code(
        self,
        target_code: CodeSnapshot,
        source_code: CodeSnapshot,
    ) -> str:
        """
        Classify what to do when target has a code with the same name as source.

        Returns one of:
          'overwrite'    — safe to overwrite (was synced from same master)
          'conflict'     — user-created or from a different source; skip
          'uptodate'     — already matches; nothing to do
        """
        if target_code.code_value == source_code.code_value:
            return "uptodate"

        if target_code.synced_from_lock_id == source_code.schlage_lock_id:
            # Same master source — safe to update
            return "overwrite"

        return "conflict"

    # ── Single-lock sync ───────────────────────────────────────────────────────

    def _sync_target_lock(
        self,
        group_id: int,
        master_lock_id: str,
        target_lock_id: str,
        master_codes: dict[str, CodeSnapshot],   # name → CodeSnapshot
        target_codes: dict[str, CodeSnapshot],  # name → CodeSnapshot
        run_id: int,
        dry_run: bool,
        client,   # pyschlage lock client
        excluded_codes: set = None,
    ) -> SyncResult:
        """
        Sync master_codes onto one target lock.

        master_codes  — keyed by code name (unique per lock in Schlage)
        target_codes  — keyed by code name
        client        — pyschlage lock client for the target lock
        excluded_codes — set of "name:target_lock_id" strings to skip (transient opt-out)
        """
        result = SyncResult(run_id=run_id, dry_run=dry_run)
        excluded = excluded_codes or set()

        # ── Detect deletions ────────────────────────────────────────────────
        # Find target codes that WERE synced from this master but the master
        # code no longer exists (by name). Those need to be deleted from target.
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT DISTINCT source_code_name, target_code_id "
                "FROM sync_code_history "
                "WHERE target_lock_id = ? "
                "  AND source_lock_id = ? "
                "  AND action IN ('created','updated') "
                "  AND status = 'success'",
                (target_lock_id, master_lock_id),
            )
            previously_synced_names = {r["source_code_name"]: r["target_code_id"]
                                       for r in cur.fetchall()}

        for name, target_code_id in previously_synced_names.items():
            if name not in master_codes:
                # Master code is gone → delete from target
                result.checked += 1
                if f"{name}:{target_lock_id}" in excluded:
                    result.skipped += 1
                    if not dry_run:
                        with self._conn() as conn:
                            conn.execute(
                                "INSERT INTO sync_code_history "
                                "(sync_run_id, group_id, source_lock_id, "
                                " source_code_name, source_code_value, "
                                " target_lock_id, target_code_id, action, status, "
                                " source_is_always_valid, skip_reason) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, 'deleted', 'skipped', 1, 'opted out')",
                                (run_id, group_id, master_lock_id,
                                 name,
                                 target_codes[name].code_value if name in target_codes else '',
                                 target_lock_id, target_code_id),
                            )
                elif not dry_run:
                    try:
                        client.delete_code(schlage_code_id=target_code_id)
                        with self._conn() as conn:
                            conn.execute(
                                "INSERT INTO sync_code_history "
                                "(sync_run_id, group_id, source_lock_id, "
                                " source_code_name, source_code_value, "
                                " target_lock_id, target_code_id, action, status, "
                                " source_is_always_valid) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, 'deleted', 'success', 1)",
                                (run_id, group_id, master_lock_id,
                                 name, target_codes[name].code_value if name in target_codes else '',
                                 target_lock_id, target_code_id),
                            )
                        result.deleted += 1
                    except Exception as e:
                        result.errors.append(f"[{target_lock_id}] delete failed: {e}")
                        result.skipped += 1
                else:
                    result.skipped += 1

        # ── Sync additions / updates ────────────────────────────────────────
        for name, master_code in master_codes.items():
            result.checked += 1

            if name in target_codes:
                target_code = target_codes[name]
                classification = self._classify_target_code(target_code, master_code)

                if classification == "uptodate":
                    # Record that we're in sync (idempotent)
                    if not dry_run:
                        with self._conn() as conn:
                            conn.execute(
                                "INSERT OR IGNORE INTO sync_code_history "
                                "(sync_run_id, group_id, source_lock_id, source_code_id, "
                                " source_code_name, source_code_value, target_lock_id, "
                                " target_code_id, action, status, "
                                " source_is_always_valid, source_start_datetime, "
                                " source_end_datetime) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'synced', 'success', "
                                "        ?, ?, ?)",
                                (run_id, group_id, master_lock_id, master_code.id,
                                 name, master_code.code_value, target_lock_id,
                                 target_code.id, master_code.is_always_valid,
                                 master_code.start_datetime, master_code.end_datetime),
                            )
                    continue

                elif classification == "overwrite":
                    if f"{name}:{target_lock_id}" in excluded:
                        result.skipped += 1
                        if not dry_run:
                            with self._conn() as conn:
                                conn.execute(
                                    "INSERT INTO sync_code_history "
                                    "(sync_run_id, group_id, source_lock_id, source_code_id, "
                                    " source_code_name, source_code_value, target_lock_id, "
                                    " target_code_id, action, status, skip_reason, "
                                    " source_is_always_valid, source_start_datetime, "
                                    " source_end_datetime) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'updated', 'skipped', "
                                    "        'opted out', ?, ?, ?)",
                                    (run_id, group_id, master_lock_id, master_code.id,
                                     name, master_code.code_value, target_lock_id,
                                     target_code.id, master_code.is_always_valid,
                                     master_code.start_datetime, master_code.end_datetime),
                                )
                    elif not dry_run:
                        try:
                            client.update_code(
                                schlage_code_id=target_code.id,
                                code=master_code.code_value,
                                name=master_code.name,
                                always=master_code.is_always_valid,
                                start_datetime=master_code.start_datetime,
                                end_datetime=master_code.end_datetime,
                            )
                            with self._conn() as conn:
                                conn.execute(
                                    "INSERT INTO sync_code_history "
                                    "(sync_run_id, group_id, source_lock_id, source_code_id, "
                                    " source_code_name, source_code_value, target_lock_id, "
                                    " target_code_id, action, status, "
                                    " source_is_always_valid, source_start_datetime, "
                                    " source_end_datetime) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'updated', 'success', "
                                    "        ?, ?, ?)",
                                    (run_id, group_id, master_lock_id, master_code.id,
                                     name, master_code.code_value, target_lock_id,
                                     target_code.id, master_code.is_always_valid,
                                     master_code.start_datetime, master_code.end_datetime),
                                )
                            result.updated += 1
                        except Exception as e:
                            result.errors.append(f"[{target_lock_id}] update failed: {e}")
                            result.skipped += 1
                    continue
            else:
                action, status = "created", "success"

            # Skip if excluded (transient opt-out)
            if f"{name}:{target_lock_id}" in excluded:
                result.skipped += 1
                if not dry_run:
                    with self._conn() as conn:
                        conn.execute(
                            "INSERT INTO sync_code_history "
                            "(sync_run_id, group_id, source_lock_id, source_code_id, "
                            " source_code_name, source_code_value, target_lock_id, "
                            " target_code_id, action, status, skip_reason, "
                            " source_is_always_valid, source_start_datetime, "
                            " source_end_datetime) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'skipped', "
                            "        'opted out', ?, ?, ?)",
                            (run_id, group_id, master_lock_id, master_code.id,
                             name, master_code.code_value, target_lock_id,
                             None, action, master_code.is_always_valid,
                             master_code.start_datetime, master_code.end_datetime),
                        )
                continue

            if not dry_run:
                try:
                    created = client.add_code(
                        code=master_code.code_value,
                        name=master_code.name,
                        always=master_code.is_always_valid,
                        start_datetime=master_code.start_datetime,
                        end_datetime=master_code.end_datetime,
                    )
                    target_code_id = created.code_id

                    # Insert into access_codes (local record)
                    with self._conn() as conn:
                        cur = conn.execute(
                            "INSERT INTO access_codes "
                            "(name, code_value, group_id, is_always_valid, "
                            " start_datetime, end_datetime, schlage_lock_id, "
                            " schlage_code_id, is_synced, sync_opt_out, "
                            " synced_from_code_id, synced_from_lock_id) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)",
                            (master_code.name, master_code.code_value, group_id,
                             int(master_code.is_always_valid),
                             master_code.start_datetime, master_code.end_datetime,
                             target_lock_id, target_code_id,
                             master_code.id, master_lock_id),
                        )
                        local_id = cur.lastrowid

                    # Record in sync history
                    with self._conn() as conn:
                        conn.execute(
                            "INSERT INTO sync_code_history "
                            "(sync_run_id, group_id, source_lock_id, source_code_id, "
                            " source_code_name, source_code_value, target_lock_id, "
                            " target_code_id, action, status, "
                            " source_is_always_valid, source_start_datetime, "
                            " source_end_datetime) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', "
                            "        ?, ?, ?)",
                            (run_id, group_id, master_lock_id, master_code.id,
                             name, master_code.code_value, target_lock_id,
                             local_id, action, master_code.is_always_valid,
                             master_code.start_datetime, master_code.end_datetime),
                        )

                    if action == "created":
                        result.created += 1
                    else:
                        result.updated += 1

                except Exception as e:
                    result.errors.append(f"[{target_lock_id}] {action} failed: {e}")
                    result.skipped += 1
                    with self._conn() as conn:
                        conn.execute(
                            "INSERT INTO sync_code_history "
                            "(sync_run_id, group_id, source_lock_id, source_code_id, "
                            " source_code_name, source_code_value, target_lock_id, "
                            " action, status, skip_reason, "
                            " source_is_always_valid, source_start_datetime, "
                            " source_end_datetime) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?)",
                            (run_id, group_id, master_lock_id, master_code.id,
                             name, master_code.code_value, target_lock_id,
                             action, str(e), master_code.is_always_valid,
                             master_code.start_datetime, master_code.end_datetime),
                        )
            else:
                # Dry run — just record intent
                if action == "created":
                    result.created += 1
                elif action == "updated":
                    result.updated += 1

        return result

    # ── Public: run sync for one group ───────────────────────────────────────

    def run_sync(
        self,
        group_id: int,
        schedule_id: Optional[int] = None,
        dry_run: bool = True,
        excluded_codes: set = None,
        skip_discover: bool = False,
    ) -> SyncResult:
        """
        Run the full sync process for all target locks in a group.

        Parameters
        ----------
        group_id     : group to sync
        schedule_id  : sync_schedules.id (optional, for record-keeping)
        dry_run      : if True, only compute intent; make no actual changes
        excluded_codes : set of "name:target_lock_id" strings to skip
        skip_discover  : if True, skip the discover_codes call so Force Sync
                         executes pending jobs without re-checking Schlage cloud

        Returns
        -------
        SyncResult with counts per action type.
        """
        # Before syncing, discover any new codes from Schlage cloud
        # (skip when Force Sync calls this after a login check already ran)
        if not dry_run and not skip_discover:
            self.discover_codes(group_ids=[group_id])

        master_lock_id = self._get_master_lock(group_id)
        if not master_lock_id:
            return SyncResult(run_id=-1, dry_run=dry_run, errors=["no master lock found"])

        lock_ids = self._get_lock_ids_in_group(group_id)
        target_ids = [lid for lid in lock_ids if lid != master_lock_id]
        if not target_ids:
            return SyncResult(run_id=-1, dry_run=dry_run, errors=["no target locks in group"])

        # ── Open Schlage API session ─────────────────────────────────────────
        try:
            auth = self.pyschlage.Auth(
                username=self.creds["username"],
                password=self.creds["password"],
            )
            auth.authenticate()
            api = self.pyschlage.Schlage(auth)
        except Exception as e:
            return SyncResult(run_id=-1, dry_run=dry_run, errors=[f"auth failed: {e}"])

        # ── Open a sync_run record ────────────────────────────────────────────
        with self._conn() as conn:
            cur = conn.execute(
                f"INSERT INTO sync_runs "
                f"(schedule_id, group_id, master_lock_id, dry_run) "
                f"VALUES (?, ?, ?, ?)",
                (schedule_id, group_id, master_lock_id, int(dry_run)),
            )
            run_id = cur.lastrowid

        # ── Fetch master codes ────────────────────────────────────────────────
        master_codes_raw = self._get_codes_for_lock(master_lock_id)
        master_codes = {c.name: c for c in master_codes_raw}

        # ── Aggregate results across all targets ──────────────────────────────
        total = SyncResult(run_id=run_id, dry_run=dry_run)

        for target_id in target_ids:
            try:
                target_codes_raw = self._get_codes_for_lock(target_id)
                target_codes = {c.name: c for c in target_codes_raw}

                # Get a pyschlage Lock client for this target
                from pyschlage import Lock as SchlageLock
                target_lock = SchlageLock(device_id=target_id)
                target_lock._auth = auth

                result = self._sync_target_lock(
                    group_id=group_id,
                    master_lock_id=master_lock_id,
                    target_lock_id=target_id,
                    master_codes=master_codes,
                    target_codes=target_codes,
                    run_id=run_id,
                    dry_run=dry_run,
                    client=target_lock,
                    excluded_codes=excluded_codes,
                )
                total.created += result.created
                total.updated += result.updated
                total.deleted += result.deleted
                total.skipped += result.skipped
                total.checked += result.checked
                total.errors.extend(result.errors)

            except Exception as e:
                total.errors.append(f"[{target_id}] lock error: {e}")

        # ── Finalize sync_run record ───────────────────────────────────────────
        with self._conn() as conn:
            conn.execute(
                "UPDATE sync_runs SET completed_at = ?, status = ?, "
                "codes_checked = ?, codes_created = ?, codes_updated = ?, "
                "codes_deleted = ?, codes_skipped = ?, errors = ? "
                "WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(),
                 "failed" if total.errors else ("partial" if total.skipped else "success"),
                 total.checked, total.created, total.updated,
                 total.deleted, total.skipped,
                 json.dumps(total.errors) if total.errors else None,
                 run_id),
            )

        return total

    # ── Public: preview (dry-run report) ─────────────────────────────────────

    def preview(self, group_id: int) -> dict:
        """
        Return a human-readable preview of what a sync run WOULD do.

        First pulls live codes from Schlage cloud (via discover_codes), inserts
        new codes into local DB, then runs a dry-sync to compute the plan.

        Keys:
          'add'     — list of (code_name, code_value, target_lock_id) to create
          'update'  — list of (code_name, old_value, new_value, target_lock_id) to update
          'delete'  — list of (code_name, target_lock_id) to delete
          'skip'     — list of (code_name, target_lock_id, reason) that would be skipped
          'errors'   — list of error strings
          'pending_jobs' — list of newly discovered codes to be synced
        """
        # Step 1: discover new codes from Schlage cloud
        discovered = self.discover_codes(group_ids=[group_id])
        pending_jobs = discovered.get(group_id, [])

        # Step 2: run dry-sync to compute the full plan
        result = self.run_sync(group_id, dry_run=True)

        preview = {
            "run_id": result.run_id,
            "would_create": result.created,
            "would_update": result.updated,
            "would_delete": result.deleted,
            "would_skip": result.skipped,
            "pending_jobs": pending_jobs,
            "details": result.to_dict(),
        }
        return preview
