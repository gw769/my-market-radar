from __future__ import annotations

import math
from datetime import timezone
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.marketplace import AnalysisRun, ListingSnapshot, TrackedKeyword
from app.models.user import User
from app.schemas.marketplace import KeywordCreate, KeywordUpdate
from app.services.marketplace.query_localization import marketplace_search_term
from app.services.marketplace.query_localization import effective_localization
from app.services.marketplace.ai import ai_status
from app.services.marketplace.report import build_report
from app.services.marketplace.runner import create_run, open_verification_browser, submit_run
from app.services.marketplace.scheduler import next_run_utc

router = APIRouter(prefix="/api", tags=["Malaysia marketplace intelligence"])
settings = get_settings()
RESULT_STATUSES = ("completed", "partial")


def _iso(value):
    if not value:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.isoformat()


def _keyword_payload(
    keyword: TrackedKeyword,
    latest: AnalysisRun | None = None,
    latest_result: AnalysisRun | None = None,
    *,
    detail: Literal["full", "summary"] = "full",
) -> dict:
    run_payload = _run_summary_payload if detail == "summary" else _run_payload
    localization = effective_localization(keyword.keyword, keyword.localization)
    return {
        "id": keyword.id,
        "keyword": keyword.keyword,
        "marketplace_query": marketplace_search_term(keyword.keyword, localization),
        "localization": localization,
        "platforms": keyword.platforms,
        "results_limit": keyword.results_limit,
        "search_pages": settings.SEARCH_PAGES,
        "max_results_per_platform": keyword.results_limit * settings.SEARCH_PAGES,
        "tracking_enabled": keyword.tracking_enabled,
        "daily_time": keyword.daily_time,
        "timezone": keyword.timezone,
        "last_run_at": _iso(keyword.last_run_at),
        "last_success_at": _iso(keyword.last_success_at),
        "next_run_at": _iso(keyword.next_run_at),
        "latest_run": run_payload(latest, keyword.keyword) if latest else None,
        "latest_result_run": run_payload(latest_result, keyword.keyword) if latest_result else None,
    }


