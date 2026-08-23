import threading
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import marketplace as marketplace_api
from app.core.database import Base
from app.models.marketplace import AnalysisRun, ListingSnapshot, TrackedKeyword
from app.models.user import User
from app.services.marketplace import runner


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))
        return Mock()


def shopee_card(
    item_id: str,
    title: str,
    *,
    sold: str | None = "100 sold",
    reviews: str | None = None,
) -> dict:
    lines = [title, "RM 9.90"]
    if sold:
        lines.append(sold)
    if reviews:
        lines.append(reviews)
    card = {
        "href": f"https://shopee.com.my/{title.replace(' ', '-')}-i.1001.{item_id}",
        "item_id": item_id,
        "shop_id": "1001",
        "title": title,
        "text": "\n".join(lines),
        "price": "RM 9.90",
    }
    if sold:
        card["sold"] = sold
    if reviews:
        card["reviews"] = reviews
    return card


class RunnerStateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        db = self.Session()
        self.user = User(username="runner-user", email="runner@example.com", password_hash="x")
        db.add(self.user)
        db.flush()
        self.keyword = TrackedKeyword(
            user_id=self.user.id,
            keyword="water bottle",
            platforms=["shopee", "lazada"],
            results_limit=20,
        )
        db.add(self.keyword)
        db.commit()
        db.refresh(self.user)
        db.refresh(self.keyword)
        self.user_id = self.user.id
        self.keyword_id = self.keyword.id
        db.close()
        runner._queued_run_ids.clear()
        runner._requeue_requested.clear()

    def tearDown(self):
        runner._queued_run_ids.clear()
        runner._requeue_requested.clear()
        self.engine.dispose()

    def test_run_captures_request_settings_at_creation(self):
        db = self.Session()
        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        run = runner.create_run(db, keyword)
        self.assertEqual(
            run.analysis["request_config"],
            {
                "keyword": "water bottle",
                "marketplace_query": "water bottle",
                "platforms": ["shopee", "lazada"],
                "results_limit": 20,
                "search_pages": 3,
                "max_results_per_platform": 60,
            },
        )

        keyword.platforms = ["shopee"]
        keyword.results_limit = 40
        db.commit()
        request = runner._collection_request(run, keyword)
        self.assertEqual(request.platforms, ["shopee", "lazada"])
        self.assertEqual(request.results_limit, 20)
        self.assertEqual(request.search_pages, 3)
        self.assertEqual(request.request_config["marketplace_query"], "water bottle")
        self.assertEqual(request.request_config["search_pages"], 3)
        self.assertEqual(request.request_config["max_results_per_platform"], 60)
        db.close()

    def test_concurrent_create_run_returns_one_active_attempt(self):
        barrier = threading.Barrier(6)
        run_ids: list[int] = []
        errors: list[Exception] = []
        result_lock = threading.Lock()

        def worker():
            db = self.Session()
            try:
                barrier.wait(timeout=5)
                run = runner.create_run(db, self.keyword)
                with result_lock:
                    run_ids.append(run.id)
            except Exception as exc:  # pragma: no cover - assertion below reports details
                with result_lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(errors, errors)
        self.assertEqual(len(run_ids), 6)
        self.assertEqual(len(set(run_ids)), 1)
        db = self.Session()
        self.assertEqual(db.query(AnalysisRun).filter_by(keyword_id=self.keyword_id).count(), 1)
        db.close()

    def test_submit_during_finishing_worker_is_requeued_not_lost(self):
        db = self.Session()
        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        run = runner.create_run(db, keyword)
        run_id = run.id
        db.close()

        fake_executor = FakeExecutor()
        with patch.object(runner, "SessionLocal", self.Session), patch.object(runner, "_executor", fake_executor):
            self.assertTrue(runner.submit_run(run_id))
            self.assertEqual(len(fake_executor.calls), 1)
            self.assertTrue(runner.submit_run(run_id))
            self.assertIn(run_id, runner._requeue_requested)
            self.assertEqual(len(fake_executor.calls), 1)

            with patch.object(runner, "execute_run_sync", lambda _run_id: None):
                runner._execute_queued(run_id)

            self.assertEqual(len(fake_executor.calls), 2)
            self.assertNotIn(run_id, runner._requeue_requested)
            self.assertIn(run_id, runner._queued_run_ids)

    def test_all_platforms_without_items_is_failed_not_partial(self):
        db = self.Session()
        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        run = runner.create_run(db, keyword)
        run_id = run.id
        db.close()

        def empty_health():
            return {
                "status": "empty",
                "health_score": 15.0,
                "raw_count": 0,
                "parsed_count": 0,
                "target_limit": 20,
                "parse_ratio": 0.0,
                "coverage": {
                    "price": 0.0,
                    "sold_count": 0.0,
                    "review_count": 0.0,
                    "rating": 0.0,
                    "seller_identity": 0.0,
                },
                "warnings": ["可解析商品样本偏少"],
            }

        async def empty_collect(_request, _run_id, _worker_id):
            return (
                {"shopee": [], "lazada": []},
                {
                    "shopee": "公开搜索页没有返回可解析商品",
                    "lazada": "公开搜索页没有返回可解析商品",
                },
                {"shopee": empty_health(), "lazada": empty_health()},
            )

        with patch.object(runner, "SessionLocal", self.Session), patch.object(runner, "_collect", empty_collect):
            runner.execute_run_sync(run_id)

        db = self.Session()
        completed = db.query(AnalysisRun).filter_by(id=run_id).one()
        self.assertEqual(completed.status, "failed")
        self.assertEqual(completed.current_step, "未采集到有效商品")
        self.assertIsNone(completed.opportunity_score)
        self.assertIn("Shopee", completed.error_message)
        self.assertEqual(completed.analysis["evidence"]["grade"], "D")
        self.assertIsNone(completed.worker_id)
        self.assertIsNone(completed.heartbeat_at)
        db.close()

    def test_partial_retry_creates_new_run_and_preserves_old_attempt(self):
        db = self.Session()
        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        old = AnalysisRun(
            keyword_id=keyword.id,
            status="partial",
            progress=100,
            opportunity_score=52.0,
            verdict="谨慎观察",
            analysis={"counts": {"shopee": 10, "lazada": 0}},
        )
        db.add(old)
        db.commit()
        db.refresh(old)
        old_id = old.id
        user = db.query(User).filter_by(id=self.user_id).one()

        with patch.object(marketplace_api, "submit_run", return_value=True):
            response = marketplace_api.resume_run(old_id, db=db, current_user=user)

        self.assertNotEqual(response["run"]["id"], old_id)
        self.assertEqual(response["retry_of"], old_id)
        old_after = db.query(AnalysisRun).filter_by(id=old_id).one()
        self.assertEqual(old_after.status, "partial")
        self.assertEqual(old_after.opportunity_score, 52.0)
        retry = db.query(AnalysisRun).filter_by(id=response["run"]["id"]).one()
        self.assertEqual(retry.status, "pending")
        self.assertEqual(retry.trigger, "retry")
        db.close()

    def test_keyword_payload_keeps_last_result_while_new_run_is_pending(self):
        db = self.Session()
        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        stable = AnalysisRun(
            keyword_id=keyword.id,
            status="completed",
            progress=100,
            opportunity_score=68.0,
            verdict="谨慎观察",
        )
        db.add(stable)
        db.commit()
        pending = AnalysisRun(keyword_id=keyword.id, status="pending", progress=0)
        db.add(pending)
        db.commit()
        user = db.query(User).filter_by(id=self.user_id).one()

        payload = marketplace_api.list_keywords(db=db, current_user=user)["data"][0]
        self.assertEqual(payload["latest_run"]["id"], pending.id)
        self.assertEqual(payload["latest_run"]["status"], "pending")
        self.assertEqual(payload["latest_result_run"]["id"], stable.id)
        self.assertEqual(payload["latest_result_run"]["opportunity_score"], 68.0)
        self.assertEqual(payload["marketplace_query"], "water bottle")
        self.assertEqual(payload["search_pages"], 3)
        self.assertEqual(payload["max_results_per_platform"], 60)
        db.close()

    def test_items_api_exposes_page_provenance(self):
        db = self.Session()
        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        run = AnalysisRun(keyword_id=keyword.id, status="completed", progress=100)
        db.add(run)
        db.flush()
        db.add(ListingSnapshot(
            run_id=run.id,
            keyword_id=keyword.id,
            platform="shopee",
            item_id="paged-item",
            title="Nail Sticker",
            product_url="https://shopee.com.my/nail-sticker-i.1001.5001",
            search_rank=24,
            raw_data={"search_page": 2, "page_rank": 4, "page_size": 20},
        ))
        db.commit()
        user = db.query(User).filter_by(id=self.user_id).one()

        item = marketplace_api.get_items(run.id, platform=None, db=db, current_user=user)["data"][0]

        self.assertEqual(item["search_rank"], 24)
        self.assertEqual(item["search_page"], 2)
        self.assertEqual(item["page_rank"], 4)
        self.assertEqual(item["page_size"], 20)
        db.close()

    def test_page_warning_prevents_completed_final_status(self):
        db = self.Session()
        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        run = runner.create_run(db, keyword)
        run_id = run.id
        db.close()

        shopee = runner.ADAPTERS["shopee"].parse_card(shopee_card("5002", "Water Bottle"), 1)
        lazada = runner.MarketplaceListing(
            platform="lazada",
            item_id="5003",
            title="Water Bottle",
            product_url="https://www.lazada.com.my/products/water-bottle-i5003-s.html",
            search_rank=1,
            price=9.9,
            sold_count=100,
            review_count=20,
        )
        health = {
            "status": "degraded",
            "health_score": 60.0,
            "raw_count": 1,
            "parsed_count": 1,
            "target_limit": 60,
            "parse_ratio": 100.0,
            "coverage": {
                "price": 100.0,
                "sold_count": 100.0,
                "review_count": 100.0,
                "rating": 0.0,
                "seller_identity": 0.0,
            },
            "warnings": ["第 2 页没有采集到可解析商品"],
        }

        async def partial_collect(_request, _run_id, _worker_id):
            return (
                {"shopee": [shopee], "lazada": [lazada]},
                {"shopee": "部分页面未完整采集：第 2 页没有采集到可解析商品"},
                {"shopee": health, "lazada": {**health, "warnings": []}},
            )

        with patch.object(runner, "SessionLocal", self.Session), patch.object(runner, "_collect", partial_collect):
            runner.execute_run_sync(run_id)

        db = self.Session()
        finished = db.query(AnalysisRun).filter_by(id=run_id).one()
        self.assertEqual(finished.status, "partial")
        self.assertNotEqual(finished.status, "completed")
        self.assertIn("部分页面未完整采集", finished.error_message)
        self.assertIn("shopee", finished.analysis["platform_errors"])
        db.close()

    def test_pending_keyword_cannot_be_deleted_under_worker(self):
        db = self.Session()
        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        db.add(AnalysisRun(keyword_id=keyword.id, status="pending", progress=0))
        db.commit()
        user = db.query(User).filter_by(id=self.user_id).one()

        with self.assertRaises(HTTPException) as raised:
            marketplace_api.delete_keyword(keyword.id, db=db, current_user=user)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIsNotNone(db.query(TrackedKeyword).filter_by(id=keyword.id).first())
        db.close()


class RunnerPageCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_collect_promotes_page_warning_to_platform_error(self):
        adapter = runner.ADAPTERS["shopee"]
        raw = shopee_card("299", "Nail Sticker")
        listing = adapter.parse_card(raw, 1)
        keyword = Mock(
            id=9,
            keyword="指甲贴",
            platforms=["shopee"],
            results_limit=2,
            search_pages=3,
        )
        warning = "第 2 页没有采集到可解析商品"

        async def resident_result(*_args, **_kwargs):
            return adapter.search_url("指甲贴", page=3), "results", [raw], [listing], [warning]

        async def no_sleep(_seconds):
            return None

        with (
            patch.object(runner, "ensure_browser"),
            patch.object(runner, "_require_lease"),
            patch.object(runner, "_collect_resident_tab", resident_result),
            patch.object(runner, "_persist_platform"),
            patch.object(runner.asyncio, "sleep", no_sleep),
        ):
            results, errors, health = await runner._collect(keyword, run_id=79, worker_id="worker")

        self.assertEqual(results["shopee"], [listing])
        self.assertIn("部分页面未完整采集", errors["shopee"])
        self.assertIn(warning, errors["shopee"])
        self.assertIn(warning, health["shopee"]["warnings"])
        self.assertNotEqual(health["shopee"]["status"], "healthy")

    async def test_one_extension_attach_navigates_three_pages_and_keeps_page_provenance(self):
        adapter = runner.ADAPTERS["shopee"]
        page_cards = {
            0: [shopee_card("100", "Nail Sticker A"), shopee_card("101", "Nail Sticker B", sold=None)],
            1: [
                shopee_card(
                    "101",
                    "Premium Nail Sticker B Full Cover",
                    sold="250 sold",
                    reviews="36 reviews",
                ),
                shopee_card("102", "Nail Sticker C"),
            ],
            2: [shopee_card("103", "Nail Sticker D"), shopee_card("104", "Nail Sticker E")],
        }
        attachments: list[tuple[str, str]] = []
        navigations: list[str] = []
        state = {"page_index": 0, "url": ""}

        class FakeExtensionSocket:
            def __init__(self, platform: str, url: str):
                attachments.append((platform, url))

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return None

        async def fake_cdp_call(_socket, _request_id, method, params=None):
            params = params or {}
            if method in {"Page.enable", "Runtime.enable", "Page.bringToFront"}:
                return {}
            if method == "Page.navigate":
                state["url"] = params["url"]
                state["page_index"] = len(navigations)
                navigations.append(params["url"])
                return {}
            if method != "Runtime.evaluate":
                self.fail(f"unexpected CDP method: {method}")

            expression = params.get("expression", "")
            if expression == "location.href":
                return {"result": {"value": state["url"]}}
            if expression == "document.body ? document.body.innerText : ''":
                return {"result": {"value": "Nail Sticker results"}}
            if expression == "window.scrollTo(0, 0); true":
                return {"result": {"value": True}}
            if "document.querySelectorAll" in expression:
                return {"result": {"value": page_cards[state["page_index"]]}}
            self.fail(f"unexpected Runtime.evaluate expression: {expression}")

        async def no_sleep(_seconds):
            return None

        with (
            patch.object(runner.settings, "BROWSER_MODE", "extension"),
            patch.object(runner, "_ExtensionCDPSocket", FakeExtensionSocket),
            patch.object(runner, "_cdp_call", fake_cdp_call),
            patch.object(runner, "_require_lease"),
            patch.object(runner.asyncio, "sleep", no_sleep),
        ):
            current_url, _body, raw_cards, listings, warnings = await runner._collect_resident_tab(
                adapter,
                "指甲贴",
                limit=2,
                search_pages=3,
                run_id=77,
                worker_id="test-worker",
            )

        expected_urls = [adapter.search_url("指甲贴", page=page) for page in (1, 2, 3)]
        self.assertEqual(attachments, [("shopee", expected_urls[0])])
        self.assertEqual(navigations, expected_urls)
        self.assertEqual(current_url, expected_urls[-1])
        self.assertEqual(len(raw_cards), 5)
        self.assertEqual([listing.item_id for listing in listings], ["100", "101", "102", "103", "104"])
        self.assertEqual([listing.search_rank for listing in listings], [1, 2, 4, 5, 6])
        self.assertEqual(
            [
                (
                    listing.raw_data["search_page"],
                    listing.raw_data["page_rank"],
                    listing.raw_data["page_size"],
                )
                for listing in listings
            ],
            [(1, 1, 2), (1, 2, 2), (2, 2, 2), (3, 1, 2), (3, 2, 2)],
        )
        enriched = listings[1]
        self.assertEqual(enriched.title, "Premium Nail Sticker B Full Cover")
        self.assertEqual(enriched.sold_count, 250)
        self.assertEqual(enriched.review_count, 36)
        self.assertEqual(enriched.search_rank, 2)
        self.assertEqual(enriched.raw_data["search_page"], 1)
        self.assertEqual(enriched.raw_data["page_rank"], 2)
        self.assertEqual(warnings, [])

    async def test_second_page_exception_returns_warning_and_keeps_other_pages(self):
        adapter = runner.ADAPTERS["shopee"]
        page_cards = {
            0: [shopee_card("200", "Nail Sticker A"), shopee_card("201", "Nail Sticker B")],
            2: [shopee_card("203", "Nail Sticker D"), shopee_card("204", "Nail Sticker E")],
        }
        navigations: list[str] = []
        state = {"page_index": 0, "url": ""}

        class FakeExtensionSocket:
            def __init__(self, _platform: str, _url: str):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return None

        async def fake_cdp_call(_socket, _request_id, method, params=None):
            params = params or {}
            if method in {"Page.enable", "Runtime.enable", "Page.bringToFront"}:
                return {}
            if method == "Page.navigate":
                state["url"] = params["url"]
                state["page_index"] = len(navigations)
                navigations.append(params["url"])
                return {}
            expression = params.get("expression", "")
            if expression == "location.href":
                return {"result": {"value": state["url"]}}
            if expression == "document.body ? document.body.innerText : ''":
                return {"result": {"value": "Nail Sticker results"}}
            if expression == "window.scrollTo(0, 0); true":
                return {"result": {"value": True}}
            if "document.querySelectorAll" in expression:
                if state["page_index"] == 1:
                    raise RuntimeError("page two extraction failed")
                return {"result": {"value": page_cards[state["page_index"]]}}
            self.fail(f"unexpected CDP call: {method} {expression}")

        async def no_sleep(_seconds):
            return None

        with (
            patch.object(runner.settings, "BROWSER_MODE", "extension"),
            patch.object(runner, "_ExtensionCDPSocket", FakeExtensionSocket),
            patch.object(runner, "_cdp_call", fake_cdp_call),
            patch.object(runner, "_require_lease"),
            patch.object(runner.asyncio, "sleep", no_sleep),
        ):
            _url, _body, raw_cards, listings, warnings = await runner._collect_resident_tab(
                adapter,
                "指甲贴",
                limit=2,
                search_pages=3,
                run_id=78,
                worker_id="test-worker",
            )

        self.assertEqual(
            navigations,
            [adapter.search_url("指甲贴", page=page) for page in (1, 2, 3)],
        )
        self.assertEqual(len(raw_cards), 4)
        self.assertEqual([listing.item_id for listing in listings], ["200", "201", "203", "204"])
        self.assertEqual([listing.search_rank for listing in listings], [1, 2, 5, 6])
        self.assertEqual(len(warnings), 1)
        self.assertIn("第 2 页采集失败", warnings[0])
        self.assertIn("page two extraction failed", warnings[0])


if __name__ == "__main__":
    unittest.main()
