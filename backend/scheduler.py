"""
import traceback
Background scheduler for V4 auto force sync.
Replaces APScheduler with a precise, per-group check_time scheduler.
"""
import logging
import threading
import time
import json
from datetime import datetime, timezone
from typing import Optional

from backend.database import get_db, DB_PATH

logger = logging.getLogger(__name__)

# ── Time utilities ─────────────────────────────────────────────────────────────


def parse_hhmm(t: str) -> tuple[int, int]:
    """Parse 'HH:MM' → (hour, minute)."""
    h, m = t.split(':', 1)
    return int(h), int(m)


def next_run_time(check_times: list[str], ref_dt: Optional[datetime] = None) -> datetime:
    """
    Given a list of 'HH:MM' strings (24-hour), return the next datetime
    that should fire from ref_dt (default: now).
    """
    if not check_times:
        return datetime.max.replace(tzinfo=timezone.utc)

    if ref_dt is None:
        ref_dt = datetime.now(timezone.utc)

    # Parse today's times
    today_times = []
    for t in check_times:
        try:
            h, m = parse_hhmm(t)
            dt = ref_dt.replace(hour=h, minute=m, second=0, microsecond=0)
            today_times.append(dt)
        except ValueError:
            continue

    # Next firing is today if we haven't passed the time yet, else tomorrow
    upcoming = [dt for dt in today_times if dt > ref_dt]
    if upcoming:
        return min(upcoming)
    # All times today have passed — next firing is tomorrow
    tomorrow_ref = ref_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_ref = tomorrow_ref.replace(day=ref_dt.day + 1)
    first_t = check_times[0]
    h, m = parse_hhmm(first_t)
    return tomorrow_ref.replace(hour=h, minute=m)


def seconds_until(target_dt: datetime) -> float:
    """Seconds from now until target_dt. Clamps to 0 if already past."""
    now = datetime.now(timezone.utc)
    diff = (target_dt - now).total_seconds()
    return max(0.0, diff)


# ── Per-group sync runner ─────────────────────────────────────────────────────


def run_group_sync(group_id: int, dry_run: bool = False) -> None:
    """
    Run the full Refresh + Force Sync sequence for a group.

    This is the scheduler's equivalent of pressing Refresh then Force Sync.

    Step 1 (Refresh-equivalent): _discover_and_seed_jobs() — detect changes in Schlage cloud
                                  and seed pending sync_jobs (same path the UI uses)
    Step 2 (Force Sync-equivalent): _scheduler_run_sync_jobs() — execute pending jobs
    """
    try:
        import pyschlage
        from backend.auth import decrypt_password
        from backend.routes import codes as codes_module
        from backend.routes import sync as sync_module

        conn = get_db()
        try:
            row = conn.execute(
                "SELECT username, encrypted_password, nonce "
                "FROM credentials WHERE is_owner = 1 LIMIT 1"
            ).fetchone()
            if not row:
                logger.warning("Scheduler: no owner credentials found")
                return
            username = row["username"]
            password = decrypt_password(row["encrypted_password"], row["nonce"])
        finally:
            conn.close()

        # Create a full SchlageSession (with Schlage API object) the same way the UI does
        from backend.auth import SchlageSession
        session = SchlageSession.from_credentials(username, password)

        # Step 1: discover and seed jobs (Refresh-equivalent — uses the exact same
        # _discover_and_seed_jobs that Force Sync in the UI calls)
        if not dry_run:
            logger.info("Scheduler: discovering codes for group %s", group_id)
            codes_module._discover_and_seed_jobs(session, group_id)

        # Step 2: execute pending jobs (Force Sync-equivalent)
        logger.info("Scheduler: running force sync for group %s (dry_run=%s)", group_id, dry_run)
        result = sync_module._scheduler_run_sync_jobs(group_id, dry_run)
        logger.info(
            "Scheduler: sync completed for group %s — created=%s updated=%s deleted=%s failed=%s",
            group_id, result.get("created", 0), result.get("updated", 0),
            result.get("deleted", 0), result.get("failed", 0),
        )
    except Exception as e:
        logger.exception("Scheduler: sync failed for group %s", group_id)


