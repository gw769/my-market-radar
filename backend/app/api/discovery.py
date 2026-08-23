from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.marketplace import AnalysisRun, TrackedKeyword
from app.models.user import User
from app.schemas.marketplace import RunCreate
from app.services.marketplace.query_localization import marketplace_search_term
from app.services.marketplace.runner import create_run, submit_run

router = APIRouter(prefix="/api/discovery", tags=["Malaysia market discovery"])
settings = get_settings()
ACTIVE_STATUSES = ("pending", "running", "needs_verification")


def _run_payload(run: AnalysisRun) -> dict:
    return {
        "id": run.id,
        "keyword_id": run.keyword_id,
        "trigger": run.trigger,
        "status": run.status,
        "progress": run.progress,
        "current_step": run.current_step,
        "analysis": run.analysis or {},
    }


@router.post("/keywords/{keyword_id}/deep-scan")
def deep_scan_keyword(
    keyword_id: int,
    payload: RunCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a larger one-off discovery scan without mutating keyword defaults."""
    keyword = (
        db.query(TrackedKeyword)
        .filter(TrackedKeyword.id == keyword_id, TrackedKeyword.user_id == current_user.id)
        .first()
    )
    if not keyword:
        raise HTTPException(status_code=404, detail="关键词不存在")

    active = (
        db.query(AnalysisRun)
        .filter(
            AnalysisRun.keyword_id == keyword.id,
            AnalysisRun.status.in_(ACTIVE_STATUSES),
        )
        .order_by(AnalysisRun.id.desc())
        .first()
    )
    if active:
        return {"success": True, "queued": False, "reason": "active_run", "run": _run_payload(active)}

    run = create_run(db, keyword, trigger="discovery_deep")
    # If another request won a race between the active check and create_run, do not rewrite its
    # frozen request settings. The trigger makes that distinction without another schema/table.
    if run.trigger != "discovery_deep" or run.status != "pending":
        return {"success": True, "queued": False, "reason": "active_run", "run": _run_payload(run)}

    base_platforms = [str(platform) for platform in (keyword.platforms or [])]
    request_config = {
        "keyword": keyword.keyword,
        "marketplace_query": marketplace_search_term(keyword.keyword),
        "platforms": list(payload.platforms or base_platforms),
        "results_limit": int(payload.results_limit or keyword.results_limit),
        "search_pages": settings.SEARCH_PAGES,
    }
    request_config["max_results_per_platform"] = (
        request_config["results_limit"] * request_config["search_pages"]
    )
    run.analysis = {**(run.analysis or {}), "request_config": request_config, "scan_mode": "discovery_deep"}
    db.commit()
    db.refresh(run)

    queued = submit_run(run.id)
    return {
        "success": True,
        "queued": queued,
        "run": _run_payload(run),
        "request_config": request_config,
        "keyword_defaults": {
            "platforms": keyword.platforms,
            "results_limit": keyword.results_limit,
            "tracking_enabled": keyword.tracking_enabled,
        },
    }
