from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote as urlquote

from sqlalchemy.orm import Session
from websockets.asyncio.client import connect as websocket_connect

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.marketplace import AnalysisRun, ListingSnapshot, TrackedKeyword
from app.services.marketplace.adapters import ADAPTERS, MarketplaceListing, VerificationRequired
from app.services.marketplace.scoring import build_analysis

settings = get_settings()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="marketplace-collector")
_VERIFICATION_CDP_URL = "http://127.0.0.1:9223"


def _verification_browser_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{_VERIFICATION_CDP_URL}/json/version", timeout=0.5) as response:
            return response.status == 200
    except Exception:
        return False


def _activate_verification_tab(platform: str) -> bool:
    """Bring the platform's resident tab forward without creating duplicates."""
    if not _verification_browser_ready():
        return False


def _resident_tab(platform: str, create_url: str | None = None) -> dict[str, Any] | None:
    markers = ("shopee", "xiapibuy") if platform == "shopee" else ("lazada",)
    try:
        with urllib.request.urlopen(f"{_VERIFICATION_CDP_URL}/json/list", timeout=1) as response:
            tabs = json.load(response)
        tab = next(
            (item for item in tabs if item.get("type") == "page" and any(marker in item.get("url", "").lower() for marker in markers)),
            None,
        )
        if tab or not create_url:
            return tab
        request = urllib.request.Request(
            f"{_VERIFICATION_CDP_URL}/json/new?{urlquote(create_url, safe='')}",
            method="PUT",
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return json.load(response)
    except Exception:
        return None


async def _cdp_call(socket: Any, request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    await socket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
    while True:
        message = json.loads(await asyncio.wait_for(socket.recv(), timeout=settings.COLLECTION_TIMEOUT_SECONDS))
        if message.get("id") != request_id:
            continue
        if "error" in message:
            raise RuntimeError(message["error"].get("message", f"Chrome 调试调用失败: {method}"))
        return message.get("result", {})


async def _collect_resident_tab(adapter: Any, keyword: str) -> tuple[str, str, list[dict[str, Any]]]:
    url = adapter.search_url(keyword)
    tab = _resident_tab(adapter.platform, url)
    if not tab or not tab.get("webSocketDebuggerUrl"):
        raise VerificationRequired("browser", "")
    async with websocket_connect(tab["webSocketDebuggerUrl"], open_timeout=5, max_size=8 * 1024 * 1024) as socket:
        request_id = 1
        await _cdp_call(socket, request_id, "Page.enable")
        request_id += 1
        await _cdp_call(socket, request_id, "Runtime.enable")
        request_id += 1
        await _cdp_call(socket, request_id, "Page.navigate", {"url": url})
        await asyncio.sleep(5)

        request_id += 1
        location = await _cdp_call(
            socket,
            request_id,
            "Runtime.evaluate",
            {"expression": "location.href", "returnByValue": True},
        )
        request_id += 1
        body = await _cdp_call(
            socket,
            request_id,
            "Runtime.evaluate",
            {"expression": "document.body ? document.body.innerText : ''", "returnByValue": True},
        )
        request_id += 1
        cards = await _cdp_call(
            socket,
            request_id,
            "Runtime.evaluate",
            {"expression": f"({adapter.extraction_script})()", "returnByValue": True, "awaitPromise": True},
        )
    current_url = location.get("result", {}).get("value", url)
    body_text = body.get("result", {}).get("value", "")
    raw_cards = cards.get("result", {}).get("value", [])
    return current_url, body_text, raw_cards if isinstance(raw_cards, list) else []
    markers = ("shopee", "xiapibuy") if platform == "shopee" else ("lazada",)
    try:
        with urllib.request.urlopen(f"{_VERIFICATION_CDP_URL}/json/list", timeout=1) as response:
            tabs = json.load(response)
        tab = next(
            (item for item in tabs if item.get("type") == "page" and any(marker in item.get("url", "").lower() for marker in markers)),
            None,
        )
        if not tab:
            return False
        with urllib.request.urlopen(f"{_VERIFICATION_CDP_URL}/json/activate/{tab['id']}", timeout=1):
            return True
    except Exception:
        return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_run(db: Session, keyword: TrackedKeyword, trigger: str = "manual") -> AnalysisRun:
    running = (
        db.query(AnalysisRun)
        .filter(
            AnalysisRun.keyword_id == keyword.id,
            AnalysisRun.status.in_(("pending", "running", "needs_verification")),
        )
        .order_by(AnalysisRun.id.desc())
        .first()
    )
    if running:
        return running
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


def submit_run(run_id: int) -> None:
    _executor.submit(execute_run_sync, run_id)


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


def _persist_platform(
    run_id: int, keyword_id: int, platform: str, listings: list[MarketplaceListing]
) -> None:
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


async def _collect(
    keyword: TrackedKeyword, run_id: int
) -> tuple[dict[str, list[MarketplaceListing]], dict[str, str]]:
    results: dict[str, list[MarketplaceListing]] = {}
    errors: dict[str, str] = {}
    platforms = [p for p in (keyword.platforms or []) if p in ADAPTERS]
    if not platforms:
        raise RuntimeError("未选择有效平台")

    if not _verification_browser_ready():
        raise VerificationRequired("browser", "")
    for index, platform in enumerate(platforms):
        adapter = ADAPTERS[platform]
        progress = 10 + int(index / len(platforms) * 55)
        _save_state(run_id, progress=progress, current_step=f"正在采集 {platform.title()} Malaysia")
        try:
            current_url, body, raw_cards = await _collect_resident_tab(adapter, keyword.keyword)
            if adapter.is_verification_page(current_url, body):
                raise VerificationRequired(platform, current_url)

            listings = adapter.parse_cards(raw_cards, keyword.results_limit)
            if raw_cards and not listings:
                raise VerificationRequired(platform, current_url)
            results[platform] = listings
            _persist_platform(run_id, keyword.id, platform, listings)
            if not listings:
                errors[platform] = "公开搜索页没有返回可解析商品"
            await asyncio.sleep(2)
        except VerificationRequired:
            raise
        except Exception as exc:
            logger.warning("%s 采集失败: %s", platform, exc)
            results[platform] = []
            errors[platform] = str(exc)[:500]
    return results, errors


def _persist_results(db: Session, run: AnalysisRun, keyword: TrackedKeyword, collected: dict[str, list[MarketplaceListing]]) -> None:
    db.query(ListingSnapshot).filter(ListingSnapshot.run_id == run.id).delete(synchronize_session=False)
    for platform, listings in collected.items():
        for listing in listings:
            db.add(_snapshot(run.id, keyword.id, platform, listing))


def execute_run_sync(run_id: int) -> None:
    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            return
        keyword = db.query(TrackedKeyword).filter(TrackedKeyword.id == run.keyword_id).first()
        if not keyword:
            return
        run.status = "running"
        run.progress = 5
        run.current_step = "启动独立采集浏览器"
        run.started_at = _utcnow()
        run.error_message = None
        run.verification_platform = None
        db.commit()

        db.refresh(keyword)
        collection_request = SimpleNamespace(
            id=keyword.id,
            keyword=keyword.keyword,
            platforms=list(keyword.platforms or []),
            results_limit=keyword.results_limit,
        )
        current_run_id = run.id
        db.close()
        collected, collection_errors = asyncio.run(_collect(collection_request, current_run_id))
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        keyword = db.query(TrackedKeyword).filter(TrackedKeyword.id == run.keyword_id).first()
        _persist_results(db, run, keyword, collected)
        db.flush()

        run.progress = 75
        run.current_step = "计算公开数据机会分"
        by_platform = {
            platform: [listing.to_dict() for listing in listings]
            for platform, listings in collected.items()
        }
        analysis = build_analysis(keyword.keyword, by_platform)
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
        if collection_errors:
            run.error_message = "；".join(f"{name.title()}: {message}" for name, message in collection_errors.items())
        run.completed_at = _utcnow()
        keyword.last_run_at = _utcnow()
        if successful:
            keyword.last_success_at = _utcnow()
        db.commit()
    except VerificationRequired as exc:
        db.rollback()
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if run:
            run.status = "needs_verification"
            run.current_step = "需要人工验证"
            run.verification_platform = exc.platform
            run.error_message = f"{exc.platform.title()} 需要人工验证。打开独立浏览器完成验证，保持窗口打开，然后点击继续。"
            db.commit()
    except Exception as exc:
        logger.error("采集任务失败 run=%s: %s", run_id, exc, exc_info=True)
        db.rollback()
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if run:
            run.status = "failed"
            run.current_step = "采集失败"
            run.error_message = str(exc)[:1000]
            run.completed_at = _utcnow()
            db.commit()
    finally:
        db.close()


async def execute_run_async(run_id: int) -> None:
    await asyncio.to_thread(execute_run_sync, run_id)


def open_verification_browser(run_id: int) -> str:
    db = SessionLocal()
    try:
        run = db.query(AnalysisRun).filter(AnalysisRun.id == run_id).first()
        if not run:
            raise ValueError("任务不存在")
        keyword = db.query(TrackedKeyword).filter(TrackedKeyword.id == run.keyword_id).first()
        platform = run.verification_platform if run.verification_platform in ADAPTERS else "shopee"
        url = ADAPTERS[platform].search_url(keyword.keyword)
    finally:
        db.close()

    if _activate_verification_tab(platform):
        return url

    executable = next(
        (path for path in (shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser")) if path),
        None,
    )
    if not executable:
        raise RuntimeError("未找到可见 Chrome/Chromium")
    browser_env = os.environ.copy()
    if not browser_env.get("DISPLAY"):
        raise RuntimeError("未连接到本机桌面，无法打开人工验证窗口")
    process = subprocess.Popen(
        [
            executable,
            f"--user-data-dir={settings.browser_profile_path}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=9223",
            "--no-first-run",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=browser_env,
    )
    for _ in range(20):
        if _verification_browser_ready():
            return url
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"验证浏览器启动失败：退出码 {return_code}")
        time.sleep(0.1)
    raise RuntimeError("验证浏览器已启动，但无法建立采集连接；请关闭旧验证窗口后重试")
