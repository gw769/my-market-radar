from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any


_NUMERIC_FIELDS = (
    "sales_30d",
    "sales_30d_growth_percent",
    "revenue_30d_myr",
    "total_sales_estimate",
    "gmv_estimate_myr",
    "listing_age_days",
    "like_count",
)


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def summarize_shopdora(collected: dict[str, list[Any]]) -> dict[str, Any] | None:
    shopee = list(collected.get("shopee") or [])
    records = []
    for listing in shopee:
        raw_data = getattr(listing, "raw_data", None)
        shopdora = raw_data.get("shopdora") if isinstance(raw_data, dict) else None
        if isinstance(shopdora, dict) and shopdora.get("provider") == "Shopdora":
            records.append(shopdora)
    if not records:
        return None

    coverage = {
        field: round(sum(_number(row.get(field)) is not None for row in records) / len(records) * 100, 1)
        for field in _NUMERIC_FIELDS
    }
    metrics: dict[str, Any] = {}
    for field in _NUMERIC_FIELDS:
        values = [_number(row.get(field)) for row in records]
        known = [value for value in values if value is not None]
        metrics[f"median_{field}"] = round(float(median(known)), 2) if known else None

    seller_types = [str(row.get("seller_type") or "").strip() for row in records]
    known_seller_types = [value for value in seller_types if value]
    local_count = sum(value in {"本土", "local", "Local"} for value in known_seller_types)
    category_counts = Counter(
        str(row.get("category_path") or "").strip()
        for row in records
        if str(row.get("category_path") or "").strip()
    )
    return {
        "provider": "Shopdora",
        "platform": "shopee",
        "status": "available",
        "source": "browser_extension_dom",
        "estimated": True,
        "sample_size": len(records),
        "snapshot_sample_size": len(shopee),
        "coverage": coverage,
        "metrics": metrics,
        "local_seller_share": (
            round(local_count / len(known_seller_types) * 100, 1)
            if known_seller_types
            else None
        ),
        "top_categories": [
            {"category": category, "count": count}
            for category, count in category_counts.most_common(5)
        ],
        "disclaimer": (
            "第三方浏览器插件显示的估算/增强字段；不替代平台公开口径，"
            "不参与确定性机会分，免费套餐的商品覆盖可能不完整。"
        ),
    }
