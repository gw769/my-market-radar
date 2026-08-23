from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.marketplace import AnalysisRun, ListingSnapshot, TrackedKeyword
from app.services.marketplace.scoring import relevance_score

RESULT_STATUSES = ("completed", "partial")
MIN_INTERVAL_HOURS = 6.0


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _run_time(run: AnalysisRun) -> datetime | None:
    return run.completed_at or run.created_at


def _safe_delta(current: int | None, previous: int | None) -> int | None:
    if current is None or previous is None or current < previous:
        return None
    return current - previous


def _price_change_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or current <= 0 or previous <= 0:
        return None
    return (current - previous) / previous * 100


def _summarize_rows(rows: list[dict[str, Any]], current_count: int, interval_hours: float) -> dict[str, Any]:
    matched = len(rows)
    sold_deltas = [float(row["sold_delta"]) for row in rows if row["sold_delta"] is not None]
    review_deltas = [float(row["review_delta"]) for row in rows if row["review_delta"] is not None]
    price_changes = [float(row["price_change_pct"]) for row in rows if row["price_change_pct"] is not None]
    rank_changes = [float(row["rank_change"]) for row in rows if row["rank_change"] is not None]

    activity_rows = [
        row for row in rows
        if row["sold_delta"] is not None or row["review_delta"] is not None
    ]
    active_rows = [
        row for row in activity_rows
        if (row["sold_delta"] or 0) > 0 or (row["review_delta"] or 0) > 0
    ]
    activity_share = len(active_rows) / len(activity_rows) * 100 if activity_rows else None
    match_rate = matched / current_count * 100 if current_count else 0.0
    sample_factor = min(1.0, matched / 10.0)
    match_factor = min(1.0, match_rate / 70.0)
    interval_factor = min(1.0, interval_hours / 24.0) if interval_hours >= MIN_INTERVAL_HOURS else 0.0
    reliability = sample_factor * 0.45 + match_factor * 0.35 + interval_factor * 0.20

    days = interval_hours / 24.0 if interval_hours >= MIN_INTERVAL_HOURS else None
    median_sold_delta = _median(sold_deltas)
    median_review_delta = _median(review_deltas)
    median_abs_price_change = _median([abs(value) for value in price_changes])

    return {
        "matched_items": matched,
        "current_items": current_count,
        "match_rate": round(match_rate, 1),
        "activity_share": round(activity_share, 1) if activity_share is not None else None,
        "sold_delta_coverage": round(len(sold_deltas) / matched * 100, 1) if matched else 0.0,
        "review_delta_coverage": round(len(review_deltas) / matched * 100, 1) if matched else 0.0,
        "median_sold_delta": round(median_sold_delta, 1) if median_sold_delta is not None else None,
        "median_sold_velocity_per_day": (
            round(median_sold_delta / days, 2) if median_sold_delta is not None and days else None
        ),
        "median_review_delta": round(median_review_delta, 1) if median_review_delta is not None else None,
        "median_review_velocity_per_day": (
            round(median_review_delta / days, 2) if median_review_delta is not None and days else None
        ),
        "median_abs_price_change_pct": (
            round(median_abs_price_change, 2) if median_abs_price_change is not None else None
        ),
        "median_rank_change": round(_median(rank_changes), 1) if rank_changes else None,
        "reliability": round(reliability * 100, 1),
    }


def _relevant_snapshot_rows(rows: list[ListingSnapshot], keyword: str) -> list[ListingSnapshot]:
    if not keyword:
        return []
    return [
        row for row in rows
        if relevance_score(keyword, str(row.title or "")) >= 0.6
    ]


