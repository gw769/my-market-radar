from __future__ import annotations

import math
import statistics
from typing import Any


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


def _log_score(value: float | None, full_at: float) -> float | None:
    if value is None:
        return None
    return _clamp(math.log1p(max(0, value)) / math.log1p(full_at) * 100)


def _verdict(score: float | None) -> str:
    if score is None:
        return "数据不足"
    if score >= 70:
        return "建议尝试"
    if score >= 45:
        return "谨慎观察"
    return "暂不建议"


def score_platform(items: list[dict[str, Any]]) -> dict[str, Any]:
    prices = [float(x["price"]) for x in items if x.get("price") is not None and float(x["price"]) > 0]
    sold = [float(x["sold_count"]) for x in items if x.get("sold_count") is not None]
    reviews = [float(x["review_count"]) for x in items if x.get("review_count") is not None]
    ratings = [float(x["rating"]) for x in items if x.get("rating") is not None]
    known_ads = [bool(x["is_sponsored"]) for x in items if x.get("is_sponsored") is not None]

    median_sold = _median(sold)
    median_reviews = _median(reviews)
    sold_signal = _log_score(median_sold, 1_000)
    review_signal = _log_score(median_reviews, 500)
    demand_parts = [(sold_signal, 0.7), (review_signal, 0.3)]
    demand_available = [(v, w) for v, w in demand_parts if v is not None]
    demand = sum(v * w for v, w in demand_available) / sum(w for _, w in demand_available) if demand_available else None

    review_barrier = _log_score(median_reviews, 500)
    incumbent_known = [
        x for x in items
        if x.get("rating") is not None and x.get("review_count") is not None
    ]
    strong_incumbent_share = (
        sum(1 for x in incumbent_known if x["rating"] >= 4.8 and x["review_count"] >= 100)
        / len(incumbent_known) * 100
        if incumbent_known else None
    )
    sponsored_share = sum(known_ads) / len(known_ads) * 100 if known_ads else None
    barrier_parts = [(review_barrier, 0.5), (strong_incumbent_share, 0.3), (sponsored_share, 0.2)]
    barrier_available = [(v, w) for v, w in barrier_parts if v is not None]
    barrier = sum(v * w for v, w in barrier_available) / sum(w for _, w in barrier_available) if barrier_available else None
    entry_ease = 100 - barrier if barrier is not None else None

    price_room = None
    if len(prices) >= 4:
        ordered = sorted(prices)
        q1 = statistics.median(ordered[: len(ordered) // 2])
        q3 = statistics.median(ordered[(len(ordered) + 1) // 2 :])
        median_price = statistics.median(ordered)
        if median_price > 0:
            price_room = _clamp(((q3 - q1) / median_price) / 0.6 * 100)

    dimensions = {"demand": demand, "entry_ease": entry_ease, "price_room": price_room}
    weighted = [(demand, 0.4), (entry_ease, 0.35), (price_room, 0.25)]
    available = [(v, w) for v, w in weighted if v is not None]
    score = sum(v * w for v, w in available) / sum(w for _, w in available) if available else None

    expected_fields = len(items) * 5
    observed_fields = sum(
        int(x.get("price") is not None)
        + int(x.get("sold_count") is not None)
        + int(x.get("rating") is not None)
        + int(x.get("review_count") is not None)
        + int(x.get("is_sponsored") is not None)
        for x in items
    )
    field_coverage = observed_fields / expected_fields if expected_fields else 0
    sample_coverage = min(1.0, len(items) / 20)
    confidence = round((field_coverage * 0.7 + sample_coverage * 0.3) * 100, 1)

    return {
        "score": round(score, 1) if score is not None else None,
        "verdict": _verdict(score),
        "confidence": confidence,
        "sample_size": len(items),
        "dimensions": {k: round(v, 1) if v is not None else None for k, v in dimensions.items()},
        "metrics": {
            "median_price": round(_median(prices), 2) if prices else None,
            "min_price": round(min(prices), 2) if prices else None,
            "max_price": round(max(prices), 2) if prices else None,
            "median_sold": int(median_sold) if median_sold is not None else None,
            "median_reviews": int(median_reviews) if median_reviews is not None else None,
            "average_rating": round(_mean(ratings), 2) if ratings else None,
            "sponsored_share": round(sponsored_share, 1) if sponsored_share is not None else None,
        },
    }


def build_analysis(keyword: str, by_platform: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    platform_scores = {platform: score_platform(items) for platform, items in by_platform.items()}
    eligible = [result for result in platform_scores.values() if result["sample_size"] >= 10 and result["score"] is not None]
    combined_score = round(statistics.mean(x["score"] for x in eligible), 1) if len(eligible) == 2 else None
    confidence = round(statistics.mean(x["confidence"] for x in eligible), 1) if eligible else 0

    verdict = _verdict(combined_score)

    recommendations: list[str] = []
    if combined_score is None:
        recommendations.append("至少需要两个平台各 10 条有效结果，当前不生成综合结论。")
    else:
        recommendations.append(f"“{keyword}”当前公开数据机会分为 {combined_score}，结论：{verdict}。")
        medians = [x["metrics"]["median_price"] for x in eligible if x["metrics"]["median_price"] is not None]
        if medians:
            recommendations.append(f"公开商品中位价约 RM {statistics.mean(medians):.2f}，可围绕该价格带测试差异化定位。")
        if any((x["metrics"]["median_reviews"] or 0) >= 200 for x in eligible):
            recommendations.append("头部商品评论门槛较高，新商品需要更明确的卖点与首批评价策略。")
        if any((x["metrics"]["sponsored_share"] or 0) >= 35 for x in eligible):
            recommendations.append("搜索结果广告占比较高，进入时应预留站内推广预算。")

    return {
        "keyword": keyword,
        "opportunity_score": combined_score,
        "verdict": verdict,
        "confidence": confidence,
        "platform_scores": platform_scores,
        "recommendations": recommendations,
        "methodology": "公开数据启发式评分：需求信号 40%、进入门槛 35%、价格空间 25%；不是利润或真实销量预测。",
    }
