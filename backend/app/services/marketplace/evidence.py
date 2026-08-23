from __future__ import annotations

from typing import Any


GRADE_LABELS = {
    "A": "高可信",
    "B": "可参考",
    "C": "弱证据",
    "D": "证据不足",
}


def build_evidence_summary(
    platform_scores: dict[str, dict[str, Any]],
    collector_health: dict[str, Any],
    requested_platforms: list[str],
) -> dict[str, Any]:
    requested = [platform for platform in requested_platforms if platform in platform_scores]
    results = [platform_scores[platform] for platform in requested]
    eligible = [result for result in results if result.get("eligible")]
    confidences = [float(result.get("confidence") or 0) for result in results]
    sample_total = sum(int(result.get("sample_size") or 0) for result in results)
    confidence = round(sum(confidences) / len(confidences), 1) if confidences else 0.0
    health_score = float(collector_health.get("health_score") or 0)
    all_eligible = bool(results) and len(eligible) == len(results)
    platform_count = len(requested)

    reasons: list[str] = []
    if not all_eligible:
        reasons.append("至少一个所选平台未达到评分证据门槛")
    if confidence < 65:
        reasons.append("平均数据完整度低于 65%")
    if health_score < 65:
        reasons.append("采集器健康度低于 65%")
    if sample_total < 15:
        reasons.append("相关商品总样本偏少")
    if collector_health.get("unhealthy_platforms"):
        reasons.append("存在疑似页面结构变化的平台")

    if (
        platform_count >= 2
        and all_eligible
        and confidence >= 80
        and health_score >= 80
        and sample_total >= 30
    ):
        grade = "A"
    elif all_eligible and confidence >= 65 and health_score >= 65 and sample_total >= 15:
        grade = "B"
    elif eligible and confidence >= 50 and health_score >= 45 and sample_total >= 8:
        grade = "C"
    else:
        grade = "D"

    return {
        "grade": grade,
        "label": GRADE_LABELS[grade],
        "confidence": confidence,
        "collector_health": round(health_score, 1),
        "sample_total": sample_total,
        "requested_platforms": requested,
        "eligible_platforms": [
            platform for platform in requested if platform_scores[platform].get("eligible")
        ],
        "reasons": reasons[:4],
    }
