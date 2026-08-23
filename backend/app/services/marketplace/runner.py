from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session
from websockets.asyncio.client import connect as websocket_connect

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.marketplace import AnalysisRun, ListingSnapshot, TrackedKeyword
from app.services.marketplace.adapters import ADAPTERS, MarketplaceListing, VerificationRequired
from app.services.marketplace.analysis_guard import finalize_analysis_evidence
from app.services.marketplace.browser import (
    activate_platform_tab,
    browser_ready,
    ensure_browser,
    ensure_platform_tab,
    find_platform_tab,
)
from app.services.marketplace.scoring import build_analysis

settings = get_settings()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="marketplace-collector")
_queue_lock = threading.Lock()
_queued_run_ids: set[int] = set()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_run(db: Session, keyword: TrackedKeyword, trigger: str = "manual") -> AnalysisRun:
    active = (
        db.query(AnalysisRun)
        .filter(
            AnalysisRun.keyword_id == keyword.id,
            AnalysisRun.status.in_(("pending", "running", "needs_verification")),
        )
        .order_by(AnalysisRun.id.desc())
        .first()
    )
    if active:
        return active
    run = AnalysisRun(
        keyword_id=keyword.id,
        trigger=trigger,
        status="pending",
        progress=0,
        current_step="等待采集",
    )
    keyword.last_run_at = _utcnow()
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _execute_queued(run_id: int) -> None:
    try:
        execute_run_sync(run_id)
    finally:
        with _queue_lock:
            _queued_run_ids.discard(run_id)


def submit_run(run_id: int) -> bool:
    """Queue a pending run exactly once within this process."""
    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run or run.status != "pending":
            return False
    finally:
        db.close()

    with _queue_lock:
        if run_id in _queued_run_ids:
            return False
        _queued_run_ids.add(run_id)
    try:
        _executor.submit(_execute_queued, run_id)
        return True
    except Exception:
        with _queue_lock:
            _queued_run_ids.discard(run_id)
        raise


def _save_state(run_id: int, **values: Any) -> None:
    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            return
        for key, value in values.items():
            setattr(run, key, value)
        db.commit()
    finally:
        db.close()


def _snapshot(run_id: int, keyword_id: int, platform: str, listing: MarketplaceListing) -> ListingSnapshot:
    return ListingSnapshot(
        run_id=run_id,
        keyword_id=keyword_id,
        platform=platform,
        item_id=listing.item_id,
        shop_id=listing.shop_id,
        title=listing.title,
        product_url=listing.product_url,
        image_url=listing.image_url,
        price=listing.price,
        original_price=listing.original_price,
        discount_percent=listing.discount_percent,
        sold_count=listing.sold_count,
        rating=listing.rating,
        review_count=listing.review_count,
        seller_name=listing.seller_name,
        seller_location=listing.seller_location,
        is_sponsored=listing.is_sponsored,
        search_rank=listing.search_rank,
        data_quality=listing.data_quality,
        raw_data=listing.raw_data,
    )


def _persist_platform(run_id: int, keyword_id: int, platform: str, listings: list[MarketplaceListing]) -> None:
    """Checkpoint a completed platform before moving to the next one."""
    db = SessionLocal()
    try:
        db.query(ListingSnapshot).filter(
            ListingSnapshot.run_id == run_id,
            ListingSnapshot.platform == platform,
        ).delete(synchronize_session=False)
        for listing in listings:
            db.add(_snapshot(run_id, keyword_id, platform, listing))
        db.commit()
    finally:
        db.close()


