from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, parse_qsl, unquote, urlparse

from sqlalchemy import case
from sqlalchemy.orm import Session
from websockets.asyncio.client import connect as websocket_connect

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.marketplace import AnalysisRun, ListingSnapshot, TrackedKeyword
from app.services.marketplace.adapters import ADAPTERS, MarketplaceListing, VerificationRequired
from app.services.marketplace.browser import (
    activate_locked_platform_tab,
    activate_tab,
    browser_ready,
    ensure_browser,
    ensure_platform_tab,
    find_platform_tab,
    find_platform_tab_by_id,
)
from app.services.marketplace.calibration import calibrate_analysis
from app.services.marketplace.evidence import build_evidence_summary
from app.services.marketplace.extension_bridge import ExtensionBridgeError, extension_request
from app.services.marketplace.health import assess_collection_health, summarize_collector_health
from app.services.marketplace.raw_collection import RawCardAccumulator, raw_card_key
from app.services.marketplace.query_localization import marketplace_search_term
from app.services.marketplace.scoring import build_analysis

settings = get_settings()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="marketplace-collector")
_queue_lock = threading.Lock()
_run_creation_lock = threading.Lock()
_queued_run_ids: set[int] = set()
_requeue_requested: set[int] = set()
_process_worker_id = uuid.uuid4().hex[:12]

_EXTENSION_WAKE_PROBE_SECONDS = 3.0
_EXTENSION_ATTACH_FIRST_SECONDS = 35.0
_EXTENSION_ATTACH_RETRY_SECONDS = 40.0
_PAGE_POLL_INTERVAL_SECONDS = 0.8
_PAGE_MAX_SECONDS = 60.0
_PAGE_NAVIGATION_MAX_SECONDS = 12.0
_PAGE_FIRST_RESULTS_MAX_SECONDS = 25.0
_PAGE_TARGET_STABLE_ROUNDS = 4
_PAGE_BOTTOM_STABLE_ROUNDS = 4
_PAGE_STATE_EXPRESSION = r"""(() => {
  const root = document.documentElement;
  const body = document.body;
  const viewportHeight = Math.max(window.innerHeight || 0, 1);
  const scrollHeight = Math.max(root?.scrollHeight || 0, body?.scrollHeight || 0);
  return {
    marker: 'my-market-radar-page-state',
    href: location.href,
    readyState: document.readyState,
    visibilityState: document.visibilityState,
    collectorDocumentNonce: typeof window.__MY_MARKET_RADAR_DOCUMENT_NONCE__ === 'string'
      ? window.__MY_MARKET_RADAR_DOCUMENT_NONCE__
      : '',
    bodyText: body ? (body.innerText || '').slice(0, 5000) : '',
    scrollY: window.scrollY || 0,
    viewportHeight,
    scrollHeight,
    atBottom: viewportHeight + (window.scrollY || 0) >= scrollHeight - 24
  };
})()"""


class WorkerLeaseLost(RuntimeError):
    pass


class PageCollectionError(RuntimeError):
    def __init__(self, message: str, page_diagnostics: list[dict[str, Any]]):
        super().__init__(message)
        self.page_diagnostics = page_diagnostics


