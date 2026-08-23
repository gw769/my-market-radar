from __future__ import annotations

from typing import Any


AUTO_SEGMENT_PREFIX = "自动拆分的商品族中"


def finalize_analysis_evidence(analysis: dict[str, Any]) -> dict[str, Any]:
    """Prevent exploratory product-family scores from becoming strong action advice."""
    segments = list(analysis.get("opportunity_segments") or [])
    overall_supported = analysis.get("opportunity_score") is not None

    supported_segments: list[dict[str, Any]] = []
    for segment in segments:
        platform_coverage = float(segment.get("platform_coverage") or 0)
        sample_size = int(segment.get("sample_size") or 0)
        confidence = float(segment.get("confidence") or 0)
        supported = (
            overall_supported
            and platform_coverage >= 99.9
            and sample_size >= 6
            and confidence >= 60
        )
        segment["evidence_status"] = "supported" if supported else "exploratory"
        if supported:
            supported_segments.append(segment)

    recommendations = [
        str(item)
        for item in (analysis.get("recommendations") or [])
        if not str(item).startswith(AUTO_SEGMENT_PREFIX)
    ]

    if supported_segments:
        top = supported_segments[0]
        recommendations.append(
            f"跨所选平台都有充分证据的商品族中，当前排序最高的是“{top['label']}”"
            f"（机会分 {top['opportunity_score']}，样本 {top['sample_size']} 条，"
            f"平台证据覆盖 {top['platform_coverage']}%）；可优先进入供应链、成本和利润验证。"
        )
    elif segments:
        recommendations.append(
            "商品族排序目前只作为探索候选：尚没有一个子方向同时满足跨平台、样本量和完整度门槛，"
            "不要仅凭该排序直接备货。"
        )

    analysis["opportunity_segments"] = segments
    analysis["recommendations"] = recommendations
    analysis["segment_evidence_supported"] = bool(supported_segments)
    return analysis
