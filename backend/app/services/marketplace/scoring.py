from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from typing import Any


ACCESSORY_TERMS = {
    "accessory", "accessories", "replacement", "spare", "part", "parts",
    "cover", "sleeve", "holder", "pouch", "bag", "strap", "cap", "lid",
    "brush", "cleaner", "cleaning", "sticker", "decal", "stand", "rack",
    "carrier", "protector", "shell", "case",
    "sarung", "penutup", "pemegang", "berus", "tali", "ganti",
}
BUNDLE_TERMS = {"bundle", "combo", "set", "pack", "pcs", "piece", "pieces"}
INCLUSION_CONNECTORS = {"with", "dengan", "including", "include", "includes", "plus"}

# Common marketplace filler words are poor product-family labels. The list is deliberately
# conservative; unknown tokens are still allowed to form a segment when repeated often.
SEGMENT_STOPWORDS = {
    "new", "original", "ready", "stock", "sale", "hot", "best", "quality", "premium",
    "free", "shipping", "murah", "terbaik", "baru", "malaysia", "my", "for", "with",
    "and", "the", "of", "in", "to", "from", "oleh", "untuk", "dengan", "dan",
    "warna", "colour", "color", "random", "official", "authentic", "brand",
}
SEGMENT_ATTRIBUTE_TERMS = {
    "stainless", "steel", "glass", "plastic", "tritan", "silicone", "ceramic", "wood",
    "thermal", "insulated", "vacuum", "electric", "manual", "wireless", "rechargeable",
    "portable", "foldable", "mini", "compact", "large", "small", "lightweight",
    "kids", "kid", "children", "child", "baby", "toddler", "adult", "men", "women",
    "sports", "sport", "gym", "outdoor", "hiking", "travel", "office", "home",
    "straw", "handle", "waterproof", "organic", "natural", "digital", "smart",
}


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


def _is_ascii_word(value: str) -> bool:
    return bool(value) and all(ch.isascii() and (ch.isalnum() or ch == "_") for ch in value)


def _normalized_text(value: str) -> str:
    return " ".join(_tokens(value))


def _query_extra_terms(keyword: str, title: str, candidates: set[str]) -> set[str]:
    query_tokens = set(_tokens(keyword))
    title_tokens = set(_tokens(title))
    return {term for term in candidates if term in title_tokens and term not in query_tokens}


def _looks_like_accessory(keyword: str, title: str) -> bool:
    query = _normalized_text(keyword)
    normalized = _normalized_text(title)
    if not query or not normalized:
        return False

    extra_accessories = _query_extra_terms(keyword, title, ACCESSORY_TERMS)
    if not extra_accessories:
        return False

    title_tokens = _tokens(title)
    query_tokens = _tokens(keyword)
    if not title_tokens or not query_tokens:
        return False

    if f" for {query}" in f" {normalized}" or f" untuk {query}" in f" {normalized}":
        return True

    first_window = set(title_tokens[:4])
    if first_window & extra_accessories:
        if query in normalized or all(token in title_tokens for token in query_tokens):
            if not first_window & INCLUSION_CONNECTORS:
                return True

    phrase_index = normalized.find(query)
    if phrase_index >= 0:
        before = _tokens(normalized[:phrase_index])
        after = _tokens(normalized[phrase_index + len(query):])
        for index, token in enumerate(after[:4]):
            if token not in extra_accessories:
                continue
            previous = after[index - 1] if index > 0 else None
            if previous in INCLUSION_CONNECTORS:
                continue
            return True
        if before:
            last_prefix = set(before[-3:])
            if last_prefix & extra_accessories and not last_prefix & INCLUSION_CONNECTORS:
                return True

    return False


def _looks_like_bundle(keyword: str, title: str) -> bool:
    query_tokens = set(_tokens(keyword))
    if query_tokens & BUNDLE_TERMS:
        return False

    normalized = _normalized_text(title)
    if not normalized:
        return False

    if re.search(r"\b(?:bundle|combo)\b", normalized):
        return True
    if re.search(r"\b\d+\s*(?:pcs|pieces|pack)\b", normalized):
        return True
    if re.search(r"\b(?:set|pack)\s+of\s+\d+\b", normalized):
        return True
    if re.search(r"\b\d+\s*x\s+", normalized):
        return True
    return False