class PageDeadlineExceeded(RuntimeError):
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
        requested_progress = payload.pop("progress", None)
        if requested_progress is not None:
            requested_progress = int(requested_progress)
            payload[AnalysisRun.progress] = case(
                (AnalysisRun.progress < requested_progress, requested_progress),
                else_=AnalysisRun.progress,
            )
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

    def __init__(self, platform: str, url: str, lock_key: str):
        self.platform = platform
        self.url = url
        self.lock_key = lock_key
        self.session_id = ""
        self.lock_owner = ""
        self.tab_id = ""
        self._preserve_lock = False
        self._responses: asyncio.Queue[str] = asyncio.Queue()

    def preserve_lock(self) -> None:
        self._preserve_lock = True

    async def __aenter__(self) -> "_ExtensionCDPSocket":
        # Extension 1.0.2 stopped its poll burst as soon as the previous platform detached.
        # A short, disposable tabs request catches an already-awake worker; if it times out the
        # bridge removes it from the queue and the following attach remains ready for the next
        # MV3 alarm.  Retrying attach once also recovers the race where Chrome completed the
        # first attach just after the backend's bounded wait expired.
        try:
            await asyncio.to_thread(
                extension_request,
                "tabs",
                _EXTENSION_WAKE_PROBE_SECONDS,
            )
        except ExtensionBridgeError:
            pass

        configured_timeout = float(settings.COLLECTION_TIMEOUT_SECONDS)
        first_timeout = max(10.0, min(configured_timeout, _EXTENSION_ATTACH_FIRST_SECONDS))
        try:
            payload = await asyncio.to_thread(
                extension_request,
                "attach",
                first_timeout,
                platform=self.platform,
                url=self.url,
                lock_key=self.lock_key,
            )
        except ExtensionBridgeError as exc:
            if "响应超时" not in str(exc):
                raise
            logger.warning(
                "主 Chrome %s 首次 attach 超时，执行一次有界重试",
                self.platform,
            )
            retry_timeout = max(
                10.0,
                min(configured_timeout, _EXTENSION_ATTACH_RETRY_SECONDS),
            )
            payload = await asyncio.to_thread(
                extension_request,
                "attach",
                retry_timeout,
                platform=self.platform,
                url=self.url,
                lock_key=self.lock_key,
            )
        self.session_id = str((payload or {}).get("session_id") or "")
        self.lock_owner = str((payload or {}).get("lock_owner") or "")
        self.tab_id = str((payload or {}).get("tab_id") or "")
        if not self.session_id:
            raise RuntimeError(f"无法连接你的 Chrome {self.platform.title()} 标签页")
        return self

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        if not self.session_id:
            return
        if not self._preserve_lock:
            try:
                await asyncio.to_thread(
                    extension_request,
                    "release_platform_lock",
                    5.0,
                    platform=self.platform,
                    lock_key=self.lock_key,
                    lock_owner=self.lock_owner,
                )
            except Exception:
                # Extension 1.0.2/1.0.3 does not implement run-scoped locks.
                logger.debug("主 Chrome 扩展不支持释放 run 锁", exc_info=True)
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


def _normalized_query_value(parsed: Any, name: str) -> str:
    values = parse_qs(parsed.query, keep_blank_values=True).get(name) or []
    return " ".join(str(values[0] if values else "").split()).casefold()


def _unique_query_value(parsed: Any, name: str) -> str | None:
    values = [
        value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() == name.casefold()
    ]
    if len(values) != 1:
        return None
    return " ".join(str(values[0]).split()).casefold()


def _unique_page_number(parsed: Any, default: int) -> int | None:
    values = [
        value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() == "page"
    ]
    if len(values) > 1:
        return None
    if not values:
        return default
    value = str(values[0]).strip()
    if not re.fullmatch(r"[0-9]+", value):
        return None
    page = int(value)
    return page if page >= 1 else None


def _slug_tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return tuple(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))


def _same_safe_lazada_origin(expected: Any, actual: Any) -> bool:
    if expected.scheme.casefold() != "https" or actual.scheme.casefold() != "https":
        return False
    if any(
        value is not None
        for value in (
            expected.username,
            expected.password,
            actual.username,
            actual.password,
        )
    ):
        return False
    expected_host = (expected.hostname or "").casefold()
    actual_host = (actual.hostname or "").casefold()
    if not expected_host or actual_host != expected_host:
        return False
    try:
        expected_port = expected.port or 443
        actual_port = actual.port or 443
    except ValueError:
        return False
    return expected_port == 443 and actual_port == expected_port


