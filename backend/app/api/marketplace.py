from __future__ import annotations

from datetime import timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.marketplace import AnalysisRun, ListingSnapshot, TrackedKeyword
from app.models.user import User
from app.schemas.marketplace import KeywordCreate, KeywordUpdate
from app.services.marketplace.report import build_report
from app.services.marketplace.runner import create_run, open_verification_browser, submit_run
from app.services.marketplace.scheduler import next_run_utc

router = APIRouter(prefix="/api", tags=["Malaysia marketplace intelligence"])


def _iso(value):
    if not value:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.isoformat()


def _keyword_payload(keyword: TrackedKeyword, latest: AnalysisRun | None = None) -> dict:
    return {
        "id": keyword.id,
        "keyword": keyword.keyword,
        "platforms": keyword.platforms,
        "results_limit": keyword.results_limit,
        "tracking_enabled": keyword.tracking_enabled,
        "daily_time": keyword.daily_time,
        "timezone": keyword.timezone,
        "last_run_at": _iso(keyword.last_run_at),
        "last_success_at": _iso(keyword.last_success_at),
        "next_run_at": _iso(keyword.next_run_at),
        "latest_run": _run_payload(latest) if latest else None,
    }


def _run_payload(run: AnalysisRun) -> dict:
    return {
        "id": run.id,
        "keyword_id": run.keyword_id,
        "keyword": run.tracked_keyword.keyword if run.tracked_keyword else None,
        "trigger": run.trigger,
        "status": run.status,
        "progress": run.progress,
        "current_step": run.current_step,
        "verification_platform": run.verification_platform,
        "opportunity_score": run.opportunity_score,
        "verdict": run.verdict,
        "confidence": run.confidence,
        "platform_scores": run.platform_scores or {},
        "analysis": run.analysis or {},
        "error_message": run.error_message,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
    }


def _owned_keyword(db: Session, user_id: int, keyword_id: int) -> TrackedKeyword:
    keyword = db.query(TrackedKeyword).filter(TrackedKeyword.id == keyword_id, TrackedKeyword.user_id == user_id).first()
    if not keyword:
        raise HTTPException(status_code=404, detail="关键词不存在")
    return keyword


def _owned_run(db: Session, user_id: int, run_id: int) -> AnalysisRun:
    run = db.query(AnalysisRun).join(TrackedKeyword).filter(AnalysisRun.id == run_id, TrackedKeyword.user_id == user_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="分析任务不存在")
    return run


def _apply_keyword_settings(keyword: TrackedKeyword, payload: KeywordCreate) -> None:
    keyword.platforms = payload.platforms
    keyword.results_limit = payload.results_limit
    keyword.tracking_enabled = payload.tracking_enabled
    keyword.daily_time = payload.daily_time
    keyword.timezone = payload.timezone
    keyword.next_run_at = next_run_utc(payload.daily_time, payload.timezone) if payload.tracking_enabled else None


