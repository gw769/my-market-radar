from __future__ import annotations

import math
import re
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


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[\w]+", value.lower(), flags=re.UNICODE) if token]


def relevance_score(keyword: str, title: str) -> float:
    keyword = " ".join(keyword.lower().split())
    title = " ".join(title.lower().split())
    if not keyword or not title:
        return 0.0
    if keyword in title:
        return 1.0
    query_tokens = list(dict.fromkeys(_tokens(keyword)))
    title_tokens = set(_tokens(title))
    if not query_tokens:
        return 0.0
    if len(query_tokens) == 1:
        token = query_tokens[0]
        return 1.0 if token in title_tokens or token in title else 0.0
    overlap = sum(token in title_tokens for token in query_tokens) / len(query_tokens)
    compact_query = "".join(query_tokens)
    compact_title = "".join(_tokens(title))
    if compact_query and compact_query in compact_title:
        return 1.0
    return round(overlap, 4)


def _relevant_items(items: list[dict[str, Any]], keyword: str | None) -> tuple[list[dict[str, Any]], int]:
    if not keyword:
        return items, 0
    kept = [item for item in items if relevance_score(keyword, str(item.get("title") or "")) >= 0.6]
    return kept, max(0, len(items) - len(kept))


def _coverage(items: list[dict[str, Any]], field: str) -> float:
    if not items:
        return 0.0
    return sum(item.get(field) is not None for item in items) / len(items)


def _weighted_with_neutral(parts: list[tuple[float | None, float]]) -> tuple[float | None, float]:
    available = [(value, weight) for value, weight in parts if value is not None]
    if not available:
        return None, 0.0
    known_weight = sum(weight for _, weight in available)
    raw = sum(value * weight for value, weight in available) / known_weight
    return _clamp(50 + (raw - 50) * known_weight), known_weight


