from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import uuid
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
from app.services.marketplace.browser import (
    activate_platform_tab,
    browser_ready,
    ensure_browser,
    ensure_platform_tab,
    find_platform_tab,
)
from app.services.marketplace.calibration import calibrate_analysis
from app.services.marketplace.evidence import build_evidence_summary
from app.services.marketplace.extension_bridge import extension_request
from app.services.marketplace.health import assess_collection_health, summarize_collector_health
from app.services.marketplace.raw_collection import RawCardAccumulator
from app.services.marketplace.query_localization import marketplace_search_term
from app.services.marketplace.scoring import build_analysis

settings = get_settings()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="marketplace-collector")
_queue_lock = threading.Lock()
_run_creation_lock = threading.Lock()
_queued_run_ids: set[int] = set()
_requeue_requested: set[int] = set()
_process_worker_id = uuid.uuid4().hex[:12]


class WorkerLeaseLost(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _worker_token(run_id: int) -> str:
    return f"{_process_worker_id}:{run_id}:{uuid.uuid4().hex[:12]}"


def _request_config(keyword: TrackedKeyword) -> dict[str, Any]:
    results_limit = int(keyword.results_limit)
    search_pages = int(settings.SEARCH_PAGES)
    return {
        "keyword": keyword.keyword,
        "marketplace_query": marketplace_search_term(keyword.keyword),
        "platforms": list(keyword.platforms or []),
        "results_limit": results_limit,
        "search_pages": search_pages,
        "max_results_per_platform": results_limit * search_pages,
    }


def _collection_request(run: AnalysisRun, keyword: TrackedKeyword) -> SimpleNamespace:
    stored = (run.analysis or {}).get("request_config") if isinstance(run.analysis, dict) else None
    config = stored if isinstance(stored, dict) else _request_config(keyword)
    platforms = [str(platform) for platform in (config.get("platforms") or []) if str(platform) in ADAPTERS]
    try:
        results_limit = int(config.get("results_limit", keyword.results_limit))
    except (TypeError, ValueError):
        results_limit = int(keyword.results_limit)
    try:
        search_pages = max(1, min(5, int(config.get("search_pages", settings.SEARCH_PAGES))))
    except (TypeError, ValueError):
        search_pages = int(settings.SEARCH_PAGES)
    return SimpleNamespace(
        id=keyword.id,
        keyword=str(config.get("keyword") or keyword.keyword),
        platforms=platforms,
        results_limit=results_limit,
        search_pages=search_pages,
        request_config={
            "keyword": str(config.get("keyword") or keyword.keyword),
            "marketplace_query": str(
                config.get("marketplace_query")
                or marketplace_search_term(str(config.get("keyword") or keyword.keyword))
            ),
            "platforms": platforms,
            "results_limit": results_limit,
            "search_pages": search_pages,
            "max_results_per_platform": results_limit * search_pages,
        },
    )


def create_run(db: Session, keyword: TrackedKeyword, trigger: str = "manual") -> AnalysisRun:
    """Return the active attempt or create one exactly once within this app process."""
    with _run_creation_lock:
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
            analysis={"request_config": _request_config(keyword)},
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
        should_requeue = False
        with _queue_lock:
            _queued_run_ids.discard(run_id)
            if run_id in _requeue_requested:
                _requeue_requested.discard(run_id)
                should_requeue = True
        if should_requeue:
            try:
                submit_run(run_id)
            except Exception:
                logger.error("延迟重新入队失败 run=%s", run_id, exc_info=True)


def submit_run(run_id: int) -> bool:
    """Queue a pending run exactly once without losing a concurrent resume request."""
    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run or run.status != "pending":
            return False
    finally:
        db.close()

    with _queue_lock:
        if run_id in _queued_run_ids:
            _requeue_requested.add(run_id)
            return True
        _queued_run_ids.add(run_id)
    try:
        _executor.submit(_execute_queued, run_id)
        return True
    except Exception:
        with _queue_lock:
            _queued_run_ids.discard(run_id)
            _requeue_requested.discard(run_id)
        raise


def _save_state(run_id: int, worker_id: str | None = None, **values: Any) -> bool:
    db = SessionLocal()
    try:
        query = db.query(AnalysisRun).filter(AnalysisRun.id == run_id)
        payload = dict(values)
        if worker_id is not None:
            query = query.filter(AnalysisRun.worker_id == worker_id, AnalysisRun.status == "running")
            payload["heartbeat_at"] = _utcnow()
        updated = query.update(payload, synchronize_session=False)
        if updated != 1:
            db.rollback()
            return False
        db.commit()
        return True
    finally:
        db.close()


def _require_lease(run_id: int, worker_id: str, **values: Any) -> None:
    if not _save_state(run_id, worker_id, **values):
        raise WorkerLeaseLost(f"run {run_id} 的 worker 租约已失效")


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


def _persist_platform(
    run_id: int,
    keyword_id: int,
    platform: str,
    listings: list[MarketplaceListing],
    worker_id: str,
) -> None:
    db = SessionLocal()
    try:
        claimed = (
            db.query(AnalysisRun)
            .filter(
                AnalysisRun.id == run_id,
                AnalysisRun.worker_id == worker_id,
                AnalysisRun.status == "running",
            )
            .update({AnalysisRun.heartbeat_at: _utcnow()}, synchronize_session=False)
        )
        if claimed != 1:
            db.rollback()
            raise WorkerLeaseLost(f"run {run_id} 的 worker 租约已失效")
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


class _ExtensionCDPSocket:
    """Small socket-compatible adapter over the user's main Chrome extension."""

    def __init__(self, platform: str, url: str):
        self.platform = platform
        self.url = url
        self.session_id = ""
        self._responses: asyncio.Queue[str] = asyncio.Queue()

    async def __aenter__(self) -> "_ExtensionCDPSocket":
        payload = await asyncio.to_thread(
            extension_request,
            "attach",
            max(10.0, float(settings.COLLECTION_TIMEOUT_SECONDS)),
            platform=self.platform,
            url=self.url,
        )
        self.session_id = str((payload or {}).get("session_id") or "")
        if not self.session_id:
            raise RuntimeError(f"无法连接你的 Chrome {self.platform.title()} 标签页")
        return self

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        if not self.session_id:
            return
        try:
            await asyncio.to_thread(
                extension_request,
                "detach",
                5.0,
                session_id=self.session_id,
            )
        except Exception:
            logger.debug("主 Chrome 扩展 detach 失败 session=%s", self.session_id, exc_info=True)

    async def send(self, raw_message: str) -> None:
        message = json.loads(raw_message)
        request_id = message.get("id")
        try:
            result = await asyncio.to_thread(
                extension_request,
                "cdp",
                max(10.0, float(settings.COLLECTION_TIMEOUT_SECONDS)),
                session_id=self.session_id,
                method=str(message.get("method") or ""),
                command_params=message.get("params") or {},
            )
            response = {"id": request_id, "result": result or {}}
        except Exception as exc:
            response = {"id": request_id, "error": {"message": str(exc)}}
        await self._responses.put(json.dumps(response, ensure_ascii=False))

    async def recv(self) -> str:
        return await self._responses.get()


def _runtime_value(result: dict[str, Any], default: Any = None) -> Any:
    return result.get("result", {}).get("value", default)


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _enrich_first_listing(existing: MarketplaceListing, incoming: MarketplaceListing) -> None:
    """Fill missing public fields without changing the product's first-seen provenance."""
    for field in (
        "shop_id",
        "image_url",
        "price",
        "original_price",
        "discount_percent",
        "sold_count",
        "rating",
        "review_count",
        "seller_name",
        "seller_location",
        "is_sponsored",
    ):
        if getattr(existing, field) in (None, "") and getattr(incoming, field) not in (None, ""):
            setattr(existing, field, getattr(incoming, field))
    if len(incoming.title.strip()) > len(existing.title.strip()):
        existing.title = incoming.title
    first_raw = dict(existing.raw_data or {})
    for key, value in (incoming.raw_data or {}).items():
        if key in {"search_page", "page_rank", "page_size"}:
            continue
        if first_raw.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
            first_raw[key] = value
    existing.raw_data = first_raw
    existing.data_quality = max(existing.data_quality, incoming.data_quality)


async def _collect_resident_tab(
    adapter: Any,
    keyword: str,
    limit: int,
    search_pages: int,
    run_id: int,
    worker_id: str,
) -> tuple[str, str, list[dict[str, Any]], list[MarketplaceListing], list[str]]:
    url = adapter.search_url(keyword)
    if settings.BROWSER_MODE == "extension":
        socket_context: Any = _ExtensionCDPSocket(adapter.platform, url)
    else:
        tab = ensure_platform_tab(adapter.platform, url)
        websocket_url = tab.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise RuntimeError(f"{adapter.platform.title()} Chrome 标签页没有调试连接")
        socket_context = websocket_connect(websocket_url, open_timeout=5, max_size=8 * 1024 * 1024)

    current_url = url
    body_text = ""
    pages = max(1, min(5, int(search_pages)))
    all_raw = RawCardAccumulator(max_cards=max(200, limit * pages * 4))
    listings: list[MarketplaceListing] = []
    seen_item_ids: set[str] = set()
    listings_by_id: dict[str, MarketplaceListing] = {}
    page_warnings: list[str] = []
    last_heartbeat = 0.0
    async with socket_context as socket:
        request_id = 1
        await _cdp_call(socket, request_id, "Page.enable")
        request_id += 1
        await _cdp_call(socket, request_id, "Runtime.enable")
        request_id += 1
        await _cdp_call(socket, request_id, "Page.bringToFront")

        # The timeout is per page. Each URL represents a real result page and must get a full
        # render/scroll opportunity; dividing one timeout across all pages made later pages race
        # the marketplaces' virtualized grids.
        page_window = max(8.0, min(float(settings.COLLECTION_TIMEOUT_SECONDS), 45.0))
        for page_number in range(1, pages + 1):
            page_url = adapter.search_url(keyword, page=page_number)
            page_raw = RawCardAccumulator(max_cards=max(100, limit * 4))
            try:
                _require_lease(
                    run_id,
                    worker_id,
                    current_step=(
                        f"正在采集 {adapter.platform.title()} Malaysia · "
                        f"第 {page_number}/{pages} 页"
                    ),
                )
                request_id += 1
                await _cdp_call(socket, request_id, "Page.navigate", {"url": page_url})
                request_id += 1
                await _cdp_call(
                    socket,
                    request_id,
                    "Runtime.evaluate",
                    {"expression": "window.scrollTo(0, 0); true", "returnByValue": True},
                )

                deadline = time.monotonic() + page_window
                previous_count = -1
                stable_rounds = 0
                at_bottom = False
                while time.monotonic() < deadline:
                    now_mono = time.monotonic()
                    if now_mono - last_heartbeat >= settings.RUN_HEARTBEAT_SECONDS:
                        _require_lease(run_id, worker_id)
                        last_heartbeat = now_mono

                    await asyncio.sleep(1.0)
                    request_id += 1
                    location = await _cdp_call(socket, request_id, "Runtime.evaluate", {"expression": "location.href", "returnByValue": True})
                    request_id += 1
                    body = await _cdp_call(socket, request_id, "Runtime.evaluate", {"expression": "document.body ? document.body.innerText : ''", "returnByValue": True})
                    current_url = str(_runtime_value(location, page_url) or page_url)
                    body_text = str(_runtime_value(body, "") or "")
                    if adapter.is_verification_page(current_url, body_text):
                        return current_url, body_text, [], [], page_warnings

                    request_id += 1
                    cards = await _cdp_call(
                        socket,
                        request_id,
                        "Runtime.evaluate",
                        {"expression": f"({adapter.extraction_script})()", "returnByValue": True, "awaitPromise": True},
                    )
                    value = _runtime_value(cards, [])
                    page_raw.add(value if isinstance(value, list) else [])

                    usable_count = len(adapter.parse_cards(page_raw.cards(), limit))
                    if usable_count >= limit:
                        break
                    stable_rounds = stable_rounds + 1 if usable_count > 0 and usable_count == previous_count else 0
                    if stable_rounds >= 3 and at_bottom and usable_count >= min(6, limit):
                        break
                    previous_count = usable_count

                    request_id += 1
                    scroll = await _cdp_call(
                        socket,
                        request_id,
                        "Runtime.evaluate",
                        {
                            "expression": (
                                "(() => { const before = window.scrollY; "
                                "window.scrollBy(0, Math.max(window.innerHeight, 700)); "
                                "return { moved: window.scrollY > before + 1, "
                                "atBottom: window.innerHeight + window.scrollY >= "
                                "document.documentElement.scrollHeight - 24 }; })()"
                            ),
                            "returnByValue": True,
                        },
                    )
                    scroll_state = _runtime_value(scroll, {})
                    at_bottom = bool(
                        isinstance(scroll_state, dict) and scroll_state.get("atBottom")
                    )
            except WorkerLeaseLost:
                raise
            except Exception as exc:
                if not listings:
                    raise
                page_warnings.append(f"第 {page_number} 页采集失败：{str(exc)[:180]}")
                logger.warning(
                    "%s 第 %s/%s 页采集失败，保留前页结果: %s",
                    adapter.platform,
                    page_number,
                    pages,
                    exc,
                )
                continue

            page_cards = page_raw.cards()
            all_raw.add(page_cards)
            page_listings = adapter.parse_cards(page_cards, limit)
            if not page_listings:
                page_warnings.append(f"第 {page_number} 页没有采集到可解析商品")
                continue
            for listing in page_listings:
                raw = dict(listing.raw_data or {})
                local_rank = _positive_int(raw.get("page_position"), listing.search_rank)
                page_size = max(local_rank, _positive_int(raw.get("page_size"), limit))
                listing.search_rank = (page_number - 1) * page_size + local_rank
                listing.raw_data = {
                    **raw,
                    "search_page": page_number,
                    "page_rank": local_rank,
                    "page_size": page_size,
                }
                if listing.item_id in seen_item_ids:
                    _enrich_first_listing(listings_by_id[listing.item_id], listing)
                    continue
                seen_item_ids.add(listing.item_id)
                listings_by_id[listing.item_id] = listing
                listings.append(listing)

    _require_lease(run_id, worker_id)
    return current_url, body_text, all_raw.cards(), listings, page_warnings


async def _collect(
    keyword: TrackedKeyword,
    run_id: int,
    worker_id: str,
) -> tuple[
    dict[str, list[MarketplaceListing]],
    dict[str, str],
    dict[str, dict[str, Any]],
]:
    results: dict[str, list[MarketplaceListing]] = {}
    errors: dict[str, str] = {}
    platform_health: dict[str, dict[str, Any]] = {}
    platforms = [platform for platform in (keyword.platforms or []) if platform in ADAPTERS]
    if not platforms:
        raise RuntimeError("未选择有效平台")

    initial_urls = [ADAPTERS[platform].search_url(keyword.keyword) for platform in platforms]
    ensure_browser(initial_urls[:1])

    for index, platform in enumerate(platforms):
        adapter = ADAPTERS[platform]
        progress = 10 + int(index / len(platforms) * 55)
        _require_lease(run_id, worker_id, progress=progress, current_step=f"正在采集 {platform.title()} Malaysia")
        try:
            current_url, body, raw_cards, listings, page_warnings = await _collect_resident_tab(
                adapter,
                keyword.keyword,
                keyword.results_limit,
                keyword.search_pages,
                run_id,
                worker_id,
            )
            if adapter.is_verification_page(current_url, body):
                raise VerificationRequired(platform, current_url)

            target_limit = keyword.results_limit * keyword.search_pages
            health = assess_collection_health(raw_cards, listings, target_limit)
            if page_warnings:
                health["warnings"] = [*(health.get("warnings") or []), *page_warnings]
                if health.get("status") == "healthy":
                    health["status"] = "degraded"
            platform_health[platform] = health
            platform_errors: list[str] = []
            if page_warnings:
                platform_errors.append("部分页面未完整采集：" + "；".join(page_warnings))
            if raw_cards and not listings:
                platform_errors.append("搜索页有内容，但当前页面结构无法解析；请检查采集适配器")
            elif not listings:
                platform_errors.append("公开搜索页没有返回可解析商品")
            elif health["status"] == "unhealthy":
                platform_errors.append("采集器健康度异常，页面结构可能已经变化")
            if platform_errors:
                errors[platform] = "；".join(platform_errors)
            results[platform] = listings
            _persist_platform(run_id, keyword.id, platform, listings, worker_id)
            await asyncio.sleep(1.0)
        except VerificationRequired:
            raise
        except WorkerLeaseLost:
            raise
        except Exception as exc:
            logger.warning("%s 采集失败: %s", platform, exc, exc_info=True)
            results[platform] = []
            health = assess_collection_health(
                [],
                [],
                keyword.results_limit * keyword.search_pages,
            )
            health["status"] = "error"
            health["health_score"] = 0.0
            health["warnings"] = [str(exc)[:300]]
            platform_health[platform] = health
            errors[platform] = str(exc)[:500]
    return results, errors, platform_health


def _persist_results(db: Session, run: AnalysisRun, keyword: TrackedKeyword, collected: dict[str, list[MarketplaceListing]]) -> None:
    db.query(ListingSnapshot).filter(ListingSnapshot.run_id == run.id).delete(synchronize_session=False)
    for platform, listings in collected.items():
        for listing in listings:
            db.add(_snapshot(run.id, keyword.id, platform, listing))


def _mark_verification(run_id: int, exc: VerificationRequired, worker_id: str) -> None:
    db = SessionLocal()
    try:
        updated = (
            db.query(AnalysisRun)
            .filter(
                AnalysisRun.id == run_id,
                AnalysisRun.worker_id == worker_id,
                AnalysisRun.status == "running",
            )
            .update(
                {
                    AnalysisRun.status: "needs_verification",
                    AnalysisRun.current_step: f"{exc.platform.title()} 需要人工验证",
                    AnalysisRun.verification_platform: exc.platform,
                    AnalysisRun.error_message: f"{exc.platform.title()} 触发了人工验证。请在你的 Google Chrome 完成验证，保持该标签页打开，然后点击继续。",
                    AnalysisRun.worker_id: None,
                    AnalysisRun.heartbeat_at: None,
                },
                synchronize_session=False,
            )
        )
        if updated:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()


def _mark_failed(run_id: int, exc: Exception, worker_id: str | None = None) -> None:
    logger.error("采集任务失败 run=%s: %s", run_id, exc, exc_info=True)
    db = SessionLocal()
    try:
        query = db.query(AnalysisRun).filter(AnalysisRun.id == run_id)
        if worker_id is not None:
            query = query.filter(AnalysisRun.worker_id == worker_id)
        updated = query.update(
            {
                AnalysisRun.status: "failed",
                AnalysisRun.current_step: "采集失败",
                AnalysisRun.error_message: str(exc)[:1000],
                AnalysisRun.completed_at: _utcnow(),
                AnalysisRun.worker_id: None,
                AnalysisRun.heartbeat_at: None,
            },
            synchronize_session=False,
        )
        if updated:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()


def _apply_evidence_gate(analysis: dict[str, Any], evidence: dict[str, Any]) -> None:
    grade = evidence.get("grade")
    if grade == "D":
        analysis["opportunity_score"] = None
        analysis["verdict"] = "数据不足"
        for segment in analysis.get("opportunity_segments") or []:
            segment["verdict"] = "数据不足"
        recommendations = [
            text
            for text in (analysis.get("recommendations") or [])
            if not text.startswith("自动拆分的商品族中，")
        ]
        recommendations.insert(
            0,
            "本次证据等级为 D，不输出强选品结论；优先检查采集健康度并补齐样本。",
        )
        analysis["recommendations"] = recommendations
    elif grade == "C" and analysis.get("verdict") == "建议尝试":
        analysis["verdict"] = "谨慎观察"
        analysis.setdefault("recommendations", []).insert(
            0,
            "本次证据等级为 C，原始机会信号偏强但证据仍弱，结论已降级为谨慎观察。",
        )


def execute_run_sync(run_id: int) -> None:
    worker_id = _worker_token(run_id)
    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run or run.status not in ("pending", "running"):
            return
        keyword = db.query(TrackedKeyword).filter(TrackedKeyword.id == run.keyword_id).first()
        if not keyword:
            return
        collection_request = _collection_request(run, keyword)
        run.status = "running"
        run.progress = 5
        run.current_step = "准备项目 Chrome"
        run.started_at = _utcnow()
        run.completed_at = None
        run.error_message = None
        run.verification_platform = None
        run.worker_id = worker_id
        run.heartbeat_at = _utcnow()
        run.analysis = {**(run.analysis or {}), "request_config": collection_request.request_config}
        db.commit()
    finally:
        db.close()

    try:
        collected, collection_errors, platform_health = asyncio.run(
            _collect(collection_request, run_id, worker_id)
        )
    except VerificationRequired as exc:
        _mark_verification(run_id, exc, worker_id)
        return
    except WorkerLeaseLost:
        logger.warning("worker 租约已失效，停止旧采集 run=%s worker=%s", run_id, worker_id)
        return
    except Exception as exc:
        _mark_failed(run_id, exc, worker_id)
        return

    db = SessionLocal()
    try:
        claimed = (
            db.query(AnalysisRun)
            .filter(
                AnalysisRun.id == run_id,
                AnalysisRun.worker_id == worker_id,
                AnalysisRun.status == "running",
            )
            .update(
                {
                    AnalysisRun.heartbeat_at: _utcnow(),
                    AnalysisRun.current_step: "计算公开数据机会分",
                    AnalysisRun.progress: 75,
                },
                synchronize_session=False,
            )
        )
        if claimed != 1:
            db.rollback()
            logger.warning("最终写入前 worker 租约已失效 run=%s worker=%s", run_id, worker_id)
            return

        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).one()
        keyword = db.query(TrackedKeyword).filter(TrackedKeyword.id == run.keyword_id).first()
        if not keyword:
            db.rollback()
            return
        _persist_results(db, run, keyword, collected)
        db.flush()

        by_platform = {platform: [listing.to_dict() for listing in listings] for platform, listings in collected.items()}
        analysis = calibrate_analysis(build_analysis(collection_request.keyword, by_platform))
        collector_health = summarize_collector_health(platform_health, collection_request.platforms)
        evidence = build_evidence_summary(
            analysis["platform_scores"],
            collector_health,
            collection_request.platforms,
        )
        analysis["collector_health"] = collector_health
        analysis["evidence"] = evidence
        _apply_evidence_gate(analysis, evidence)

        counts = {platform: len(items) for platform, items in collected.items()}
        expected = set(collection_request.platforms)
        successful = {platform for platform, items in collected.items() if items}
        if not successful:
            run.status = "failed"
            run.current_step = "未采集到有效商品"
        elif expected == successful and not collection_errors:
            run.status = "completed"
            run.current_step = "分析完成"
        else:
            run.status = "partial"
            run.current_step = "部分平台无结果"
        run.progress = 100
        run.opportunity_score = analysis["opportunity_score"]
        run.verdict = analysis["verdict"]
        run.confidence = analysis["confidence"]
        run.platform_scores = analysis["platform_scores"]
        run.analysis = {
            **analysis,
            "request_config": collection_request.request_config,
            "counts": counts,
            "platform_errors": collection_errors,
        }
        run.error_message = "；".join(f"{name.title()}: {message}" for name, message in collection_errors.items()) if collection_errors else None
        if run.status == "failed" and not run.error_message:
            run.error_message = "所选平台没有返回可用于分析的公开商品"
        run.completed_at = _utcnow()
        run.worker_id = None
        run.heartbeat_at = None
        keyword.last_run_at = _utcnow()
        if successful:
            keyword.last_success_at = _utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        _mark_failed(run_id, exc, worker_id)
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

    if settings.BROWSER_MODE != "extension" and not _visible_desktop_available():
        raise RuntimeError("当前运行环境没有可见桌面，无法人工处理验证码。请在 Windows/macOS/Linux 桌面本机运行后继续。")

    if not browser_ready():
        ensure_browser([url])
    tab = find_platform_tab(platform)
    if not tab:
        tab = ensure_platform_tab(platform, url)
    if not activate_platform_tab(platform):
        raise RuntimeError("项目 Chrome 已启动，但无法激活验证标签页")
    return str(tab.get("url") or url)