@router.post("/keywords")
def add_keyword(payload: KeywordCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    existing = db.query(TrackedKeyword).filter(func.lower(TrackedKeyword.keyword) == payload.keyword.lower(), TrackedKeyword.user_id == current_user.id).first()
    if existing:
        _apply_keyword_settings(existing, payload)
        db.commit()
        db.refresh(existing)
        run = create_run(db, existing, trigger="manual")
        queued = submit_run(run.id)
        return {"success": True, "queued": queued, "keyword": _keyword_payload(existing, run), "run": _run_payload(run)}

    keyword = TrackedKeyword(user_id=current_user.id, keyword=payload.keyword)
    _apply_keyword_settings(keyword, payload)
    db.add(keyword)
    db.commit()
    db.refresh(keyword)
    run = create_run(db, keyword, trigger="manual")
    queued = submit_run(run.id)
    return {"success": True, "queued": queued, "keyword": _keyword_payload(keyword, run), "run": _run_payload(run)}


@router.get("/keywords")
def list_keywords(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    keywords = db.query(TrackedKeyword).filter(TrackedKeyword.user_id == current_user.id).order_by(TrackedKeyword.updated_at.desc()).all()
    data = []
    for keyword in keywords:
        latest = db.query(AnalysisRun).filter(AnalysisRun.keyword_id == keyword.id).order_by(AnalysisRun.id.desc()).first()
        data.append(_keyword_payload(keyword, latest))
    return {"success": True, "data": data}


@router.patch("/keywords/{keyword_id}")
def update_keyword(keyword_id: int, payload: KeywordUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    keyword = _owned_keyword(db, current_user.id, keyword_id)
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(keyword, key, value)
    keyword.next_run_at = next_run_utc(keyword.daily_time, keyword.timezone) if keyword.tracking_enabled else None
    db.commit()
    db.refresh(keyword)
    latest = db.query(AnalysisRun).filter(AnalysisRun.keyword_id == keyword.id).order_by(AnalysisRun.id.desc()).first()
    return {"success": True, "data": _keyword_payload(keyword, latest)}


@router.delete("/keywords/{keyword_id}")
def delete_keyword(keyword_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    keyword = _owned_keyword(db, current_user.id, keyword_id)
    db.delete(keyword)
    db.commit()
    return {"success": True}


@router.post("/keywords/{keyword_id}/runs")
def run_keyword(keyword_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    keyword = _owned_keyword(db, current_user.id, keyword_id)
    run = create_run(db, keyword, trigger="manual")
    queued = submit_run(run.id)
    return {"success": True, "queued": queued, "run": _run_payload(run)}


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return {"success": True, "data": _run_payload(_owned_run(db, current_user.id, run_id))}


@router.get("/runs/{run_id}/items")
def get_items(run_id: int, platform: str | None = Query(default=None), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = _owned_run(db, current_user.id, run_id)
    query = db.query(ListingSnapshot).filter(ListingSnapshot.run_id == run.id)
    if platform:
        query = query.filter(ListingSnapshot.platform == platform)
    rows = query.order_by(ListingSnapshot.platform, ListingSnapshot.search_rank).all()
    return {"success": True, "data": [{
        "id": row.id, "platform": row.platform, "item_id": row.item_id, "title": row.title,
        "product_url": row.product_url, "image_url": row.image_url, "price": row.price,
        "original_price": row.original_price, "discount_percent": row.discount_percent,
        "sold_count": row.sold_count, "rating": row.rating, "review_count": row.review_count,
        "seller_name": row.seller_name, "seller_location": row.seller_location,
        "is_sponsored": row.is_sponsored, "search_rank": row.search_rank,
        "data_quality": row.data_quality, "collected_at": _iso(row.collected_at),
    } for row in rows]}


@router.post("/runs/{run_id}/verification-browser")
def verification_browser(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = _owned_run(db, current_user.id, run_id)
    if run.status != "needs_verification":
        raise HTTPException(status_code=409, detail="当前任务不需要人工验证")
    try:
        url = open_verification_browser(run.id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "url": url, "message": "完成验证后保持项目 Chrome 窗口打开，再点击继续采集。"}


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = _owned_run(db, current_user.id, run_id)
    if run.status not in ("needs_verification", "failed", "partial"):
        raise HTTPException(status_code=409, detail="当前任务不能继续")
    run.status = "pending"
    run.progress = 0
    run.current_step = "等待重新采集"
    run.error_message = None
    run.verification_platform = None
    db.commit()
    queued = submit_run(run.id)
    return {"success": True, "queued": queued, "run": _run_payload(run)}


@router.get("/runs/{run_id}/report.xlsx")
def report(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = _owned_run(db, current_user.id, run_id)
    if run.status not in ("completed", "partial"):
        raise HTTPException(status_code=409, detail="任务尚未完成")
    output = build_report(db, run)
    filename = quote(f"{run.tracked_keyword.keyword}_MY_marketplace.xlsx")
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    keywords = db.query(TrackedKeyword).filter(TrackedKeyword.user_id == current_user.id).all()
    keyword_ids = [item.id for item in keywords]
    runs = db.query(AnalysisRun).filter(AnalysisRun.keyword_id.in_(keyword_ids)).order_by(AnalysisRun.id.desc()).limit(50).all() if keyword_ids else []
    completed_ids = [run.id for run in runs if run.status in ("completed", "partial")]
    platform_counts = dict(db.query(ListingSnapshot.platform, func.count(ListingSnapshot.id)).filter(ListingSnapshot.run_id.in_(completed_ids)).group_by(ListingSnapshot.platform).all()) if completed_ids else {}
    return {"success": True, "data": {
        "keyword_count": len(keywords), "tracking_count": sum(bool(x.tracking_enabled) for x in keywords),
        "needs_verification": sum(x.status == "needs_verification" for x in runs),
        "completed_runs": sum(x.status in ("completed", "partial") for x in runs),
        "platform_counts": platform_counts,
        "latest_runs": [_run_payload(run) for run in runs[:8]],
        "score_history": [{"run_id": run.id, "keyword": run.tracked_keyword.keyword, "score": run.opportunity_score, "created_at": _iso(run.created_at)} for run in reversed(runs) if run.opportunity_score is not None][-30:],
    }}