def relevance_score(keyword: str, title: str) -> float:
    keyword = " ".join(keyword.lower().split())
    title = " ".join(title.lower().split())
    if not keyword or not title:
        return 0.0

    if _looks_like_accessory(keyword, title):
        return 0.2
    if _looks_like_bundle(keyword, title):
        return 0.55
    if keyword in title:
        return 1.0

    query_tokens = list(dict.fromkeys(_tokens(keyword)))
    title_tokens = set(_tokens(title))
    if not query_tokens:
        return 0.0

    if len(query_tokens) == 1:
        token = query_tokens[0]
        if _is_ascii_word(token):
            return 1.0 if token in title_tokens else 0.0
        return 1.0 if token in title else 0.0

    overlap = sum(token in title_tokens for token in query_tokens) / len(query_tokens)
    compact_query = "".join(query_tokens)
    compact_title = "".join(_tokens(title))
    if compact_query and compact_query in compact_title:
        return 1.0
    return round(overlap, 4)


def _exclusion_reason(keyword: str, title: str) -> str | None:
    if _looks_like_accessory(keyword, title):
        return "accessory"
    if _looks_like_bundle(keyword, title):
        return "bundle"
    return None if relevance_score(keyword, title) >= 0.6 else "low_relevance"


def _relevant_items(
    items: list[dict[str, Any]], keyword: str | None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not keyword:
        return items, {"accessory": 0, "bundle": 0, "low_relevance": 0}
    kept: list[dict[str, Any]] = []
    excluded = {"accessory": 0, "bundle": 0, "low_relevance": 0}
    for item in items:
        title = str(item.get("title") or "")
        reason = _exclusion_reason(keyword, title)
        if reason:
            excluded[reason] += 1
        else:
            kept.append(item)
    return kept, excluded


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


def _seller_identity(item: dict[str, Any]) -> str | None:
    shop_id = str(item.get("shop_id") or "").strip()
    if shop_id:
        return f"id:{shop_id}"
    seller = " ".join(str(item.get("seller_name") or "").lower().split())
    if seller and seller not in {"unknown", "n/a", "na", "-", "—"}:
        return f"name:{seller}"
    return None


def _normalized_hhi(weights: list[float]) -> float | None:
    positive = [max(0.0, float(value)) for value in weights]
    total = sum(positive)
    if not positive or total <= 0:
        return None
    if len(positive) == 1:
        return 100.0
    shares = [value / total for value in positive]
    hhi = sum(share * share for share in shares)
    equal_share_hhi = 1.0 / len(shares)
    if equal_share_hhi >= 1:
        return 100.0
    return _clamp((hhi - equal_share_hhi) / (1 - equal_share_hhi) * 100)


def _seller_structure(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            "coverage": 0.0,
            "seller_count": None,
            "top5_listing_share": None,
            "top5_demand_share": None,
            "listing_hhi": None,
            "demand_hhi": None,
            "concentration_pressure": None,
            "reliability": 0.0,
        }

    identified = [(identity, item) for item in items if (identity := _seller_identity(item))]
    coverage = len(identified) / len(items)
    listing_counts = Counter(identity for identity, _ in identified)
    seller_count = len(listing_counts)
    if coverage < 0.5 or len(identified) < 5:
        return {
            "coverage": coverage,
            "seller_count": seller_count or None,
            "top5_listing_share": None,
            "top5_demand_share": None,
            "listing_hhi": None,
            "demand_hhi": None,
            "concentration_pressure": None,
            "reliability": min(1.0, coverage / 0.8),
        }

    top5_listing_share = sum(count for _, count in listing_counts.most_common(5)) / len(identified) * 100
    listing_hhi = _normalized_hhi([float(count) for count in listing_counts.values()])

    sold_rows = [
        (identity, float(item["sold_count"]))
        for identity, item in identified
        if item.get("sold_count") is not None and float(item["sold_count"]) >= 0
    ]
    sold_coverage = len(sold_rows) / len(identified)
    top5_demand_share = None
    demand_hhi = None
    if sold_coverage >= 0.5:
        sold_by_seller: dict[str, float] = defaultdict(float)
        for identity, sold_count in sold_rows:
            sold_by_seller[identity] += sold_count
        total_sold = sum(sold_by_seller.values())
        if total_sold > 0:
            top5_demand_share = sum(sorted(sold_by_seller.values(), reverse=True)[:5]) / total_sold * 100
            demand_hhi = _normalized_hhi(list(sold_by_seller.values()))

    # Top-5 shares are useful descriptive metrics, but they have a large mechanical floor
    # in small samples (five distinct sellers imply Top5=100%). Normalized HHI removes that
    # floor: equal-sized sellers score 0 regardless of seller count; monopoly scores 100.
    concentration_pressure = demand_hhi if demand_hhi is not None else listing_hhi
    return {
        "coverage": coverage,
        "seller_count": seller_count,
        "top5_listing_share": top5_listing_share,
        "top5_demand_share": top5_demand_share,
        "listing_hhi": listing_hhi,
        "demand_hhi": demand_hhi,
        "concentration_pressure": concentration_pressure,
        "reliability": min(1.0, coverage / 0.8),
    }


def _verdict(score: float | None, eligible: bool = True) -> str:
    if score is None or not eligible:
        return "数据不足"
    if score >= 70:
        return "建议尝试"
    if score >= 45:
        return "谨慎观察"
    return "暂不建议"


def score_platform(
    items: list[dict[str, Any]],
    keyword: str | None = None,
    min_sample_size: int = 10,
) -> dict[str, Any]:
    raw_sample_size = len(items)
    items, exclusion_breakdown = _relevant_items(items, keyword)
    excluded_irrelevant = sum(exclusion_breakdown.values())
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
        sum(1 for x in incumbent_known if float(x["rating"]) >= 4.8 and float(x["review_count"]) >= 100)
        / len(incumbent_known)
        * 100
        if incumbent_known and incumbent_coverage >= 0.25
        else None
    )
    sponsored_share = (
        sum(known_ads) / len(known_ads) * 100
        if known_ads and coverage["is_sponsored"] >= 0.5
        else None
    )

    seller = _seller_structure(items)
    seller_pressure = seller["concentration_pressure"]
    barrier, barrier_reliability = _weighted_with_neutral(
        [
            (review_barrier, 0.35),
            (strong_incumbent_share, 0.20),
            (sponsored_share, 0.15),
            (seller_pressure, 0.30),
        ]
    )
    entry_ease = 100 - barrier if barrier is not None else None

    minimum_prices = max(4, math.ceil(sample_size * 0.4))
    price_room, price_dispersion = _price_room(prices) if len(prices) >= minimum_prices else (None, None)
    price_reliability = min(1.0, coverage["price"] / 0.8) if price_room is not None else 0.0

    dimensions = {"demand": demand, "entry_ease": entry_ease, "price_room": price_room}
    score, dimension_weight = _weighted_with_neutral(
        [(demand, 0.4), (entry_ease, 0.35), (price_room, 0.25)]
    )
    evidence_reliability = (
        0.4 * demand_reliability + 0.35 * barrier_reliability + 0.25 * price_reliability
    )

    field_coverage = statistics.mean(coverage.values()) if coverage else 0.0
    sample_coverage = min(1.0, sample_size / max(20, min_sample_size))
    relevance_coverage = sample_size / raw_sample_size if raw_sample_size else 0.0
    confidence = round(
        (
            field_coverage * 0.45
            + sample_coverage * 0.25
            + relevance_coverage * 0.15
            + evidence_reliability * 0.15
        )
        * 100,
        1,
    )

    eligibility_reasons: list[str] = []
    if sample_size < min_sample_size:
        eligibility_reasons.append(f"相关商品样本少于 {min_sample_size} 条")
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
        "exclusion_breakdown": exclusion_breakdown,
        "eligibility_reasons": eligibility_reasons,
        "coverage": {key: round(value * 100, 1) for key, value in coverage.items()},
        "dimensions": {
            key: round(value, 1) if value is not None else None
            for key, value in dimensions.items()
        },
        "metrics": {
            "median_price": round(_median(prices), 2) if prices else None,
            "min_price": round(min(prices), 2) if prices else None,
            "max_price": round(max(prices), 2) if prices else None,
            "price_dispersion": round(price_dispersion, 3) if price_dispersion is not None else None,
            "median_sold": int(median_sold) if median_sold is not None else None,
            "median_reviews": int(median_reviews) if median_reviews is not None else None,
            "average_rating": round(_mean(ratings), 2) if ratings else None,
            "sponsored_share": round(sponsored_share, 1) if sponsored_share is not None else None,
            "seller_identity_coverage": round(seller["coverage"] * 100, 1),
            "seller_count": seller["seller_count"],
            "top5_seller_listing_share": (
                round(seller["top5_listing_share"], 1)
                if seller["top5_listing_share"] is not None else None
            ),
            "top5_seller_demand_share": (
                round(seller["top5_demand_share"], 1)
                if seller["top5_demand_share"] is not None else None
            ),
            "seller_listing_hhi": (
                round(seller["listing_hhi"], 1) if seller["listing_hhi"] is not None else None
            ),
            "seller_demand_hhi": (
                round(seller["demand_hhi"], 1) if seller["demand_hhi"] is not None else None
            ),
            "seller_concentration": (
                round(seller_pressure, 1) if seller_pressure is not None else None
            ),
        },
    }


