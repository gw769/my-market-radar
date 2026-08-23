from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.marketplace import AnalysisRun
from app.services.marketplace.recovery_worker import submit_recovery_run
from app.services.marketplace.runner import submit_run

settings = get_settings()
RECOVERABLE_STATUSES = ("pending", "running")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _reset_for_recovery(run: AnalysisRun, step: str) -> None:
    run.status = "pending"
    # Recovery reuses the same run, so keep the highest user-visible checkpoint. The resumed
    # worker and runner state writes are monotonic and will advance it from here.
    run.current_step = step
    run.started_at = None
    run.completed_at = None
    run.error_message = None
    run.verification_platform = None
    run.worker_id = None
    run.heartbeat_at = None


def _submit_recovered(
    run_ids: list[int],
    source: str,
    submitter: Callable[[int], bool],
) -> int:
    queued = 0
    for run_id in run_ids:
        if submitter(run_id):
            queued += 1
    if run_ids:
        logger.info("%s恢复任务: found=%s queued=%s", source, len(run_ids), queued)
    return queued


def recover_interrupted_runs() -> int:
    """Requeue runs that lost their in-memory worker after a process restart."""
    db = SessionLocal()
    run_ids: list[int] = []
    try:
        runs = (
            db.query(AnalysisRun)
            .filter(AnalysisRun.status.in_(RECOVERABLE_STATUSES))
            .order_by(AnalysisRun.id.asc())
            .all()
        )
        for run in runs:
            _reset_for_recovery(run, "服务重启后等待恢复采集")
            run_ids.append(run.id)
        if runs:
            db.commit()
    finally:
        db.close()

    # After process restart there are no old in-memory threads, so the normal serial queue is
    # exactly what we want.
    return _submit_recovered(run_ids, "启动", submit_run)


def recover_stale_runs(now: datetime | None = None) -> int:
    """Replace running attempts whose live worker lease stopped heartbeating.

    A stale worker can still occupy runner's single normal executor forever. Therefore stale
    recovery must not call submit_run(), which intentionally deduplicates against that old
    in-memory run_id. Instead it uses a small independent recovery executor. The database worker
    lease is cleared before submission, so any old worker that wakes later loses ownership and
    cannot write checkpoints/final results.
    """
    now = now or _utcnow()
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(seconds=settings.RUN_STALE_AFTER_SECONDS)

    db = SessionLocal()
    run_ids: list[int] = []
    try:
        runs = (
            db.query(AnalysisRun)
            .filter(AnalysisRun.status == "running")
            .order_by(AnalysisRun.id.asc())
            .all()
        )
        for run in runs:
            lease_time = run.heartbeat_at or run.started_at
            if lease_time is None or lease_time > cutoff:
                continue
            logger.warning(
                "检测到采集 worker 心跳超时 run=%s worker=%s heartbeat=%s",
                run.id,
                run.worker_id,
                lease_time,
            )
            _reset_for_recovery(run, "采集 worker 心跳超时，启动独立恢复 worker")
            run_ids.append(run.id)
        if run_ids:
            db.commit()
    finally:
        db.close()

    return _submit_recovered(run_ids, "心跳超时", submit_recovery_run)