# ── Main scheduler loop ────────────────────────────────────────────────────────


class CheckTimeScheduler:
    """
    Precision scheduler: fires exactly at each group's configured check_times.
    Wakes every 60s to check if any group needs to run.
    """

    def __init__(self):
        self._timer: Optional[threading.Timer] = None
        self._running = False
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        # Per-group locks to prevent duplicate sync threads for the same group
        self._group_locks: dict[int, threading.Lock] = {}

    def _get_group_lock(self, group_id: int) -> threading.Lock:
        """Get or create a lock for a specific group."""
        with self._lock:
            if group_id not in self._group_locks:
                self._group_locks[group_id] = threading.Lock()
            return self._group_locks[group_id]

    def _load_schedules(self) -> list[dict]:
        """Load all enabled schedules from DB."""
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT group_id, check_times FROM sync_schedules WHERE enabled = 1")
        rows = cur.fetchall()
        schedules = []
        for row in rows:
            try:
                check_times = json.loads(row["check_times"]) if row["check_times"] else []
            except json.JSONDecodeError:
                check_times = []
            schedules.append({
                "group_id": row["group_id"],
                "check_times": check_times,
            })
        return schedules

    def _tick(self) -> None:
        """Main loop tick — check if any group needs to run, then reschedule."""
        if self._stop_event.is_set():
            return

        try:
            schedules = self._load_schedules()
            now = datetime.now(timezone.utc)

            for sched in schedules:
                check_times = sched["check_times"]
                if not check_times:
                    continue

                next_run = next_run_time(check_times, now)
                secs = seconds_until(next_run)

                # Determine if this check_time should fire:
                # - secs <= 5:        normal — within 5s of target, fire now
                # - -60 < secs <= 0:  just missed — within last 60s, fire immediately
                # - secs > 5:          future — log normally, schedule normally
                # - secs <= -60:      way past — skip, already rolled to next occurrence

                gid = sched["group_id"]
                group_lock = self._get_group_lock(gid)

                if secs < 0:
                    # Past — fire immediately
                    secs = 0.1
                elif secs > 5:
                    # Future — log and skip
                    logger.info("Scheduler: group %s next fire in %.0f seconds (%s)", gid, secs, next_run.strftime("%H:%M"))
                # Try to acquire the per-group lock — if already held, skip
                acquired = group_lock.acquire(blocking=False)
                if not acquired:
                    logger.info(
                        "Scheduler: group %s already syncing, skipping duplicate fire",
                        gid,
                    )
                    continue

                # Fire via threading.Timer
                delay = secs if secs > 0.1 else 0.1
                logger.info(
                    "Scheduler: check_time for group %s firing (secs=%.1f)",
                    gid, secs,
                )

                def delayed_group_sync(delay: float, group_id: int, group_lock: threading.Lock) -> None:
                    try:
                        time.sleep(delay)
                        if not self._stop_event.is_set():
                            run_group_sync(group_id, dry_run=False)
                    finally:
                        group_lock.release()

                gt = threading.Thread(
                    target=delayed_group_sync,
                    args=(delay, gid, group_lock),
                    daemon=True,
                )
                gt.start()
                logger.info(
                    "Scheduler: group %s next fire in %.0f seconds (%s)",
                    gid, secs, next_run.strftime("%H:%M"),
                )

        except Exception as e:
            logger.error("Scheduler: tick error: %s", e)

        # Reschedule next tick (60 seconds)
        if not self._stop_event.is_set():
            with self._lock:
                self._timer = threading.Timer(60.0, self._tick)
                self._timer.start()

    def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        logger.info("CheckTimeScheduler: starting")
        self._tick()  # kick off first tick immediately

    def stop(self) -> None:
        """Stop the scheduler."""
        if not self._running:
            return
        self._stop_event.set()
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
        self._running = False
        logger.info("CheckTimeScheduler: stopped")


# ── Singleton ─────────────────────────────────────────────────────────────────


_scheduler: Optional[CheckTimeScheduler] = None


def start_scheduler() -> CheckTimeScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = CheckTimeScheduler()
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None