def _strict_tag_slug(raw_slug: str) -> str | None:
    if not raw_slug or "/" in raw_slug or "\\" in raw_slug:
        return None
    if re.search(r"%(?![0-9a-fA-F]{2})", raw_slug):
        return None
    try:
        decoded = unquote(raw_slug, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if not decoded or "\ufffd" in decoded:
        return None
    normalized_forms = (
        decoded,
        unicodedata.normalize("NFKC", decoded),
        unicodedata.normalize("NFKD", decoded),
    )
    for normalized in normalized_forms:
        for char in normalized:
            if char in "/\\?#" or unicodedata.category(char).startswith("C"):
                return None
            unicode_name = unicodedata.name(char, "")
            if "SLASH" in unicode_name or "SOLIDUS" in unicode_name:
                return None
    return normalized_forms[1]


_RESERVED_LAZADA_TAG_SLUGS = {
    "404",
    "404-error",
    "access-denied",
    "captcha",
    "challenge",
    "error",
    "errors",
    "forbidden",
    "not-found",
    "notfound",
    "punish",
    "robot-check",
    "security",
    "security-check",
    "verification",
    "verify",
}


def _lazada_tag_redirect_matches(adapter: Any, expected: Any, actual: Any, actual_url: str) -> bool:
    if not _same_safe_lazada_origin(expected, actual):
        return False
    if expected.path.rstrip("/").casefold() != "/catalog":
        return False
    raw_path = actual.path.rstrip("/")
    if not raw_path.casefold().startswith("/tag/"):
        return False
    raw_slug = raw_path[len("/tag/"):]
    decoded_slug = _strict_tag_slug(raw_slug)
    if decoded_slug is None:
        return False

    slug_tokens = _slug_tokens(decoded_slug)
    slug_key = "-".join(slug_tokens)
    if not slug_tokens or slug_key in _RESERVED_LAZADA_TAG_SLUGS:
        return False
    if adapter.is_verification_page(actual_url, ""):
        return False

    expected_query = _unique_query_value(expected, "q")
    actual_query = _unique_query_value(actual, "q")
    if not expected_query or actual_query != expected_query:
        return False
    if slug_tokens != _slug_tokens(expected_query):
        return False

    marker = _unique_query_value(actual, "catalog_redirect_tag")
    if marker != "true":
        return False
    expected_page = _unique_page_number(expected, 1)
    actual_page = _unique_page_number(actual, 1)
    return expected_page is not None and actual_page == expected_page


def _search_page_url_matches(adapter: Any, expected_url: str, actual_url: str) -> bool:
    """Confirm the attached tab reached the requested marketplace result page."""
    try:
        expected = urlparse(expected_url)
        actual = urlparse(actual_url)
    except ValueError:
        return False
    if adapter.platform == "lazada":
        if not _same_safe_lazada_origin(expected, actual):
            return False
    else:
        expected_host = (expected.hostname or "").lower().removeprefix("www.")
        actual_host = (actual.hostname or "").lower().removeprefix("www.")
        if not expected_host or actual_host != expected_host:
            return False
    expected_path = expected.path.rstrip("/").lower()
    actual_path = actual.path.rstrip("/").lower()
    exact_path = actual_path == expected_path
    if not exact_path:
        return adapter.platform == "lazada" and _lazada_tag_redirect_matches(
            adapter,
            expected,
            actual,
            actual_url,
        )

    if adapter.platform == "lazada":
        expected_query = _unique_query_value(expected, "q")
        actual_query = _unique_query_value(actual, "q")
        if not expected_query or actual_query != expected_query:
            return False
        expected_page = _unique_page_number(expected, 1)
        actual_page = _unique_page_number(actual, 1)
        if expected_page is None or actual_page != expected_page:
            return False
        redirect_markers = [
            value
            for key, value in parse_qsl(actual.query, keep_blank_values=True)
            if key.casefold() == "catalog_redirect_tag"
        ]
        return len(redirect_markers) <= 1

    query_name = "keyword" if adapter.platform == "shopee" else "q"
    if _normalized_query_value(actual, query_name) != _normalized_query_value(expected, query_name):
        return False

    default_page = 0 if adapter.platform == "shopee" else 1
    expected_page = _normalized_query_value(expected, "page") or str(default_page)
    actual_page = _normalized_query_value(actual, "page") or str(default_page)
    try:
        return int(actual_page) == int(expected_page)
    except ValueError:
        return False


def _raw_keys(cards: list[dict[str, Any]]) -> set[str]:
    return {raw_card_key(card) for card in cards if isinstance(card, dict)}


def _grid_is_stale(current_keys: set[str], previous_page_keys: set[str]) -> bool:
    # Virtualized grids may unmount most previous cards during navigation.  An old 20-card
    # viewport is therefore commonly only a subset of the prior page's 40-60 accumulated keys.
    # A genuinely new page can still repeat a few ads, but one rotating ad must not make a
    # 19/20-old grid look fresh.  Small exact subsets remain stale; larger grids use an 80%
    # overlap threshold.
    if not current_keys or not previous_page_keys:
        return False
    if current_keys.issubset(previous_page_keys):
        return True
    overlap = len(current_keys & previous_page_keys)
    return len(current_keys) >= 6 and overlap / len(current_keys) >= 0.80


def _run_lock_key(run_id: int, platform: str) -> str:
    return f"run:{int(run_id)}:{platform}"


def _card_grid_signature(
    cards: list[dict[str, Any]],
    accumulated_count: int,
    page_state: dict[str, Any],
) -> tuple[Any, ...]:
    normalized_cards = json.dumps(cards, ensure_ascii=False, sort_keys=True, default=str)
    return (
        accumulated_count,
        int(page_state.get("scrollHeight") or 0),
        int(page_state.get("scrollY") or 0),
        normalized_cards,
    )


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
    progress_start: int = 10,
    progress_end: int = 65,
) -> tuple[
    str,
    str,
    list[dict[str, Any]],
    list[MarketplaceListing],
    list[str],
    list[dict[str, Any]],
]:
    url = adapter.search_url(keyword)
    if settings.BROWSER_MODE == "extension":
        socket_context: Any = _ExtensionCDPSocket(
            adapter.platform,
            url,
            _run_lock_key(run_id, adapter.platform),
        )
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
    page_diagnostics: list[dict[str, Any]] = []
    previous_page_keys: set[str] = set()
    last_heartbeat = 0.0
    _require_lease(
        run_id,
        worker_id,
        progress=progress_start,
        current_step=f"正在唤醒已登录 Chrome · {adapter.platform.title()} Malaysia",
    )
    async with socket_context as socket:
        request_id = 1
        active_call_deadline: float | None = None

        async def call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            nonlocal request_id
            operation = _cdp_call(socket, request_id, method, params)
            if active_call_deadline is None:
                result = await operation
            else:
                remaining = active_call_deadline - time.monotonic()
                if remaining <= 0:
                    operation.close()
                    raise PageDeadlineExceeded("本页浏览器调用超过加载窗口")
                try:
                    result = await asyncio.wait_for(operation, timeout=remaining)
                except TimeoutError as exc:
                    raise PageDeadlineExceeded("本页浏览器调用超过加载窗口") from exc
            request_id += 1
            return result

        def heartbeat() -> None:
            nonlocal last_heartbeat
            now_mono = time.monotonic()
            if now_mono - last_heartbeat >= settings.RUN_HEARTBEAT_SECONDS:
                _require_lease(run_id, worker_id)
                last_heartbeat = now_mono

        async def observe_page() -> dict[str, Any]:
            state_result = await call(
                "Runtime.evaluate",
                {"expression": _PAGE_STATE_EXPRESSION, "returnByValue": True},
            )
            state = _runtime_value(state_result, {})
            return state if isinstance(state, dict) else {}

        async def extract_cards() -> list[dict[str, Any]]:
            result = await call(
                "Runtime.evaluate",
                {
                    "expression": f"({adapter.extraction_script})()",
                    "returnByValue": True,
                    "awaitPromise": True,
                },
            )
            value = _runtime_value(result, [])
            return [card for card in value if isinstance(card, dict)] if isinstance(value, list) else []

        heartbeat()
        await call("Page.enable")
        heartbeat()
        await call("Runtime.enable")
        heartbeat()
        await call("Page.bringToFront")
        heartbeat()

        # The timeout is per page. Each URL represents a real result page and must get a full
        # render/scroll opportunity; dividing one timeout across all pages made later pages race
        # the marketplaces' virtualized grids.
        page_window = max(
            8.0,
            min(float(settings.COLLECTION_TIMEOUT_SECONDS), _PAGE_MAX_SECONDS),
        )
        for page_number in range(1, pages + 1):
            page_url = adapter.search_url(keyword, page=page_number)
            page_raw = RawCardAccumulator(max_cards=max(100, limit * 4))
            page_started = time.monotonic()
            page_deadline = page_started + page_window
            active_call_deadline = page_deadline
            page_progress = progress_start + int(
                (page_number - 1) / pages * max(0, progress_end - progress_start)
            )
            page_state: dict[str, Any] = {}
            navigation_confirmed = False
            new_document_confirmed = False
            first_results_ready = False
            completion_reason = ""
            diagnostic: dict[str, Any] = {
                "page": page_number,
                "requested_url": page_url,
                "final_url": "",
                "elapsed_ms": 0,
                "navigation_confirmed": False,
                "new_document_confirmed": False,
                "first_results_ready": False,
                "dom_stable": False,
                "completion_reason": "",
                "raw_count": 0,
                "parsed_count": 0,
                "ready_state": "",
                "visibility_state": "",
            }

            def verification_result() -> tuple[
                str,
                str,
                list[dict[str, Any]],
                list[MarketplaceListing],
                list[str],
                list[dict[str, Any]],
            ]:
                diagnostic.update(
                    {
                        "final_url": current_url,
                        "elapsed_ms": round((time.monotonic() - page_started) * 1000),
                        "navigation_confirmed": navigation_confirmed,
                        "new_document_confirmed": new_document_confirmed,
                        "first_results_ready": first_results_ready,
                        "completion_reason": "verification_required",
                        "raw_count": len(page_raw),
                        "parsed_count": len(adapter.parse_cards(page_raw.cards(), limit)),
                        "ready_state": str(page_state.get("readyState") or ""),
                        "visibility_state": str(page_state.get("visibilityState") or ""),
                        "tab_id": str(getattr(socket, "tab_id", "") or ""),
                    }
                )
                page_diagnostics.append(diagnostic)
                preserve_lock = getattr(socket, "preserve_lock", None)
                if callable(preserve_lock):
                    preserve_lock()
                return (
                    current_url,
                    body_text,
                    all_raw.cards(),
                    listings,
                    page_warnings,
                    page_diagnostics,
                )

            try:
                _require_lease(
                    run_id,
                    worker_id,
                    progress=page_progress,
                    current_step=(
                        f"正在采集 {adapter.platform.title()} Malaysia · "
                        f"第 {page_number}/{pages} 页"
                    ),
                )
                await call("Page.bringToFront")
                document_nonce = f"{run_id}:{adapter.platform}:{page_number}:{uuid.uuid4().hex}"
                await call(
                    "Runtime.evaluate",
                    {
                        "expression": (
                            "window.__MY_MARKET_RADAR_DOCUMENT_NONCE__ = "
                            f"{json.dumps(document_nonce)}; true"
                        ),
                        "returnByValue": True,
                    },
                )
                await call("Page.navigate", {"url": page_url})
                await call("Page.bringToFront")

                # Phase 1: do not touch the old DOM until both the requested URL and a visible,
                # interactive document are confirmed.  This prevents page 1 cards being counted
                # again as page 2 while a background tab is still switching routes.
                navigation_budget = min(
                    _PAGE_NAVIGATION_MAX_SECONDS,
                    max(3.0, page_window * 0.35),
                )
                navigation_deadline = min(page_deadline, page_started + navigation_budget)
                while time.monotonic() < navigation_deadline:
                    heartbeat()
                    await asyncio.sleep(_PAGE_POLL_INTERVAL_SECONDS)
                    page_state = await observe_page()
                    current_url = str(page_state.get("href") or "")
                    body_text = str(page_state.get("bodyText") or "")
                    visible = page_state.get("visibilityState") == "visible"
                    ready = page_state.get("readyState") in {"interactive", "complete"}
                    new_document_confirmed = (
                        str(page_state.get("collectorDocumentNonce") or "")
                        != document_nonce
                    )
                    if (
                        visible
                        and ready
                        and new_document_confirmed
                        and _search_page_url_matches(adapter, page_url, current_url)
                    ):
                        navigation_confirmed = True
                        break
                    if not visible:
                        await call("Page.bringToFront")

                if not navigation_confirmed:
                    # A redirect to a real challenge never matches the requested search URL.
                    # Wait out the navigation budget first so a stale pre-navigation captcha DOM
                    # cannot immediately trap every resume attempt in needs_verification.
                    if adapter.is_verification_page(current_url, body_text):
                        return verification_result()
                    if current_url and not _search_page_url_matches(adapter, page_url, current_url):
                        raise RuntimeError(
                            f"页面跳转未确认，仍停留在 {current_url[:160]}"
                        )
                    if not new_document_confirmed:
                        raise RuntimeError("页面导航未提交新文档")
                    raise RuntimeError("页面未进入可见且可交互状态")

                await call(
                    "Runtime.evaluate",
                    {"expression": "window.scrollTo(0, 0); true", "returnByValue": True},
                )

                # Phase 2: wait for a parseable, fresh first grid.  When changing pages, an
                # identical card set is treated as the previous virtual list still being mounted
                # and is never added to this page's accumulator.
                first_budget = min(
                    _PAGE_FIRST_RESULTS_MAX_SECONDS,
                    max(3.0, page_window * 0.40),
                )
                first_deadline = min(page_deadline, time.monotonic() + first_budget)
                stale_grid_seen = False
                current_cards: list[dict[str, Any]] = []
                while time.monotonic() < first_deadline:
                    heartbeat()
                    await asyncio.sleep(_PAGE_POLL_INTERVAL_SECONDS)
                    page_state = await observe_page()
                    current_url = str(page_state.get("href") or current_url)
                    body_text = str(page_state.get("bodyText") or body_text)
                    if adapter.is_verification_page(current_url, body_text):
                        return verification_result()
                    if not _search_page_url_matches(adapter, page_url, current_url):
                        continue
                    if page_state.get("visibilityState") != "visible":
                        await call("Page.bringToFront")
                        continue
                    current_cards = await extract_cards()
                    current_keys = _raw_keys(current_cards)
                    if _grid_is_stale(current_keys, previous_page_keys):
                        stale_grid_seen = True
                        continue
                    candidate = RawCardAccumulator(max_cards=max(100, limit * 4))
                    candidate.add(current_cards)
                    if adapter.parse_cards(candidate.cards(), limit):
                        page_raw.add(current_cards)
                        first_results_ready = True
                        break

                if not first_results_ready:
                    if stale_grid_seen:
                        raise RuntimeError("商品网格未从上一页刷新")
                    raise RuntimeError("页面在限定时间内未挂载可解析商品网格")

                # Phase 3: scroll the virtual list and only finish after either the requested
                # count or the bottom-of-page DOM has remained unchanged for several polls.
                previous_signature: tuple[Any, ...] | None = None
                stable_rounds = 0
                while True:
                    heartbeat()
                    usable_count = len(adapter.parse_cards(page_raw.cards(), limit))
                    signature = _card_grid_signature(current_cards, len(page_raw), page_state)
                    stable_rounds = stable_rounds + 1 if signature == previous_signature else 0
                    previous_signature = signature
                    at_bottom = bool(page_state.get("atBottom"))
                    page_visible = page_state.get("visibilityState") == "visible"
                    if (
                        page_visible
                        and usable_count >= limit
                        and stable_rounds >= _PAGE_TARGET_STABLE_ROUNDS
                    ):
                        completion_reason = "target_count_stable"
                        break
                    if (
                        page_visible
                        and at_bottom
                        and usable_count > 0
                        and stable_rounds >= _PAGE_BOTTOM_STABLE_ROUNDS
                    ):
                        completion_reason = "bottom_dom_stable"
                        break
                    if time.monotonic() >= page_deadline:
                        completion_reason = "page_timeout_with_results"
                        page_warnings.append(
                            f"第 {page_number} 页加载未完全稳定，已保留 {usable_count} 条结果"
                        )
                        break

                    if usable_count < limit:
                        await call(
                            "Runtime.evaluate",
                            {
                                "expression": (
                                    "(() => { const before = window.scrollY; "
                                    "window.scrollBy(0, Math.max(window.innerHeight, 700)); "
                                    "return { moved: window.scrollY > before + 1 }; })()"
                                ),
                                "returnByValue": True,
                            },
                        )
                    await asyncio.sleep(_PAGE_POLL_INTERVAL_SECONDS)
                    page_state = await observe_page()
                    current_url = str(page_state.get("href") or current_url)
                    body_text = str(page_state.get("bodyText") or body_text)
                    if adapter.is_verification_page(current_url, body_text):
                        return verification_result()
                    if not _search_page_url_matches(adapter, page_url, current_url):
                        raise RuntimeError("采集过程中标签页离开了当前搜索页")
                    if page_state.get("visibilityState") != "visible":
                        await call("Page.bringToFront")
                        continue
                    current_cards = await extract_cards()
                    page_raw.add(current_cards)
            except WorkerLeaseLost:
                raise
            except Exception as exc:
                if isinstance(exc, PageDeadlineExceeded) and first_results_ready and len(page_raw):
                    completion_reason = "page_timeout_with_results"
                    page_warnings.append(
                        f"第 {page_number} 页加载未完全稳定，已保留 "
                        f"{len(adapter.parse_cards(page_raw.cards(), limit))} 条结果"
                    )
                else:
                    diagnostic.update(
                        {
                            "final_url": current_url,
                            "elapsed_ms": round((time.monotonic() - page_started) * 1000),
                            "navigation_confirmed": navigation_confirmed,
                            "new_document_confirmed": new_document_confirmed,
                            "first_results_ready": first_results_ready,
                            "completion_reason": "failed",
                            "raw_count": len(page_raw),
                            "parsed_count": len(adapter.parse_cards(page_raw.cards(), limit)),
                            "ready_state": str(page_state.get("readyState") or ""),
                            "visibility_state": str(page_state.get("visibilityState") or ""),
                            "error": str(exc)[:180],
                        }
                    )
                    page_diagnostics.append(diagnostic)
                    if not listings:
                        raise PageCollectionError(str(exc), list(page_diagnostics)) from exc
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
            diagnostic.update(
                {
                    "final_url": current_url,
                    "elapsed_ms": round((time.monotonic() - page_started) * 1000),
                    "navigation_confirmed": navigation_confirmed,
                    "new_document_confirmed": new_document_confirmed,
                    "first_results_ready": first_results_ready,
                    "dom_stable": completion_reason in {
                        "target_count_stable",
                        "bottom_dom_stable",
                    },
                    "completion_reason": completion_reason,
                    "raw_count": len(page_cards),
                    "parsed_count": len(page_listings),
                    "ready_state": str(page_state.get("readyState") or ""),
                    "visibility_state": str(page_state.get("visibilityState") or ""),
                }
            )
            page_diagnostics.append(diagnostic)
            if not page_listings:
                page_warnings.append(f"第 {page_number} 页没有采集到可解析商品")
                continue
            previous_page_keys = _raw_keys(page_cards)
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

            completed_progress = progress_start + int(
                page_number / pages * max(0, progress_end - progress_start)
            )
            _require_lease(run_id, worker_id, progress=completed_progress)

    _require_lease(run_id, worker_id)
    return current_url, body_text, all_raw.cards(), listings, page_warnings, page_diagnostics


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
            platform_progress_start = 10 + int(index / len(platforms) * 55)
            platform_progress_end = 10 + int((index + 1) / len(platforms) * 55)
            (
                current_url,
                body,
                raw_cards,
                listings,
                page_warnings,
                page_diagnostics,
            ) = await _collect_resident_tab(
                adapter,
                keyword.keyword,
                keyword.results_limit,
                keyword.search_pages,
                run_id,
                worker_id,
                progress_start=platform_progress_start,
                progress_end=platform_progress_end,
            )
            if adapter.is_verification_page(current_url, body):
                if listings:
                    _persist_platform(run_id, keyword.id, platform, listings, worker_id)
                raise VerificationRequired(
                    platform,
                    current_url,
                    context={
                        "platform": platform,
                        "url": current_url,
                        "lock_key": _run_lock_key(run_id, platform),
                        "tab_id": next(
                            (
                                str(row.get("tab_id") or "")
                                for row in reversed(page_diagnostics)
                                if row.get("tab_id")
                            ),
                            "",
                        ),
                        "preserved_listing_count": len(listings),
                        "raw_count": len(raw_cards),
                        "warnings": list(page_warnings),
                        "page_diagnostics": list(page_diagnostics),
                    },
                )

            target_limit = keyword.results_limit * keyword.search_pages
            health = assess_collection_health(raw_cards, listings, target_limit)
            health["page_diagnostics"] = page_diagnostics
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
            elif health["status"] == "unhealthy" or any(
                "页面结构变化" in warning for warning in (health.get("warnings") or [])
            ):
                platform_errors.append("采集器健康度异常，页面结构可能已经变化")
            if platform_errors:
                errors[platform] = "；".join(platform_errors)
            results[platform] = listings
            _persist_platform(run_id, keyword.id, platform, listings, worker_id)
            # Extension 1.0.2 stops polling shortly after the last debugger session detaches.
            # Queue the next platform attach immediately while its worker is still awake.
            if settings.BROWSER_MODE != "extension":
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
            health["page_diagnostics"] = list(
                getattr(exc, "page_diagnostics", []) or []
            )
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
        run = (
            db.query(AnalysisRun)
            .filter(
                AnalysisRun.id == run_id,
                AnalysisRun.worker_id == worker_id,
                AnalysisRun.status == "running",
            )
            .first()
        )
        if not run:
            db.rollback()
            return
        run.status = "needs_verification"
        run.current_step = f"{exc.platform.title()} 需要人工验证"
        run.verification_platform = exc.platform
        run.error_message = (
            f"{exc.platform.title()} 触发了人工验证。请在你的 Google Chrome "
            "完成验证，保持该标签页打开，然后点击继续。"
        )
        run.worker_id = None
        run.heartbeat_at = None
        run.analysis = {
            **(run.analysis or {}),
            "verification_context": dict(exc.context or {}),
        }
        db.commit()
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
        run.progress = max(int(run.progress or 0), 5)
        run.current_step = (
            "连接你已登录的 Google Chrome"
            if settings.BROWSER_MODE == "extension"
            else "准备采集浏览器"
        )
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
            run.current_step = (
                "分析完成 · 部分数据不完整"
                if expected == successful
                else "部分平台无结果"
            )
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
        verification_context = (
            (run.analysis or {}).get("verification_context")
            if isinstance(run.analysis, dict)
            else {}
        )
        verification_context = (
            verification_context
            if isinstance(verification_context, dict)
            and verification_context.get("platform") == platform
            else {}
        )
        lock_key = str(
            verification_context.get("lock_key") or _run_lock_key(run.id, platform)
        )
        locked_tab_id = str(verification_context.get("tab_id") or "")
    finally:
        db.close()

    if settings.BROWSER_MODE != "extension" and not _visible_desktop_available():
        raise RuntimeError("当前运行环境没有可见桌面，无法人工处理验证码。请在 Windows/macOS/Linux 桌面本机运行后继续。")

    if not browser_ready():
        ensure_browser([url])
    exact_tab = find_platform_tab_by_id(platform, locked_tab_id)
    if exact_tab and activate_tab(str(exact_tab.get("id") or "")):
        return str(exact_tab.get("url") or url)
    locked_tab = activate_locked_platform_tab(platform, lock_key)
    if locked_tab:
        return str(locked_tab.get("url") or url)
    tab = find_platform_tab(platform)
    if not tab:
        tab = ensure_platform_tab(platform, url)
    if not activate_tab(str(tab.get("id") or "")):
        if settings.BROWSER_MODE == "extension":
            raise RuntimeError("无法激活你已登录的 Google Chrome 验证标签页")
        raise RuntimeError("采集浏览器已启动，但无法激活验证标签页")
    return str(tab.get("url") or url)