def _segment_feature_tokens(title: str, keyword: str) -> set[str]:
    query_tokens = set(_tokens(keyword))
    features: set[str] = set()
    for token in _tokens(title):
        if token in query_tokens or token in SEGMENT_STOPWORDS:
            continue
        if token in ACCESSORY_TERMS or token in BUNDLE_TERMS:
            continue
        if len(token) <= 2 and _is_ascii_word(token):
            continue
        if re.fullmatch(r"\d+(?:ml|l|oz|cm|mm|kg|g|w|v)?", token):
            continue
        features.add(token)
    return features


def _segment_candidates(keyword: str, items: list[dict[str, Any]]) -> list[str]:
    if len(items) < 6:
        return []
    document_frequency: Counter[str] = Counter()
    for item in items:
        document_frequency.update(_segment_feature_tokens(str(item.get("title") or ""), keyword))

    minimum = max(2, math.ceil(len(items) * 0.12))
    maximum = max(minimum, math.floor(len(items) * 0.75))
    candidates = [
        token for token, count in document_frequency.items()
        if minimum <= count <= maximum
    ]
    candidates.sort(
        key=lambda token: (
            token in SEGMENT_ATTRIBUTE_TERMS,
            document_frequency[token],
            len(token),
        ),
        reverse=True,
    )
    return candidates[:8]


