"""
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
from backend.sync_logic import SyncEngine

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


def run_group_sync(group_id: int) -> None:
    """Decrypt credentials and run the full refresh + force sync sequence."""
    try:
        import pyschlage
        from backend.auth import decrypt_password

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
            creds = {"username": username, "password": password}
        finally:
            conn.close()

        engine = SyncEngine(str(DB_PATH), pyschlage, creds)
        engine.run_sync(group_id, dry_run=False)
        logger.info("Scheduler: sync completed for group %s", group_id)
    except Exception as e:
        logger.error("Scheduler: sync failed for group %s: %s", group_id, e)


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

                # Fire if within 5 seconds of target (accounts for 60s wake interval)
                if secs <= 5:
                    gid = sched["group_id"]
                    logger.info("Scheduler: firing check_time for group %s", gid)
                    # Run sync in a separate thread so tick doesn't block
                    t = threading.Thread(target=run_group_sync, args=(gid,), daemon=True)
                    t.start()
                    # Recalculate next run after firing (it was today, next should be tomorrow)
                    next_run = next_run_time(check_times, now)
                    secs = seconds_until(next_run)

                # Reschedule this group's next fire
                if secs > 0:
                    def delayed_group_sync(delay: float, gid: int) -> None:
                        time.sleep(delay)
                        if not self._stop_event.is_set():
                            run_group_sync(gid)

                    gt = threading.Thread(
                        target=delayed_group_sync,
                        args=(secs, sched["group_id"]),
                        daemon=True,
                    )
                    gt.start()
                    logger.info(
                        "Scheduler: group %s next fire in %.0f seconds (%s)",
                        sched["group_id"], secs, next_run.strftime("%H:%M"),
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
