from __future__ import annotations

from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.marketplace import AnalysisRun
from app.services.marketplace.runner import submit_run


RECOVERABLE_STATUSES = ("pending", "running")


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
            run.status = "pending"
            run.progress = 0
            run.current_step = "服务重启后等待恢复采集"
            run.started_at = None
            run.completed_at = None
            run.error_message = None
            run.verification_platform = None
            run_ids.append(run.id)
        if runs:
            db.commit()
    finally:
        db.close()

    queued = 0
    for run_id in run_ids:
        if submit_run(run_id):
            queued += 1

    if run_ids:
        logger.info("启动恢复任务: found=%s queued=%s", len(run_ids), queued)
    return queued
