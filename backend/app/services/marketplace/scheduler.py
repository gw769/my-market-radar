from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.marketplace import TrackedKeyword
from app.services.marketplace.runner import create_run, submit_run

_stop = threading.Event()
_thread: threading.Thread | None = None


def next_run_utc(daily_time: str, timezone_name: str, now_utc: datetime | None = None) -> datetime:
    now_utc = now_utc or datetime.now(timezone.utc)
    zone = ZoneInfo(timezone_name)
    local = now_utc.astimezone(zone)
    hour, minute = (int(part) for part in daily_time.split(":"))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc).replace(tzinfo=None)


def _run_due_jobs() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        keywords = db.query(TrackedKeyword).filter(TrackedKeyword.tracking_enabled.is_(True)).all()
        for keyword in keywords:
            zone = ZoneInfo(keyword.timezone)
            local_now = now.astimezone(zone)
            hour, minute = (int(part) for part in keyword.daily_time.split(":"))
            due_today = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            last_local_date = (
                keyword.last_run_at.replace(tzinfo=timezone.utc).astimezone(zone).date()
                if keyword.last_run_at
                else None
            )
            if local_now >= due_today and last_local_date != local_now.date():
                run = create_run(db, keyword, trigger="scheduled")
                submit_run(run.id)
            keyword.next_run_at = next_run_utc(keyword.daily_time, keyword.timezone, now)
        db.commit()
    finally:
        db.close()


def _loop() -> None:
    while not _stop.is_set():
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