def _price_room(prices: list[float]) -> tuple[float | None, float | None]:
    if len(prices) < 4:
        return None, None
    ordered = sorted(prices)
    lower = ordered[: len(ordered) // 2]
    upper = ordered[(len(ordered) + 1) // 2 :]
    if not lower or not upper:
        return None, None
    q1 = statistics.median(lower)
    q3 = statistics.median(upper)
    median_price = statistics.median(ordered)
    if median_price <= 0:
        return None, None
    dispersion = (q3 - q1) / median_price
    if dispersion <= 0.05:
        score = 20 + dispersion / 0.05 * 15
    elif dispersion <= 0.35:
        score = 35 + (dispersion - 0.05) / 0.30 * 65
    else:
        score = 100 - min(65, (dispersion - 0.35) / 0.65 * 65)
    return _clamp(score), dispersion


def _verdict(score: float | None, eligible: bool = True) -> str:
    if score is None or not eligible:
        return "数据不足"
    if score >= 70:
        return "建议尝试"
    if score >= 45:
        return "谨慎观察"
    return "暂不建议"


def score_platform(items: list[dict[str, Any]], keyword: str | None = None) -> dict[str, Any]:
    raw_sample_size = len(items)
    items, excluded_irrelevant = _relevant_items(items, keyword)
    sample_size = len(items)
    prices = [float(x["price"]) for x in items if x.get("price") is not None and float(x["price"]) > 0]
    sold = [float(x["sold_count"]) for x in items if x.get("sold_count") is not None]
    reviews = [float(x["review_count"]) for x in items if x.get("review_count") is not None]
    ratings = [float(x["rating"]) for x in items if x.get("rating") is not None]
    known_ads = [bool(x["is_sponsored"]) for x in items if x.get("is_sponsored") is not None]

    coverage = {
        "price": _coverage(items, "price"),
        "sold_count": _coverage(items, "sold_count"),
        "rating": _coverage(items, "rating"),
        "review_count": _coverage(items, "review_count"),
        "is_sponsored": _coverage(items, "is_sponsored"),
    }

    median_sold = _median(sold)
    median_reviews = _median(reviews)
    sold_signal = _log_score(median_sold, 1_000) if coverage["sold_count"] >= 0.25 else None
    review_signal = _log_score(median_reviews, 500) if coverage["review_count"] >= 0.25 else None
    demand, demand_reliability = _weighted_with_neutral([(sold_signal, 0.7), (review_signal, 0.3)])

    review_barrier = _log_score(median_reviews, 500) if coverage["review_count"] >= 0.25 else None
    incumbent_known = [x for x in items if x.get("rating") is not None and x.get("review_count") is not None]
    incumbent_coverage = len(incumbent_known) / sample_size if sample_size else 0
    strong_incumbent_share = (
        sum(1 for x in incumbent_known if float(x["rating"]) >= 4.8 and float(x["review_count"]) >= 100) / len(incumbent_known) * 100
        if incumbent_known and incumbent_coverage >= 0.25 else None
    )
    sponsored_share = sum(known_ads) / len(known_ads) * 100 if known_ads and coverage["is_sponsored"] >= 0.5 else None
    barrier, barrier_reliability = _weighted_with_neutral([(review_barrier, 0.5), (strong_incumbent_share, 0.3), (sponsored_share, 0.2)])
    entry_ease = 100 - barrier if barrier is not None else None

    minimum_prices = max(4, math.ceil(sample_size * 0.4))
    price_room, price_dispersion = _price_room(prices) if len(prices) >= minimum_prices else (None, None)
    price_reliability = min(1.0, coverage["price"] / 0.8) if price_room is not None else 0.0

    dimensions = {"demand": demand, "entry_ease": entry_ease, "price_room": price_room}
    score, dimension_weight = _weighted_with_neutral([(demand, 0.4), (entry_ease, 0.35), (price_room, 0.25)])
    evidence_reliability = 0.4 * demand_reliability + 0.35 * barrier_reliability + 0.25 * price_reliability

    field_coverage = statistics.mean(coverage.values()) if coverage else 0.0
    sample_coverage = min(1.0, sample_size / 20)
    relevance_coverage = sample_size / raw_sample_size if raw_sample_size else 0.0
    confidence = round((field_coverage * 0.45 + sample_coverage * 0.25 + relevance_coverage * 0.15 + evidence_reliability * 0.15) * 100, 1)

    eligibility_reasons: list[str] = []
    if sample_size < 10:
        eligibility_reasons.append("相关商品样本少于 10 条")
    if coverage["price"] < 0.5:
        eligibility_reasons.append("价格字段覆盖不足 50%")
    if demand_reliability < 0.5:
        eligibility_reasons.append("销量/评论需求证据不足")
    if evidence_reliability < 0.55:
        eligibility_reasons.append("评分核心证据覆盖不足")
    if confidence < 55:
        eligibility_reasons.append("总体数据完整度低于 55%")
    eligible = not eligibility_reasons and score is not None and dimension_weight >= 0.65

    return {
        "score": round(score, 1) if score is not None else None,
        "verdict": _verdict(score, eligible),
        "eligible": eligible,
        "confidence": confidence,
        "sample_size": sample_size,
        "raw_sample_size": raw_sample_size,
        "excluded_irrelevant": excluded_irrelevant,
        "eligibility_reasons": eligibility_reasons,
        "coverage": {key: round(value * 100, 1) for key, value in coverage.items()},
        "dimensions": {key: round(value, 1) if value is not None else None for key, value in dimensions.items()},
        "metrics": {
            "median_price": round(_median(prices), 2) if prices else None,
            "min_price": round(min(prices), 2) if prices else None,
            "max_price": round(max(prices), 2) if prices else None,
            "price_dispersion": round(price_dispersion, 3) if price_dispersion is not None else None,
            "median_sold": int(median_sold) if median_sold is not None else None,
            "median_reviews": int(median_reviews) if median_reviews is not None else None,
            "average_rating": round(_mean(ratings), 2) if ratings else None,
            "sponsored_share": round(sponsored_share, 1) if sponsored_share is not None else None,
        },
    }


def build_analysis(keyword: str, by_platform: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    platform_scores = {platform: score_platform(items, keyword=keyword) for platform, items in by_platform.items()}
    requested = list(platform_scores.values())
    eligible = [result for result in requested if result["eligible"]]
    all_eligible = bool(requested) and len(eligible) == len(requested)
    combined_score = round(statistics.mean(x["score"] for x in eligible), 1) if all_eligible else None
    confidence = round(statistics.mean(x["confidence"] for x in requested), 1) if requested else 0
    verdict = _verdict(combined_score, all_eligible)

    recommendations: list[str] = []
    if not all_eligible:
        recommendations.append("当前证据不足，不生成强选品结论；先补齐下面缺失的数据再判断。")
        for platform, result in platform_scores.items():
            if not result["eligible"]:
                reason = "；".join(result["eligibility_reasons"][:3]) or "数据不足"
                recommendations.append(f"{platform.title()}：{reason}。")
    else:
        recommendations.append(f"“{keyword}”当前公开数据机会分为 {combined_score}，结论：{verdict}。")
        medians = [x["metrics"]["median_price"] for x in eligible if x["metrics"]["median_price"] is not None]
        if medians:
            recommendations.append(f"相关商品公开中位价约 RM {statistics.mean(medians):.2f}；优先围绕该价格带验证差异化卖点，而不是直接按最低价竞争。")
        if any((x["metrics"]["median_reviews"] or 0) >= 200 for x in eligible):
            recommendations.append("头部商品评论门槛较高，新商品需要更明确的卖点与首批评价策略。")
        if any((x["metrics"]["sponsored_share"] or 0) >= 35 for x in eligible):
            recommendations.append("搜索结果广告占比较高，进入时应把站内推广成本纳入验证预算。")
        if any((x["metrics"]["price_dispersion"] or 0) > 0.6 for x in eligible):
            recommendations.append("价格分布非常分散，可能混有规格/套装差异；下单前应进一步拆分子品类验证。")

    excluded = sum(x["excluded_irrelevant"] for x in requested)
    if excluded:
        recommendations.append(f"已从机会评分中排除 {excluded} 条与关键词相关性偏低的搜索结果。")

    return {
        "keyword": keyword,
        "opportunity_score": combined_score,
        "verdict": verdict,
        "confidence": confidence,
        "platform_scores": platform_scores,
        "recommendations": recommendations,
        "methodology": "公开数据启发式评分：需求信号 40%、进入门槛 35%、价格空间 25%。缺失证据不会被剩余字段放大，而会向中性分收缩；相关样本、字段覆盖和完整度不足时不输出强结论。",
    }
