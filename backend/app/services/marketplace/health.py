from __future__ import annotations

from typing import Any


def _coverage(items: list[Any], field: str) -> float:
    if not items:
        return 0.0
    return sum(getattr(item, field, None) is not None for item in items) / len(items)


def assess_collection_health(
    raw_cards: list[dict[str, Any]],
    listings: list[Any],
    target_limit: int,
) -> dict[str, Any]:
    """Describe whether the collector itself looks healthy, separate from market quality.

    A weak market and a broken parser can both produce few usable rows. This health object keeps
    those two cases distinguishable by tracking raw-card parsing and critical public-field coverage.
    """
    raw_count = len(raw_cards)
    parsed_count = len(listings)
    parse_ratio = parsed_count / raw_count if raw_count else (1.0 if parsed_count else 0.0)
    target_coverage = min(1.0, parsed_count / max(1, target_limit))
    price_coverage = _coverage(listings, "price")
    sold_coverage = _coverage(listings, "sold_count")
    review_coverage = _coverage(listings, "review_count")
    rating_coverage = _coverage(listings, "rating")
    seller_coverage = sum(
        bool(getattr(item, "shop_id", None) or getattr(item, "seller_name", None))
        for item in listings
    ) / parsed_count if parsed_count else 0.0
    demand_coverage = max(sold_coverage, review_coverage)

    # Empty search pages are not automatically parser failures. When cards are present, however,
    # inability to turn them into listings is a strong collector-health signal.
    parse_component = parse_ratio if raw_count else (0.6 if parsed_count == 0 else 1.0)
    score = (
        parse_component * 0.35
        + target_coverage * 0.25
        + price_coverage * 0.20
        + demand_coverage * 0.20
    ) * 100

    warnings: list[str] = []
    if raw_count >= 5 and parse_ratio < 0.5:
        warnings.append("页面卡片存在，但可解析比例低于 50%，可能是页面结构变化")
    if parsed_count and price_coverage < 0.5:
        warnings.append("价格字段覆盖低于 50%")
    if parsed_count and demand_coverage < 0.25:
        warnings.append("销量/评论需求字段覆盖低于 25%")
    if parsed_count < min(6, max(1, target_limit)):
        warnings.append("可解析商品样本偏少")

    if raw_count >= 5 and parse_ratio < 0.35:
        status = "unhealthy"
    elif parsed_count == 0:
        status = "empty"
    elif score < 60:
        status = "degraded"
    else:
        status = "healthy"

    return {
        "status": status,
        "health_score": round(max(0.0, min(100.0, score)), 1),
        "raw_count": raw_count,
        "parsed_count": parsed_count,
        "target_limit": target_limit,
        "parse_ratio": round(parse_ratio * 100, 1),
        "coverage": {
            "price": round(price_coverage * 100, 1),
            "sold_count": round(sold_coverage * 100, 1),
            "review_count": round(review_coverage * 100, 1),
            "rating": round(rating_coverage * 100, 1),
            "seller_identity": round(seller_coverage * 100, 1),
        },
        "warnings": warnings,
    }


def summarize_collector_health(
    platform_health: dict[str, dict[str, Any]],
    requested_platforms: list[str],
) -> dict[str, Any]:
    requested = [platform for platform in requested_platforms if platform in platform_health]
    values = [platform_health[platform] for platform in requested]
    scores = [float(value.get("health_score") or 0) for value in values]
    statuses = {platform: platform_health[platform].get("status", "unknown") for platform in requested}
    unhealthy = [platform for platform, status in statuses.items() if status == "unhealthy"]
    degraded = [platform for platform, status in statuses.items() if status in {"degraded", "empty"}]
    overall = round(sum(scores) / len(scores), 1) if scores else 0.0
    if unhealthy:
        status = "unhealthy"
    elif degraded:
        status = "degraded"
    elif values:
        status = "healthy"
    else:
        status = "unknown"
    return {
        "status": status,
        "health_score": overall,
        "platforms": platform_health,
        "unhealthy_platforms": unhealthy,
        "degraded_platforms": degraded,
    }
