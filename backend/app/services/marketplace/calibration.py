from __future__ import annotations

from typing import Any


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _verdict(score: float | None) -> str:
    if score is None:
        return "数据不足"
    if score >= 70:
        return "建议尝试"
    if score >= 45:
        return "谨慎观察"
    return "暂不建议"


def confidence_weighted_score(results: list[dict[str, Any]]) -> float | None:
    usable = [
        result
        for result in results
        if result.get("eligible") and result.get("score") is not None
    ]
    if not usable:
        return None
    weights = [max(1.0, float(result.get("confidence") or 0)) for result in usable]
    total = sum(weights)
    return sum(float(result["score"]) * weight for result, weight in zip(usable, weights)) / total


def _shrink_to_neutral(score: float, reliability: float) -> float:
    reliability = _clamp(reliability, 0.0, 1.0)
    return 50.0 + (score - 50.0) * reliability


def calibrate_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """Calibrate aggregation without changing the deterministic platform scoring model.

    Platform scores remain reproducible. This layer only prevents low-confidence platforms and
    tiny/single-platform product families from dominating the final ranking.
    """
    platform_scores = analysis.get("platform_scores") or {}
    results = list(platform_scores.values())
    eligible = [result for result in results if result.get("eligible")]
    all_eligible = bool(results) and len(eligible) == len(results)

    if all_eligible:
        weighted = confidence_weighted_score(eligible)
        if weighted is not None:
            analysis["opportunity_score"] = round(weighted, 1)
            analysis["verdict"] = _verdict(weighted)
            analysis["aggregation"] = {
                "method": "confidence_weighted",
                "platform_weights": {
                    platform: round(float(result.get("confidence") or 0), 1)
                    for platform, result in platform_scores.items()
                },
            }

    requested_platforms = max(1, len(platform_scores))
    calibrated_segments: list[dict[str, Any]] = []
    for segment in analysis.get("opportunity_segments") or []:
        item = dict(segment)
        segment_scores = list((item.get("platform_scores") or {}).values())
        weighted = confidence_weighted_score(segment_scores)
        if weighted is None:
            continue

        sample_size = int(item.get("sample_size") or 0)
        eligible_platforms = sum(1 for result in segment_scores if result.get("eligible"))
        platform_factor = eligible_platforms / requested_platforms
        sample_factor = min(1.0, sample_size / 12.0)
        confidence_factor = min(1.0, float(item.get("confidence") or 0) / 80.0)

        reliability = 0.50 * sample_factor + 0.30 * platform_factor + 0.20 * confidence_factor
        calibrated = _shrink_to_neutral(weighted, reliability)
        item["raw_opportunity_score"] = round(weighted, 1)
        item["opportunity_score"] = round(calibrated, 1)
        item["verdict"] = _verdict(calibrated)
        item["ranking_reliability"] = round(reliability * 100, 1)
        calibrated_segments.append(item)

    calibrated_segments.sort(
        key=lambda item: (
            float(item.get("opportunity_score") or 0),
            float(item.get("ranking_reliability") or 0),
            int(item.get("sample_size") or 0),
        ),
        reverse=True,
    )
    analysis["opportunity_segments"] = calibrated_segments[:5]

    keyword = str(analysis.get("keyword") or "")
    overall_prefix = f"“{keyword}”当前公开数据机会分为 " if keyword else ""
    recommendations = [
        text
        for text in (analysis.get("recommendations") or [])
        if not text.startswith("自动拆分的商品族中，当前排序最高的是")
        and not text.startswith("自动拆分的商品族中，校准后排序最高的是")
        and (not overall_prefix or not text.startswith(overall_prefix))
    ]

    if all_eligible and analysis.get("opportunity_score") is not None and keyword:
        recommendations.insert(
            0,
            f"“{keyword}”按平台数据完整度校准后的机会分为 {analysis['opportunity_score']}，"
            f"结论：{analysis['verdict']}。",
        )

    if calibrated_segments:
        top = calibrated_segments[0]
        recommendations.append(
            f"自动拆分的商品族中，校准后排序最高的是“{top['label']}”"
            f"（机会分 {top['opportunity_score']}，排序可靠度 {top['ranking_reliability']}%，"
            f"样本 {top['sample_size']} 条）；优先验证这一子类。"
        )
    analysis["recommendations"] = recommendations

    methodology = str(analysis.get("methodology") or "")
    calibration_note = (
        "跨平台总分按各平台数据完整度加权；商品族按样本量、平台覆盖和完整度向中性分收缩。"
    )
    if calibration_note not in methodology:
        analysis["methodology"] = methodology + calibration_note
    return analysis