async def _cdp_call(socket: Any, request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    await socket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
    while True:
        message = json.loads(await asyncio.wait_for(socket.recv(), timeout=settings.COLLECTION_TIMEOUT_SECONDS))
        if message.get("id") != request_id:
            continue
        if "error" in message:
            raise RuntimeError(message["error"].get("message", f"Chrome 调试调用失败: {method}"))
        return message.get("result", {})


def _runtime_value(result: dict[str, Any], default: Any = None) -> Any:
    return result.get("result", {}).get("value", default)


async def _collect_resident_tab(adapter: Any, keyword: str, limit: int) -> tuple[str, str, list[dict[str, Any]]]:
    url = adapter.search_url(keyword)
    tab = ensure_platform_tab(adapter.platform, url)
    websocket_url = tab.get("webSocketDebuggerUrl")
    if not websocket_url:
        raise RuntimeError(f"{adapter.platform.title()} Chrome 标签页没有调试连接")

    current_url = url
    body_text = ""
    raw_cards: list[dict[str, Any]] = []
    async with websocket_connect(websocket_url, open_timeout=5, max_size=8 * 1024 * 1024) as socket:
        request_id = 1
        await _cdp_call(socket, request_id, "Page.enable")
        request_id += 1
        await _cdp_call(socket, request_id, "Runtime.enable")
        request_id += 1
        await _cdp_call(socket, request_id, "Page.navigate", {"url": url})

        deadline = time.monotonic() + min(float(settings.COLLECTION_TIMEOUT_SECONDS), 24.0)
        previous_count = -1
        stable_rounds = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            request_id += 1
            location = await _cdp_call(socket, request_id, "Runtime.evaluate", {"expression": "location.href", "returnByValue": True})
            request_id += 1
            body = await _cdp_call(socket, request_id, "Runtime.evaluate", {"expression": "document.body ? document.body.innerText : ''", "returnByValue": True})
            current_url = str(_runtime_value(location, url) or url)
            body_text = str(_runtime_value(body, "") or "")
            if adapter.is_verification_page(current_url, body_text):
                return current_url, body_text, []

            request_id += 1
            cards = await _cdp_call(
                socket,
                request_id,
                "Runtime.evaluate",
                {"expression": f"({adapter.extraction_script})()", "returnByValue": True, "awaitPromise": True},
            )
            value = _runtime_value(cards, [])
            raw_cards = value if isinstance(value, list) else []

            usable_count = len(adapter.parse_cards(raw_cards, limit))
            if usable_count >= limit:
                break
            stable_rounds = stable_rounds + 1 if usable_count > 0 and usable_count == previous_count else 0
            if stable_rounds >= 2 and usable_count >= min(6, limit):
                break
            previous_count = usable_count

            request_id += 1
            await _cdp_call(
                socket,
                request_id,
                "Runtime.evaluate",
                {"expression": "window.scrollBy(0, Math.max(window.innerHeight, 700)); true", "returnByValue": True},
            )

    return current_url, body_text, raw_cards


async def _collect(keyword: TrackedKeyword, run_id: int) -> tuple[dict[str, list[MarketplaceListing]], dict[str, str]]:
    results: dict[str, list[MarketplaceListing]] = {}
    errors: dict[str, str] = {}
    platforms = [platform for platform in (keyword.platforms or []) if platform in ADAPTERS]
    if not platforms:
        raise RuntimeError("未选择有效平台")

    initial_urls = [ADAPTERS[platform].search_url(keyword.keyword) for platform in platforms]
    ensure_browser(initial_urls[:1])

    for index, platform in enumerate(platforms):
        adapter = ADAPTERS[platform]
        progress = 10 + int(index / len(platforms) * 55)
        _save_state(run_id, progress=progress, current_step=f"正在采集 {platform.title()} Malaysia")
        try:
            current_url, body, raw_cards = await _collect_resident_tab(adapter, keyword.keyword, keyword.results_limit)
            if adapter.is_verification_page(current_url, body):
                raise VerificationRequired(platform, current_url)

            listings = adapter.parse_cards(raw_cards, keyword.results_limit)
            if raw_cards and not listings:
                errors[platform] = "搜索页有内容，但当前页面结构无法解析；请检查采集适配器"
            elif not listings:
                errors[platform] = "公开搜索页没有返回可解析商品"
            results[platform] = listings
            _persist_platform(run_id, keyword.id, platform, listings)
            await asyncio.sleep(1.0)
        except VerificationRequired:
            raise
        except Exception as exc:
            logger.warning("%s 采集失败: %s", platform, exc, exc_info=True)
            results[platform] = []
            errors[platform] = str(exc)[:500]
    return results, errors


def _persist_results(db: Session, run: AnalysisRun, keyword: TrackedKeyword, collected: dict[str, list[MarketplaceListing]]) -> None:
    db.query(ListingSnapshot).filter(ListingSnapshot.run_id == run.id).delete(synchronize_session=False)
    for platform, listings in collected.items():
        for listing in listings:
            db.add(_snapshot(run.id, keyword.id, platform, listing))


def _mark_verification(run_id: int, exc: VerificationRequired) -> None:
    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            return
        run.status = "needs_verification"
        run.current_step = f"{exc.platform.title()} 需要人工验证"
        run.verification_platform = exc.platform
        run.error_message = f"{exc.platform.title()} 触发了人工验证。打开项目 Chrome 完成验证，保持该标签页和窗口打开，然后点击继续。"
        db.commit()
    finally:
        db.close()


def _mark_failed(run_id: int, exc: Exception) -> None:
    logger.error("采集任务失败 run=%s: %s", run_id, exc, exc_info=True)
    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            return
        run.status = "failed"
        run.current_step = "采集失败"
        run.error_message = str(exc)[:1000]
        run.completed_at = _utcnow()
        db.commit()
    finally:
        db.close()


def execute_run_sync(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run or run.status not in ("pending", "running"):
            return
        keyword = db.query(TrackedKeyword).filter(TrackedKeyword.id == run.keyword_id).first()
        if not keyword:
            return
        run.status = "running"
        run.progress = 5
        run.current_step = "准备项目 Chrome"
        run.started_at = _utcnow()
        run.error_message = None
        run.verification_platform = None
        db.commit()
        collection_request = SimpleNamespace(
            id=keyword.id,
            keyword=keyword.keyword,
            platforms=list(keyword.platforms or []),
            results_limit=keyword.results_limit,
        )
    finally:
        db.close()

    try:
        collected, collection_errors = asyncio.run(_collect(collection_request, run_id))
    except VerificationRequired as exc:
        _mark_verification(run_id, exc)
        return
    except Exception as exc:
        _mark_failed(run_id, exc)
        return

    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            return
        keyword = db.query(TrackedKeyword).filter(TrackedKeyword.id == run.keyword_id).first()
        if not keyword:
            return
        _persist_results(db, run, keyword, collected)
        db.flush()

        run.progress = 75
        run.current_step = "计算公开数据机会分"
        by_platform = {platform: [listing.to_dict() for listing in listings] for platform, listings in collected.items()}
        analysis = finalize_analysis_evidence(build_analysis(keyword.keyword, by_platform))
        counts = {platform: len(items) for platform, items in collected.items()}
        expected = set(keyword.platforms or [])
        successful = {platform for platform, items in collected.items() if items}
        run.status = "completed" if expected == successful else "partial"
        run.progress = 100
        run.current_step = "分析完成" if run.status == "completed" else "部分平台无结果"
        run.opportunity_score = analysis["opportunity_score"]
        run.verdict = analysis["verdict"]
        run.confidence = analysis["confidence"]
        run.platform_scores = analysis["platform_scores"]
        run.analysis = {**analysis, "counts": counts, "platform_errors": collection_errors}
        run.error_message = "；".join(f"{name.title()}: {message}" for name, message in collection_errors.items()) if collection_errors else None
        run.completed_at = _utcnow()
        keyword.last_run_at = _utcnow()
        if successful:
            keyword.last_success_at = _utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        _mark_failed(run_id, exc)
    finally:
        db.close()


async def execute_run_async(run_id: int) -> None:
    await asyncio.to_thread(execute_run_sync, run_id)


def _visible_desktop_available() -> bool:
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_verification_browser(run_id: int) -> str:
    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            raise ValueError("任务不存在")
        keyword = db.query(TrackedKeyword).filter(TrackedKeyword.id == run.keyword_id).first()
        if not keyword:
            raise ValueError("关键词不存在")
        platform = run.verification_platform if run.verification_platform in ADAPTERS else "shopee"
        url = ADAPTERS[platform].search_url(keyword.keyword)
    finally:
        db.close()

    if not _visible_desktop_available():
        raise RuntimeError("当前运行环境没有可见桌面，无法人工处理验证码。请在 Windows/macOS/Linux 桌面本机运行后继续。")

    if not browser_ready():
        ensure_browser([url])
    tab = find_platform_tab(platform)
    if not tab:
        tab = ensure_platform_tab(platform, url)
    if not activate_platform_tab(platform):
        raise RuntimeError("项目 Chrome 已启动，但无法激活验证标签页")
    return str(tab.get("url") or url)