def _run_payload(run: AnalysisRun, keyword: str | None = None) -> dict:
    return {
        "id": run.id,
        "keyword_id": run.keyword_id,
        "keyword": keyword if keyword is not None else (
            run.tracked_keyword.keyword if run.tracked_keyword else None
        ),
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


def _run_summary_payload(run: AnalysisRun, keyword: str | None = None) -> dict:
    analysis = run.analysis if isinstance(run.analysis, dict) else {}
    evidence = analysis.get("evidence")
    compact_evidence = (
        {"grade": evidence.get("grade"), "label": evidence.get("label")}
        if isinstance(evidence, dict)
        else None
    )
    segments = analysis.get("opportunity_segments")
    top_segment = segments[0] if isinstance(segments, list) and segments else None
    compact_top_segment = (
        {
            "label": top_segment.get("label"),
            "ranking_reliability": top_segment.get("ranking_reliability"),
        }
        if isinstance(top_segment, dict)
        else None
    )
    prices: list[float] = []
    platform_scores = run.platform_scores if isinstance(run.platform_scores, dict) else {}
    for score in platform_scores.values():
        metrics = score.get("metrics") if isinstance(score, dict) else None
        value = metrics.get("median_price") if isinstance(metrics, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            if math.isfinite(numeric):
                prices.append(numeric)
    return {
        "id": run.id,
        "keyword_id": run.keyword_id,
        "keyword": keyword if keyword is not None else (
            run.tracked_keyword.keyword if run.tracked_keyword else None
        ),
        "status": run.status,
        "progress": run.progress,
        "current_step": run.current_step,
        "verification_platform": run.verification_platform,
        "opportunity_score": run.opportunity_score,
        "verdict": run.verdict,
        "confidence": run.confidence,
        "analysis": {
            "platform_errors": analysis.get("platform_errors") or {},
            "counts": analysis.get("counts") or {},
            "evidence": compact_evidence,
            "top_segment": compact_top_segment,
            "median_price": sum(prices) / len(prices) if prices else None,
        },
        "error_message": run.error_message,
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
    }


def _latest_runs_by_keyword(
    db: Session,
    keyword_ids: list[int],
    *,
    result_only: bool = False,
) -> dict[int, AnalysisRun]:
    if not keyword_ids:
        return {}
    latest_ids = db.query(
        AnalysisRun.keyword_id.label("keyword_id"),
        func.max(AnalysisRun.id).label("run_id"),
    ).filter(AnalysisRun.keyword_id.in_(keyword_ids))
    if result_only:
        latest_ids = latest_ids.filter(AnalysisRun.status.in_(RESULT_STATUSES))
    latest_ids = latest_ids.group_by(AnalysisRun.keyword_id).subquery()
    rows = (
        db.query(AnalysisRun)
        .join(latest_ids, AnalysisRun.id == latest_ids.c.run_id)
        .all()
    )
    return {int(run.keyword_id): run for run in rows}


def _latest_result(db: Session, keyword_id: int) -> AnalysisRun | None:
    return (
        db.query(AnalysisRun)
        .filter(AnalysisRun.keyword_id == keyword_id, AnalysisRun.status.in_(RESULT_STATUSES))
        .order_by(AnalysisRun.id.desc())
        .first()
    )


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


@router.get("/marketplace-defaults")
def marketplace_defaults(current_user: User = Depends(get_current_user)):
    return {
        "success": True,
        "data": {
            "results_limit": settings.DEFAULT_RESULTS_LIMIT,
            "search_pages": settings.SEARCH_PAGES,
            "max_results_per_platform": settings.DEFAULT_RESULTS_LIMIT * settings.SEARCH_PAGES,
            "daily_time": settings.DEFAULT_DAILY_TIME,
            "timezone": settings.DEFAULT_TIMEZONE,
            "platforms": ["shopee", "lazada"],
            "ai": ai_status(),
        },
    }


@router.post("/keywords")
def add_keyword(payload: KeywordCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    existing = db.query(TrackedKeyword).filter(func.lower(TrackedKeyword.keyword) == payload.keyword.lower(), TrackedKeyword.user_id == current_user.id).first()
    if existing:
        _apply_keyword_settings(existing, payload)
        db.commit()
        db.refresh(existing)
        previous_result = _latest_result(db, existing.id)
        run = create_run(db, existing, trigger="manual")
        queued = submit_run(run.id)
        return {"success": True, "queued": queued, "keyword": _keyword_payload(existing, run, previous_result), "run": _run_payload(run)}

    keyword = TrackedKeyword(user_id=current_user.id, keyword=payload.keyword)
    _apply_keyword_settings(keyword, payload)
    db.add(keyword)
    db.commit()
    db.refresh(keyword)
    run = create_run(db, keyword, trigger="manual")
    queued = submit_run(run.id)
    return {"success": True, "queued": queued, "keyword": _keyword_payload(keyword, run, None), "run": _run_payload(run)}


@router.get("/keywords")
def list_keywords(
    detail: Annotated[Literal["full", "summary"], Query()] = "full",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    keywords = (
        db.query(TrackedKeyword)
        .filter(TrackedKeyword.user_id == current_user.id)
        .order_by(TrackedKeyword.updated_at.desc())
        .all()
    )
    keyword_ids = [int(keyword.id) for keyword in keywords]
    latest_runs = _latest_runs_by_keyword(db, keyword_ids)
    latest_results = _latest_runs_by_keyword(db, keyword_ids, result_only=True)
    data = [
        _keyword_payload(
            keyword,
            latest_runs.get(int(keyword.id)),
            latest_results.get(int(keyword.id)),
            detail=detail,
        )
        for keyword in keywords
    ]
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
    return {"success": True, "data": _keyword_payload(keyword, latest, _latest_result(db, keyword.id))}


@router.delete("/keywords/{keyword_id}")
def delete_keyword(keyword_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    keyword = _owned_keyword(db, current_user.id, keyword_id)
    active = db.query(AnalysisRun).filter(
        AnalysisRun.keyword_id == keyword.id,
        AnalysisRun.status.in_(("pending", "running")),
    ).first()
    if active:
        raise HTTPException(status_code=409, detail="当前关键词正在采集队列中，任务结束或暂停后再删除")
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
def get_items(
    run_id: int,
    platform: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = _owned_run(db, current_user.id, run_id)
    filters = [ListingSnapshot.run_id == run.id]
    if platform:
        filters.append(ListingSnapshot.platform == platform)
    search_text = " ".join(str(q or "").split())
    if search_text:
        escaped = search_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(ListingSnapshot.title.ilike(f"%{escaped}%", escape="\\"))

    image_url = case(
        (func.lower(ListingSnapshot.image_url).like("data:%"), None),
        (func.lower(ListingSnapshot.image_url).like("blob:%"), None),
        else_=ListingSnapshot.image_url,
    ).label("image_url")
    query = db.query(
        ListingSnapshot.id,
        ListingSnapshot.platform,
        ListingSnapshot.item_id,
        ListingSnapshot.title,
        ListingSnapshot.product_url,
        image_url,
        ListingSnapshot.price,
        ListingSnapshot.original_price,
        ListingSnapshot.discount_percent,
        ListingSnapshot.sold_count,
        ListingSnapshot.rating,
        ListingSnapshot.review_count,
        ListingSnapshot.seller_name,
        ListingSnapshot.seller_location,
        ListingSnapshot.is_sponsored,
        ListingSnapshot.search_rank,
        ListingSnapshot.raw_data["search_page"].as_integer().label("search_page"),
        ListingSnapshot.raw_data["page_rank"].as_integer().label("page_rank"),
        ListingSnapshot.raw_data["page_size"].as_integer().label("page_size"),
        ListingSnapshot.raw_data["shopdora"].label("shopdora"),
        ListingSnapshot.data_quality,
        ListingSnapshot.collected_at,
    ).filter(*filters)
    total = db.query(func.count(ListingSnapshot.id)).filter(*filters).scalar() if limit else None
    query = query.order_by(ListingSnapshot.platform, ListingSnapshot.search_rank)
    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    rows = query.all()
    data = [{
        "id": row.id, "platform": row.platform, "item_id": row.item_id, "title": row.title,
        "product_url": row.product_url, "image_url": row.image_url, "price": row.price,
        "original_price": row.original_price, "discount_percent": row.discount_percent,
        "sold_count": row.sold_count, "rating": row.rating, "review_count": row.review_count,
        "seller_name": row.seller_name, "seller_location": row.seller_location,
        "is_sponsored": row.is_sponsored, "search_rank": row.search_rank,
        "search_page": row.search_page,
        "page_rank": row.page_rank,
        "page_size": row.page_size,
        "shopdora": row.shopdora if isinstance(row.shopdora, dict) else None,
        "data_quality": row.data_quality, "collected_at": _iso(row.collected_at),
    } for row in rows]
    response = {"success": True, "data": data}
    if limit is not None:
        total_count = int(total or 0)
        response["pagination"] = {
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(data) < total_count,
        }
    return response


@router.post("/runs/{run_id}/verification-browser")
def verification_browser(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = _owned_run(db, current_user.id, run_id)
    if run.status != "needs_verification":
        raise HTTPException(status_code=409, detail="当前任务不需要人工验证")
    try:
        url = open_verification_browser(run.id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "url": url, "message": "完成验证后保持该验证标签页打开，再点击继续采集。"}


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = _owned_run(db, current_user.id, run_id)
    if run.status not in ("needs_verification", "failed", "partial"):
        raise HTTPException(status_code=409, detail="当前任务不能继续")

    if run.status in ("failed", "partial"):
        keyword = run.tracked_keyword
        retry = create_run(db, keyword, trigger="retry")
        queued = submit_run(retry.id)
        return {"success": True, "queued": queued, "run": _run_payload(retry), "retry_of": run.id}

    run.status = "pending"
    run.current_step = "等待验证后重新采集"
    run.error_message = None
    run.verification_platform = None
    run.completed_at = None
    db.commit()
    queued = submit_run(run.id)
    return {"success": True, "queued": queued, "run": _run_payload(run)}


@router.get("/runs/{run_id}/report.xlsx")
def report(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = _owned_run(db, current_user.id, run_id)
    if run.status not in RESULT_STATUSES:
        raise HTTPException(status_code=409, detail="任务尚未完成")
    output = build_report(db, run)
    filename = quote(f"{run.tracked_keyword.keyword}_MY_marketplace.xlsx")
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    keywords = db.query(TrackedKeyword).filter(TrackedKeyword.user_id == current_user.id).all()
    keyword_ids = [item.id for item in keywords]
    if not keyword_ids:
        return {"success": True, "data": {
            "keyword_count": 0, "tracking_count": 0, "needs_verification": 0,
            "completed_runs": 0, "platform_counts": {}, "latest_runs": [], "score_history": [],
        }}

    run_base = db.query(AnalysisRun).filter(AnalysisRun.keyword_id.in_(keyword_ids))
    completed_runs = run_base.filter(AnalysisRun.status.in_(RESULT_STATUSES)).count()
    needs_verification = run_base.filter(AnalysisRun.status == "needs_verification").count()

    recent_runs = run_base.order_by(AnalysisRun.id.desc()).limit(50).all()
    latest_runs = recent_runs[:8]
    keyword_names = {int(keyword.id): keyword.keyword for keyword in keywords}
    score_history = [
        {
            "run_id": run.id,
            "keyword": keyword_names.get(int(run.keyword_id)),
            "score": run.opportunity_score,
            "created_at": _iso(run.created_at),
        }
        for run in reversed(recent_runs)
        if run.status in RESULT_STATUSES and run.opportunity_score is not None
    ][-30:]

    stable_runs = _latest_runs_by_keyword(db, keyword_ids, result_only=True)
    stable_run_ids = [result.id for result in stable_runs.values()]
    platform_counts = dict(
        db.query(ListingSnapshot.platform, func.count(ListingSnapshot.id))
        .filter(ListingSnapshot.run_id.in_(stable_run_ids))
        .group_by(ListingSnapshot.platform)
        .all()
    ) if stable_run_ids else {}

    return {"success": True, "data": {
        "keyword_count": len(keywords),
        "tracking_count": sum(bool(item.tracking_enabled) for item in keywords),
        "needs_verification": needs_verification,
        "completed_runs": completed_runs,
        "platform_counts": platform_counts,
        "latest_runs": [
            _run_summary_payload(run, keyword_names.get(int(run.keyword_id)))
            for run in latest_runs
        ],
        "score_history": score_history,
    }}
