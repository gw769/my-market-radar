from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.core.logging import logger
from app.services.marketplace.runner import execute_run_sync

# Normal marketplace work remains strictly serial in runner._executor(max_workers=1).
# This small executor is used only after the watchdog has already invalidated a stale worker's
# database lease. It lets a replacement start even if the original thread is genuinely hung.
_recovery_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="marketplace-recovery")


def _execute_recovery(run_id: int) -> None:
    try:
        execute_run_sync(run_id)
    except Exception:
        logger.error("stale run 恢复 worker 异常 run=%s", run_id, exc_info=True)


def submit_recovery_run(run_id: int) -> bool:
    try:
        _recovery_executor.submit(_execute_recovery, run_id)
        return True
    except Exception:
        logger.error("提交 stale run 恢复 worker 失败 run=%s", run_id, exc_info=True)
        return False