def build_keyword_trend(db: Session, keyword_id: int) -> dict[str, Any]:
    runs = (
        db.query(AnalysisRun)
        .filter(
            AnalysisRun.keyword_id == keyword_id,
            AnalysisRun.status.in_(RESULT_STATUSES),
        )
        .order_by(AnalysisRun.id.desc())
        .limit(2)
        .all()
    )
    if len(runs) < 2:
        return {
            "status": "insufficient_history",
            "message": "至少需要两次稳定 completed/partial 快照才能判断近期动量。",
            "current_run_id": runs[0].id if runs else None,
            "previous_run_id": None,
            "interval_hours": None,
            "overall": None,
            "platforms": {},
            "recommendations": [],
        }

    keyword_row = db.query(TrackedKeyword).filter(TrackedKeyword.id == keyword_id).first()
    keyword_text = str(keyword_row.keyword if keyword_row else "")
    current_run, previous_run = runs[0], runs[1]
    current_time = _run_time(current_run)
    previous_time = _run_time(previous_run)
    interval_hours = (
        max(0.0, (current_time - previous_time).total_seconds() / 3600.0)
        if current_time and previous_time
        else 0.0
    )

    current_rows = _relevant_snapshot_rows(
        db.query(ListingSnapshot).filter(ListingSnapshot.run_id == current_run.id).all(),
        keyword_text,
    )
    previous_rows = _relevant_snapshot_rows(
        db.query(ListingSnapshot).filter(ListingSnapshot.run_id == previous_run.id).all(),
        keyword_text,
    )
    previous_map = {(row.platform, row.item_id): row for row in previous_rows}

    matched_rows: list[dict[str, Any]] = []
    platform_current_counts: dict[str, int] = {}
    platform_rows: dict[str, list[dict[str, Any]]] = {}
    for current in current_rows:
        platform_current_counts[current.platform] = platform_current_counts.get(current.platform, 0) + 1
        previous = previous_map.get((current.platform, current.item_id))
        if not previous:
            continue
        sold_delta = _safe_delta(current.sold_count, previous.sold_count)
        review_delta = _safe_delta(current.review_count, previous.review_count)
        price_change = _price_change_pct(current.price, previous.price)
        rank_change = (
            previous.search_rank - current.search_rank
            if previous.search_rank is not None and current.search_rank is not None
            else None
        )
        item = {
            "platform": current.platform,
            "item_id": current.item_id,
            "title": current.title,
            "sold_delta": sold_delta,
            "review_delta": review_delta,
            "price_change_pct": price_change,
            "rank_change": rank_change,
        }
        matched_rows.append(item)
        platform_rows.setdefault(current.platform, []).append(item)

    platforms = {
        platform: _summarize_rows(
            platform_rows.get(platform, []),
            platform_current_counts.get(platform, 0),
            interval_hours,
        )
        for platform in sorted(platform_current_counts)
    }
    overall = _summarize_rows(matched_rows, len(current_rows), interval_hours)

    recommendations: list[str] = []
    reliability = float(overall.get("reliability") or 0)
    activity_share = overall.get("activity_share")
    price_volatility = overall.get("median_abs_price_change_pct")
    if interval_hours < MIN_INTERVAL_HOURS:
        recommendations.append("两次快照间隔少于 6 小时，销量/评论增量容易受展示刷新噪声影响，暂不用于强判断。")
    elif reliability < 50:
        recommendations.append("历史匹配样本偏少，近期动量只能作为辅助证据。")
    elif activity_share is not None and activity_share >= 60:
        recommendations.append(f"近期有增长的匹配商品占 {activity_share:.1f}%，需求信号更像当前活跃，而不只是历史累计销量。")
    elif activity_share is not None and activity_share <= 20:
        recommendations.append(f"近期有增长的匹配商品仅 {activity_share:.1f}%，累计已售可能主要来自历史沉淀，建议谨慎解读需求分。")
    if price_volatility is not None and price_volatility >= 20:
        recommendations.append(f"匹配商品中位绝对价格变动约 {price_volatility:.1f}%，价格带近期波动较大，利润测算应留出促销波动空间。")

    status = "usable" if interval_hours >= MIN_INTERVAL_HOURS and reliability >= 50 else "weak"
    return {
        "status": status,
        "message": "近期动量只对比主评分相关性门槛内、同平台同 item_id 的两次稳定快照。",
        "current_run_id": current_run.id,
        "previous_run_id": previous_run.id,
        "interval_hours": round(interval_hours, 2),
        "overall": overall,
        "platforms": platforms,
        "recommendations": recommendations,
    }
