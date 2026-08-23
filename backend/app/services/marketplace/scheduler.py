from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.marketplace import TrackedKeyword
from app.services.marketplace.recovery import recover_stale_runs
from app.services.marketplace.runner import create_run, submit_run

_stop = threading.Event()
_thread: threading.Thread | None = None


def _aware_utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def next_run_utc(daily_time: str, timezone_name: str, now_utc: datetime | None = None) -> datetime:
    now_utc = _aware_utc(now_utc)
    zone = ZoneInfo(timezone_name)
    local = now_utc.astimezone(zone)
    hour, minute = (int(part) for part in daily_time.split(":"))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc).replace(tzinfo=None)


def initial_next_run_utc(
    daily_time: str,
    timezone_name: str,
    last_run_at: datetime | None,
    now_utc: datetime | None = None,
) -> datetime:
    """Initialize migrated/missing schedules while preserving one same-day catch-up run."""
    now_utc = _aware_utc(now_utc)
    zone = ZoneInfo(timezone_name)
    local_now = now_utc.astimezone(zone)
    hour, minute = (int(part) for part in daily_time.split(":"))
    due_today = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    last_local_date = (
        _aware_utc(last_run_at).astimezone(zone).date()
        if last_run_at is not None
        else None
    )
    if local_now >= due_today and last_local_date != local_now.date():
        return now_utc.replace(tzinfo=None)
    return next_run_utc(daily_time, timezone_name, now_utc)


def _run_due_jobs() -> None:
    db = SessionLocal()
    try:
        now_utc = _aware_utc()
        now_naive = now_utc.replace(tzinfo=None)
        keywords = (
            db.query(TrackedKeyword)
            .filter(TrackedKeyword.tracking_enabled.is_(True))
            .all()
        )
        for keyword in keywords:
            if keyword.next_run_at is None:
                keyword.next_run_at = initial_next_run_utc(
                    keyword.daily_time,
                    keyword.timezone,
                    keyword.last_run_at,
                    now_utc,
                )

            if keyword.next_run_at > now_naive:
                continue

            run = create_run(db, keyword, trigger="scheduled")
            keyword.next_run_at = next_run_utc(keyword.daily_time, keyword.timezone, now_utc)
            db.commit()

            if run.status == "pending":
                submit_run(run.id)
    finally:
        db.close()


def _loop() -> None:
    while not _stop.is_set():
        try:
            recover_stale_runs()
        except Exception as exc:
            logger.error("采集 worker 心跳巡检失败: %s", exc, exc_info=True)
        try:
            _run_due_jobs()
        except Exception as exc:
            logger.error("每日跟踪调度失败: %s", exc, exc_info=True)
        _stop.wait(30)


def start_scheduler() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="daily-marketplace-scheduler", daemon=True)
    _thread.start()


def stop_scheduler() -> None:
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=2)
