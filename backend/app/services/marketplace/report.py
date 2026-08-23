from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy.orm import Session

from app.models.marketplace import AnalysisRun, ListingSnapshot

HEADER_FILL = PatternFill("solid", fgColor="0F766E")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def _header(ws, labels: list[str]) -> None:
    ws.append(labels)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"


def build_report(db: Session, run: AnalysisRun) -> BytesIO:
    keyword = run.tracked_keyword
    rows = db.query(ListingSnapshot).filter(ListingSnapshot.run_id == run.id).order_by(ListingSnapshot.platform, ListingSnapshot.search_rank).all()
    wb = Workbook()
    summary = wb.active
    summary.title = "综合结论"
    summary.append(["MY Marketplace 公开竞品分析", keyword.keyword])
    summary.append(["机会分", run.opportunity_score if run.opportunity_score is not None else "—"])
    summary.append(["结论", run.verdict or "数据不足"])
    summary.append(["数据完整度", f"{run.confidence or 0:.1f}%"])
    summary.append(["数据口径", "公开搜索结果快照；不是利润、真实月销量或转化率预测。"])
    for platform, score in (run.platform_scores or {}).items():
        platform_score = score.get("score") if score.get("eligible", True) else "—"
        raw_sample = score.get("raw_sample_size", score.get("sample_size", 0))
        reasons = "；".join(score.get("eligibility_reasons") or [])
        summary.append([
            f"{platform.title()} 结论",
            f"{score.get('verdict', '数据不足')}；机会分 {platform_score}；相关样本 {score.get('sample_size', 0)}/{raw_sample}；完整度 {score.get('confidence', 0)}%" + (f"；{reasons}" if reasons else ""),
        ])
    for recommendation in (run.analysis or {}).get("recommendations", []):
        summary.append(["建议", recommendation])
    summary.column_dimensions["A"].width = 22
    summary.column_dimensions["B"].width = 100

    columns = ["排名", "商品", "价格(MYR)", "原价(MYR)", "折扣(%)", "公开已售", "评分", "评论数", "店铺", "地区", "广告", "链接", "采集时间"]
    for platform in ("shopee", "lazada"):
        ws = wb.create_sheet(f"{platform.title()}竞品")
        _header(ws, columns)
        for item in [row for row in rows if row.platform == platform]:
            ws.append([
                item.search_rank, item.title, item.price if item.price is not None else "—",
                item.original_price if item.original_price is not None else "—",
                item.discount_percent if item.discount_percent is not None else "—",
                item.sold_count if item.sold_count is not None else "—",
                item.rating if item.rating is not None else "—",
                item.review_count if item.review_count is not None else "—",
                item.seller_name or "—", item.seller_location or "—",
                "是" if item.is_sponsored is True else "否" if item.is_sponsored is False else "—",
                item.product_url, item.collected_at.isoformat() if item.collected_at else "—",
            ])
        ws.column_dimensions["B"].width = 55
        ws.column_dimensions["L"].width = 55

    trend = wb.create_sheet("每日价格与排名趋势")
    _header(trend, ["采集时间", "平台", "商品ID", "商品", "价格(MYR)", "公开已售", "评论数", "搜索排名"])
    history = db.query(ListingSnapshot).filter(ListingSnapshot.keyword_id == keyword.id).order_by(ListingSnapshot.collected_at).all()
    for item in history:
        trend.append([item.collected_at.isoformat() if item.collected_at else "—", item.platform, item.item_id, item.title, item.price if item.price is not None else "—", item.sold_count if item.sold_count is not None else "—", item.review_count if item.review_count is not None else "—", item.search_rank])

    notes = wb.create_sheet("数据口径说明")
    notes.append(["字段", "说明"])
    notes.append(["公开已售", "平台页面当时展示的累计/公开口径，不换算为日销量或月销量。"])
    notes.append(["机会分", "需求信号40%、进入门槛35%、价格空间25%的证据门槛启发式评分。"])
    notes.append(["缺失值", "公开页面未提供的字段不会填0，也不会把剩余权重放大；证据不足时不输出强结论。"])
    notes.append(["相关性", "低关键词相关性的搜索漂移结果会保留在原始快照，但从机会评分样本中排除。"])
    notes.append(["跨平台", "所选平台分别评分；原始已售数字不直接跨平台比较。"])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output