def build_opportunity_segments(
    keyword: str,
    by_platform: dict[str, list[dict[str, Any]]],
    min_segment_size: int = 4,
) -> list[dict[str, Any]]:
    relevant_by_platform: dict[str, list[dict[str, Any]]] = {}
    combined: list[dict[str, Any]] = []
    for platform, items in by_platform.items():
        relevant, _ = _relevant_items(items, keyword)
        relevant_by_platform[platform] = relevant
        combined.extend(relevant)

    candidates = _segment_candidates(keyword, combined)
    if not candidates:
        return []

    assigned: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    representatives: dict[str, list[str]] = defaultdict(list)
    for platform, items in relevant_by_platform.items():
        for item in items:
            features = _segment_feature_tokens(str(item.get("title") or ""), keyword)
            label = next((candidate for candidate in candidates if candidate in features), "core")
            assigned[label][platform].append(item)
            if len(representatives[label]) < 2:
                representatives[label].append(str(item.get("title") or "")[:140])

    segments: list[dict[str, Any]] = []
    platform_total = max(1, len(by_platform))
    total_relevant = max(1, len(combined))
    for label, platform_items in assigned.items():
        sample_size = sum(len(items) for items in platform_items.values())
        if sample_size < min_segment_size:
            continue

        platform_scores: dict[str, dict[str, Any]] = {}
        eligible_scores: list[dict[str, Any]] = []
        for platform, items in platform_items.items():
            if len(items) < min_segment_size:
                continue
            result = score_platform(items, keyword=None, min_sample_size=min_segment_size)
            platform_scores[platform] = result
            if result["eligible"]:
                eligible_scores.append(result)
        if not eligible_scores:
            continue

        platform_coverage = len(eligible_scores) / platform_total
        mean_score = statistics.mean(float(result["score"]) for result in eligible_scores)
        # A segment observed on only one selected platform remains useful, but is ranked a
        # little below equally strong segments corroborated across platforms.
        ranked_score = _clamp(mean_score * (0.90 + 0.10 * platform_coverage))
        confidence = statistics.mean(float(result["confidence"]) for result in eligible_scores)
        median_prices = [
            result["metrics"]["median_price"]
            for result in eligible_scores
            if result["metrics"]["median_price"] is not None
        ]
        seller_pressures = [
            result["metrics"]["seller_concentration"]
            for result in eligible_scores
            if result["metrics"]["seller_concentration"] is not None
        ]

        segments.append({
            "label": "核心商品" if label == "core" else label,
            "token": None if label == "core" else label,
            "opportunity_score": round(ranked_score, 1),
            "verdict": _verdict(ranked_score, True),
            "confidence": round(confidence, 1),
            "sample_size": sample_size,
            "share": round(sample_size / total_relevant * 100, 1),
            "platform_coverage": round(platform_coverage * 100, 1),
            "median_price": round(statistics.mean(median_prices), 2) if median_prices else None,
            "seller_concentration": (
                round(statistics.mean(seller_pressures), 1) if seller_pressures else None
            ),
            "representative_titles": representatives[label],
            "platform_scores": platform_scores,
        })

    segments.sort(
        key=lambda item: (item["opportunity_score"], item["confidence"], item["sample_size"]),
        reverse=True,
    )
    return segments[:5]


def build_analysis(keyword: str, by_platform: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    platform_scores = {
        platform: score_platform(items, keyword=keyword)
        for platform, items in by_platform.items()
    }
    requested = list(platform_scores.values())
    eligible = [result for result in requested if result["eligible"]]
    all_eligible = bool(requested) and len(eligible) == len(requested)
    combined_score = (
        round(statistics.mean(x["score"] for x in eligible), 1)
        if all_eligible
        else None
    )
    confidence = (
        round(statistics.mean(x["confidence"] for x in requested), 1)
        if requested
        else 0
    )
    verdict = _verdict(combined_score, all_eligible)
    opportunity_segments = build_opportunity_segments(keyword, by_platform)

    recommendations: list[str] = []
    if not all_eligible:
        recommendations.append("当前证据不足，不生成强选品结论；先补齐下面缺失的数据再判断。")
        for platform, result in platform_scores.items():
            if not result["eligible"]:
                reason = "；".join(result["eligibility_reasons"][:3]) or "数据不足"
                recommendations.append(f"{platform.title()}：{reason}。")
    else:
        recommendations.append(
            f"“{keyword}”当前公开数据机会分为 {combined_score}，结论：{verdict}。"
        )
        medians = [
            x["metrics"]["median_price"]
            for x in eligible
            if x["metrics"]["median_price"] is not None
        ]
        if medians:
            recommendations.append(
                f"相关商品公开中位价约 RM {statistics.mean(medians):.2f}；"
                "优先围绕该价格带验证差异化卖点，而不是直接按最低价竞争。"
            )
        if any((x["metrics"]["median_reviews"] or 0) >= 200 for x in eligible):
            recommendations.append("头部商品评论门槛较高，新商品需要更明确的卖点与首批评价策略。")
        if any((x["metrics"]["sponsored_share"] or 0) >= 35 for x in eligible):
            recommendations.append("搜索结果广告占比较高，进入时应把站内推广成本纳入验证预算。")
        if any((x["metrics"]["seller_concentration"] or 0) >= 65 for x in eligible):
            recommendations.append("可识别卖家中的头部集中度较高，需求可能被少数强店铺占据；不要只看销量高就判断容易进入。")
        if any((x["metrics"]["price_dispersion"] or 0) > 0.6 for x in eligible):
            recommendations.append("价格分布非常分散，可能混有规格/子品类差异；优先参考下面的商品族机会排序。")

    if opportunity_segments:
        top = opportunity_segments[0]
        recommendations.append(
            f"自动拆分的商品族中，当前排序最高的是“{top['label']}”"
            f"（机会分 {top['opportunity_score']}，样本 {top['sample_size']} 条，"
            f"平台证据覆盖 {top['platform_coverage']}%）；建议先围绕这一子类做供应链和利润核算。"
        )

    excluded_accessories = sum(
        x.get("exclusion_breakdown", {}).get("accessory", 0) for x in requested
    )
    excluded_bundles = sum(
        x.get("exclusion_breakdown", {}).get("bundle", 0) for x in requested
    )
    excluded_low_relevance = sum(
        x.get("exclusion_breakdown", {}).get("low_relevance", 0) for x in requested
    )
    excluded = excluded_accessories + excluded_bundles + excluded_low_relevance
    if excluded:
        detail: list[str] = []
        if excluded_accessories:
            detail.append(f"{excluded_accessories} 条配件/替换件")
        if excluded_bundles:
            detail.append(f"{excluded_bundles} 条套装/多件装")
        if excluded_low_relevance:
            detail.append(f"{excluded_low_relevance} 条低相关结果")
        recommendations.append(f"已从机会评分样本中排除 {'、'.join(detail)}，避免污染价格与竞争度。")

    return {
        "keyword": keyword,
        "opportunity_score": combined_score,
        "verdict": verdict,
        "confidence": confidence,
        "platform_scores": platform_scores,
        "opportunity_segments": opportunity_segments,
        "recommendations": recommendations,
        "methodology": (
            "公开数据启发式评分：需求信号 40%、进入门槛 35%、价格空间 25%。"
            "进入门槛同时考虑评论门槛、强势商品、广告压力和可识别卖家集中度；"
            "卖家集中度使用归一化 HHI，避免小样本 Top5 占比天然偏高造成误判；"
            "缺失证据不会被剩余字段放大，而会向中性分收缩。"
            "配件/替换件、明显多件装和低相关搜索漂移会从评分样本中排除；"
            "商品族排序来自重复标题属性的轻量聚类，用于缩小验证范围，不等同于平台官方类目。"
        ),
    }
