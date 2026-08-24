import json
import threading
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import marketplace as marketplace_api
from app.core.database import Base
from app.models.marketplace import AnalysisRun, ListingSnapshot, TrackedKeyword
from app.models.user import User
from app.services.marketplace import runner
from app.services.marketplace.extension_bridge import ExtensionBridgeError


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
        self.ai_status_patch = patch.object(runner, "ai_status", return_value={"enabled": False})
        self.ai_status_patch.start()

    def tearDown(self):
        self.ai_status_patch.stop()
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
                "relevance_phrases": ["water bottle"],
                "localization": None,
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

    def test_ai_localization_is_cached_and_frozen_into_run_request(self):
        db = self.Session()
        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        run = runner.create_run(db, keyword)
        run.status = "running"
        run.worker_id = "worker-ai-test"
        db.commit()
        run_id = run.id
        db.close()

        localization = {
            "keyword": "water bottle",
            "search_term": "water bottle",
            "aliases": ["water bottle", "botol air"],
            "source": "ai",
            "model": "gpt-5.6-sol",
        }
        with patch.object(runner, "SessionLocal", self.Session), patch.object(
            runner, "ai_status", return_value={"enabled": True}
        ), patch.object(runner, "translate_keyword", return_value=localization):
            runner._prepare_run_localization(run_id, "worker-ai-test")

        db = self.Session()
        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        run = db.query(AnalysisRun).filter_by(id=run_id).one()
        self.assertEqual(keyword.localization, localization)
        self.assertEqual(run.analysis["request_config"]["marketplace_query"], "water bottle")
        self.assertIn("botol air", run.analysis["request_config"]["relevance_phrases"])
        self.assertEqual(run.analysis["localization_status"]["status"], "completed")
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

    def test_verification_resume_keeps_progress_and_save_state_is_monotonic(self):
        db = self.Session()
        run = AnalysisRun(
            keyword_id=self.keyword_id,
            status="needs_verification",
            progress=46,
            verification_platform="shopee",
        )
        db.add(run)
        db.commit()
        run_id = run.id
        user = db.query(User).filter_by(id=self.user_id).one()

        with patch.object(marketplace_api, "submit_run", return_value=True):
            response = marketplace_api.resume_run(run_id, db=db, current_user=user)

        self.assertEqual(response["run"]["progress"], 46)
        resumed = db.query(AnalysisRun).filter_by(id=run_id).one()
        resumed.status = "running"
        resumed.worker_id = "worker-progress"
        db.commit()
        db.close()

        with patch.object(runner, "SessionLocal", self.Session):
            self.assertTrue(
                runner._save_state(
                    run_id,
                    "worker-progress",
                    progress=10,
                    current_step="重新采集第 1 页",
                )
            )
            self.assertTrue(
                runner._save_state(run_id, "worker-progress", progress=60)
            )

        db = self.Session()
        updated = db.query(AnalysisRun).filter_by(id=run_id).one()
        self.assertEqual(updated.progress, 60)
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

    def test_keyword_list_summary_is_bounded_and_both_modes_use_three_queries(self):
        db = self.Session()
        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        stable = AnalysisRun(
            keyword_id=keyword.id,
            status="completed",
            progress=100,
            opportunity_score=68.0,
            verdict="谨慎观察",
            confidence=82.0,
            platform_scores={
                "shopee": {"score": 70.0, "metrics": {"median_price": 10.0}},
                "lazada": {"score": 66.0, "metrics": {"median_price": 20.5}},
            },
            analysis={
                "platform_errors": {"lazada": "部分页面不完整"},
                "counts": {"shopee": 20, "lazada": 10},
                "evidence": {"grade": "B", "label": "可辅助决策", "reasons": ["full-only"]},
                "opportunity_segments": [{
                    "label": "彩绘甲贴",
                    "ranking_reliability": 72.5,
                    "representative_titles": ["full-only"],
                    "platform_scores": {"full": "only"},
                }],
                "recommendations": ["full-only"],
            },
        )
        db.add(stable)
        db.flush()
        pending = AnalysisRun(
            keyword_id=keyword.id,
            status="pending",
            progress=35,
            current_step="等待第 2 页",
            analysis={
                "platform_errors": {"shopee": "等待加载"},
                "counts": {"shopee": 7},
                "collector_health": {"large": "full-only"},
            },
        )
        db.add(pending)
        second = TrackedKeyword(
            user_id=self.user_id,
            keyword="lunch box",
            platforms=["shopee", "lazada"],
            results_limit=20,
        )
        db.add(second)
        db.flush()
        db.add(AnalysisRun(
            keyword_id=second.id,
            status="partial",
            progress=100,
            opportunity_score=51.0,
            analysis={"counts": {"shopee": 5, "lazada": 0}},
        ))
        db.commit()
        user = db.query(User).filter_by(id=self.user_id).one()

        summary_statements: list[str] = []
        listener = lambda _conn, _cursor, statement, _params, _context, _many: summary_statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", listener)
        try:
            response = marketplace_api.list_keywords(
                detail="summary",
                db=db,
                current_user=user,
            )
        finally:
            event.remove(self.engine, "before_cursor_execute", listener)

        self.assertEqual(len(summary_statements), 3)
        rows = {row["id"]: row for row in response["data"]}
        payload = rows[self.keyword_id]
        self.assertEqual(payload["latest_run"]["id"], pending.id)
        self.assertEqual(payload["latest_result_run"]["id"], stable.id)
        self.assertEqual(
            payload["latest_run"]["analysis"],
            {
                "platform_errors": {"shopee": "等待加载"},
                "counts": {"shopee": 7},
                "evidence": None,
                "top_segment": None,
                "median_price": None,
            },
        )
        self.assertEqual(
            payload["latest_result_run"]["analysis"],
            {
                "platform_errors": {"lazada": "部分页面不完整"},
                "counts": {"shopee": 20, "lazada": 10},
                "evidence": {"grade": "B", "label": "可辅助决策"},
                "top_segment": {"label": "彩绘甲贴", "ranking_reliability": 72.5},
                "median_price": 15.25,
            },
        )
        summary_keys = {
            "id", "keyword_id", "keyword", "status", "progress", "current_step",
            "verification_platform", "opportunity_score", "verdict", "confidence",
            "analysis", "error_message", "created_at", "started_at", "completed_at",
        }
        self.assertEqual(set(payload["latest_run"]), summary_keys)
        self.assertNotIn("platform_scores", payload["latest_result_run"])
        self.assertNotIn("reasons", payload["latest_result_run"]["analysis"]["evidence"])
        self.assertNotIn(
            "representative_titles",
            payload["latest_result_run"]["analysis"]["top_segment"],
        )

        full_statements: list[str] = []
        full_listener = lambda _conn, _cursor, statement, _params, _context, _many: full_statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", full_listener)
        try:
            full = marketplace_api.list_keywords(
                detail="full",
                db=db,
                current_user=user,
            )
        finally:
            event.remove(self.engine, "before_cursor_execute", full_listener)
        self.assertEqual(len(full_statements), 3)
        full_payload = {row["id"]: row for row in full["data"]}[self.keyword_id]
        self.assertEqual(full_payload["latest_result_run"]["platform_scores"]["shopee"]["score"], 70.0)
        self.assertEqual(full_payload["latest_result_run"]["analysis"]["recommendations"], ["full-only"])
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

    def test_items_api_paginates_filters_and_hides_inline_images(self):
        db = self.Session()
        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        run = AnalysisRun(keyword_id=keyword.id, status="completed", progress=100)
        db.add(run)
        db.flush()
        snapshots = [
            ("shopee", "s1", "Red Nail Sticker", 1, "data:image/png;base64,AAAA"),
            ("shopee", "s2", "Blue Bottle", 2, "blob:https://shopee.com.my/abc"),
            ("lazada", "l1", "Red Nail Art", 1, "https://img.example/l1.jpg"),
            ("lazada", "l2", "red nail sticker", 2, "http://img.example/l2.jpg"),
            ("lazada", "l3", "100% Nail Sticker", 3, None),
        ]
        for platform, item_id, title, rank, image in snapshots:
            db.add(ListingSnapshot(
                run_id=run.id,
                keyword_id=keyword.id,
                platform=platform,
                item_id=item_id,
                title=title,
                product_url=f"https://example.test/{item_id}",
                image_url=image,
                search_rank=rank,
                raw_data={"search_page": 1, "page_rank": rank, "page_size": 20},
            ))
        db.commit()
        user = db.query(User).filter_by(id=self.user_id).one()

        legacy = marketplace_api.get_items(run.id, db=db, current_user=user)
        self.assertEqual(set(legacy), {"success", "data"})
        self.assertEqual(len(legacy["data"]), 5)
        legacy_by_id = {row["item_id"]: row for row in legacy["data"]}
        self.assertIsNone(legacy_by_id["s1"]["image_url"])
        self.assertIsNone(legacy_by_id["s2"]["image_url"])
        self.assertEqual(legacy_by_id["l1"]["image_url"], "https://img.example/l1.jpg")

        first = marketplace_api.get_items(
            run.id,
            platform="lazada",
            limit=1,
            offset=0,
            q="red nail",
            db=db,
            current_user=user,
        )
        self.assertEqual([row["item_id"] for row in first["data"]], ["l1"])
        self.assertEqual(
            first["pagination"],
            {"total": 2, "limit": 1, "offset": 0, "has_more": True},
        )
        second_page = marketplace_api.get_items(
            run.id,
            platform="lazada",
            limit=1,
            offset=1,
            q="red nail",
            db=db,
            current_user=user,
        )
        self.assertEqual([row["item_id"] for row in second_page["data"]], ["l2"])
        self.assertEqual(
            second_page["pagination"],
            {"total": 2, "limit": 1, "offset": 1, "has_more": False},
        )
        literal_percent = marketplace_api.get_items(
            run.id,
            q="100%",
            limit=10,
            db=db,
            current_user=user,
        )
        self.assertEqual([row["item_id"] for row in literal_percent["data"]], ["l3"])
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
        self.assertEqual(finished.current_step, "分析完成 · 部分数据不完整")
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

    def test_verification_opens_exact_extension_locked_tab_for_current_run(self):
        db = self.Session()
        run = AnalysisRun(
            keyword_id=self.keyword_id,
            status="needs_verification",
            verification_platform="shopee",
        )
        db.add(run)
        db.commit()
        run_id = run.id
        db.close()
        locked = {
            "id": "current-run-tab",
            "url": "https://shopee.xiapibuy.com/verify?run=current",
        }

        with (
            patch.object(runner, "SessionLocal", self.Session),
            patch.object(runner.settings, "BROWSER_MODE", "extension"),
            patch.object(runner, "browser_ready", return_value=True),
            patch.object(
                runner,
                "activate_locked_platform_tab",
                return_value=locked,
            ) as lock_action,
            patch.object(runner, "find_platform_tab") as find_tab,
            patch.object(runner, "activate_tab") as activate,
        ):
            opened_url = runner.open_verification_browser(run_id)

        self.assertEqual(opened_url, locked["url"])
        lock_action.assert_called_once_with("shopee", f"run:{run_id}:shopee")
        find_tab.assert_not_called()
        activate.assert_not_called()

    def test_mark_verification_persists_auditable_context(self):
        db = self.Session()
        run = AnalysisRun(
            keyword_id=self.keyword_id,
            status="running",
            worker_id="worker-verify",
            progress=35,
            analysis={"request_config": {"keyword": "water bottle"}},
        )
        db.add(run)
        db.commit()
        run_id = run.id
        db.close()
        context = {
            "platform": "shopee",
            "lock_key": f"run:{run_id}:shopee",
            "tab_id": "verify-tab-9",
            "preserved_listing_count": 20,
            "page_diagnostics": [{"page": 2, "completion_reason": "verification_required"}],
        }

        with patch.object(runner, "SessionLocal", self.Session):
            runner._mark_verification(
                run_id,
                runner.VerificationRequired(
                    "shopee",
                    "https://shopee.xiapibuy.com/verify",
                    context=context,
                ),
                "worker-verify",
            )

        db = self.Session()
        paused = db.query(AnalysisRun).filter_by(id=run_id).one()
        self.assertEqual(paused.status, "needs_verification")
        self.assertEqual(paused.progress, 35)
        self.assertEqual(paused.analysis["request_config"], {"keyword": "water bottle"})
        self.assertEqual(paused.analysis["verification_context"], context)
        db.close()

    def test_two_paused_runs_activate_their_persisted_exact_tabs(self):
        db = self.Session()
        runs = []
        for suffix in ("a", "b"):
            run = AnalysisRun(
                keyword_id=self.keyword_id,
                status="needs_verification",
                verification_platform="shopee",
            )
            db.add(run)
            db.flush()
            run.analysis = {
                "verification_context": {
                    "platform": "shopee",
                    "lock_key": f"run:{run.id}:shopee",
                    "tab_id": f"challenge-{suffix}",
                }
            }
            runs.append(run.id)
        db.commit()
        db.close()

        def exact_tab(_platform, tab_id):
            return {
                "id": tab_id,
                "url": f"https://shopee.xiapibuy.com/verify?tab={tab_id}",
            }

        with (
            patch.object(runner, "SessionLocal", self.Session),
            patch.object(runner.settings, "BROWSER_MODE", "extension"),
            patch.object(runner, "browser_ready", return_value=True),
            patch.object(runner, "find_platform_tab_by_id", side_effect=exact_tab),
            patch.object(runner, "activate_tab", return_value=True) as activate,
            patch.object(runner, "activate_locked_platform_tab") as lock_action,
        ):
            opened = [runner.open_verification_browser(run_id) for run_id in runs]

        self.assertIn("challenge-a", opened[0])
        self.assertIn("challenge-b", opened[1])
        self.assertEqual(
            [call.args[0] for call in activate.call_args_list],
            ["challenge-a", "challenge-b"],
        )
        lock_action.assert_not_called()

    def test_verification_fallback_activates_the_same_selected_old_extension_tab(self):
        db = self.Session()
        run = AnalysisRun(
            keyword_id=self.keyword_id,
            status="needs_verification",
            verification_platform="shopee",
        )
        db.add(run)
        db.commit()
        run_id = run.id
        db.close()
        selected = {
            "id": "challenge-z",
            "url": "https://shopee.xiapibuy.com/verify?old=2",
        }

        with (
            patch.object(runner, "SessionLocal", self.Session),
            patch.object(runner.settings, "BROWSER_MODE", "extension"),
            patch.object(runner, "browser_ready", return_value=True),
            patch.object(runner, "activate_locked_platform_tab", return_value=None),
            patch.object(runner, "find_platform_tab", return_value=selected),
            patch.object(runner, "activate_tab", return_value=True) as activate,
        ):
            opened_url = runner.open_verification_browser(run_id)

        self.assertEqual(opened_url, selected["url"])
        activate.assert_called_once_with("challenge-z")


class RunnerPageCollectionTests(unittest.IsolatedAsyncioTestCase):
    def test_page_wait_budgets_allow_cold_load_but_remain_bounded(self):
        self.assertEqual(runner._PAGE_MAX_SECONDS, 60.0)
        self.assertEqual(runner._PAGE_NAVIGATION_MAX_SECONDS, 12.0)
        self.assertEqual(runner._PAGE_FIRST_RESULTS_MAX_SECONDS, 25.0)
        self.assertEqual(runner._PAGE_TARGET_STABLE_ROUNDS, 4)
        self.assertEqual(runner._PAGE_BOTTOM_STABLE_ROUNDS, 4)

    async def test_extension_attach_wakes_then_retries_once_after_timeout(self):
        calls: list[tuple[str, float, dict]] = []
        attach_attempts = 0

        def fake_extension_request(action, timeout, **params):
            nonlocal attach_attempts
            calls.append((action, timeout, params))
            if action == "tabs":
                raise ExtensionBridgeError("主 Chrome 扩展响应超时：tabs")
            if action == "attach":
                attach_attempts += 1
                if attach_attempts == 1:
                    raise ExtensionBridgeError("主 Chrome 扩展响应超时：attach")
                return {
                    "session_id": "retry-session",
                    "lock_owner": "retry-owner",
                    "tab_id": "17",
                }
            if action == "release_platform_lock":
                return True
            if action == "detach":
                return True
            self.fail(f"unexpected extension action: {action}")

        with (
            patch.object(runner, "extension_request", fake_extension_request),
            patch.object(runner.settings, "COLLECTION_TIMEOUT_SECONDS", 45),
        ):
            async with runner._ExtensionCDPSocket(
                "shopee",
                "https://shopee.com.my/search?keyword=nail&page=0",
                "run:81:shopee",
            ) as socket:
                self.assertEqual(socket.session_id, "retry-session")
                self.assertEqual(socket.lock_owner, "retry-owner")

        self.assertEqual(
            [call[0] for call in calls],
            ["tabs", "attach", "attach", "release_platform_lock", "detach"],
        )
        self.assertEqual(calls[0][1], 3.0)
        self.assertEqual(calls[1][1], 35.0)
        self.assertEqual(calls[2][1], 40.0)
        self.assertEqual(calls[1][2]["lock_key"], "run:81:shopee")
        self.assertEqual(calls[2][2]["lock_key"], "run:81:shopee")
        self.assertEqual(calls[3][2]["lock_key"], "run:81:shopee")
        self.assertEqual(calls[3][2]["lock_owner"], "retry-owner")
        self.assertEqual(calls[4][2]["session_id"], "retry-session")

    async def test_stale_socket_late_release_cannot_delete_recovered_owner_lock(self):
        owners = iter(("owner-a", "owner-b"))
        current_owner = {"value": ""}
        releases: list[tuple[str, str]] = []

        def fake_extension_request(action, _timeout, **params):
            if action == "tabs":
                return []
            if action == "attach":
                owner = next(owners)
                current_owner["value"] = owner
                return {
                    "session_id": f"session-{owner}",
                    "lock_owner": owner,
                    "tab_id": "17",
                }
            if action == "release_platform_lock":
                requested_owner = params.get("lock_owner", "")
                releases.append((requested_owner, current_owner["value"]))
                if requested_owner == current_owner["value"]:
                    current_owner["value"] = ""
                return True
            if action == "detach":
                return True
            self.fail(f"unexpected extension action: {action}")

        with patch.object(runner, "extension_request", fake_extension_request):
            stale = runner._ExtensionCDPSocket(
                "shopee",
                "https://shopee.com.my/search?keyword=nail&page=0",
                "run:82:shopee",
            )
            recovered = runner._ExtensionCDPSocket(
                "shopee",
                "https://shopee.com.my/search?keyword=nail&page=0",
                "run:82:shopee",
            )
            await stale.__aenter__()
            await recovered.__aenter__()
            await stale.__aexit__(None, None, None)
            self.assertEqual(current_owner["value"], "owner-b")
            await recovered.__aexit__(None, None, None)

        self.assertEqual(current_owner["value"], "")
        self.assertEqual(
            releases,
            [("owner-a", "owner-b"), ("owner-b", "owner-b")],
        )

    async def test_old_extension_without_lock_owner_still_detaches_cleanly(self):
        actions: list[str] = []

        def fake_extension_request(action, _timeout, **_params):
            actions.append(action)
            if action == "tabs":
                return []
            if action == "attach":
                # Bridge 1.0.2/1.0.3 does not return lock_owner.
                return {"session_id": "legacy-session", "tab_id": "19"}
            if action == "release_platform_lock":
                raise ExtensionBridgeError("Unknown bridge action: release_platform_lock")
            if action == "detach":
                return True
            self.fail(f"unexpected extension action: {action}")

        with patch.object(runner, "extension_request", fake_extension_request):
            async with runner._ExtensionCDPSocket(
                "lazada",
                "https://www.lazada.com.my/catalog/?q=nail&page=1",
                "run:83:lazada",
            ) as socket:
                self.assertEqual(socket.session_id, "legacy-session")
                self.assertEqual(socket.lock_owner, "")

        self.assertEqual(
            actions,
            ["tabs", "attach", "release_platform_lock", "detach"],
        )

    def test_search_page_confirmation_checks_query_and_real_page(self):
        shopee = runner.ADAPTERS["shopee"]
        expected = shopee.search_url("指甲贴", page=2)
        self.assertTrue(runner._search_page_url_matches(shopee, expected, expected + "&sortBy=sales"))
        self.assertFalse(
            runner._search_page_url_matches(
                shopee,
                expected,
                shopee.search_url("指甲贴", page=1),
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                shopee,
                expected,
                "https://shopee.com.my/search?keyword=water+bottle&page=1",
            )
        )

        lazada = runner.ADAPTERS["lazada"]
        first_page = lazada.search_url("指甲贴", page=1)
        self.assertTrue(
            runner._search_page_url_matches(lazada, first_page, first_page + "&page=1")
        )

    def test_lazada_catalog_tag_redirect_is_bounded_by_query_page_and_safety(self):
        lazada = runner.ADAPTERS["lazada"]
        page_one = lazada.search_url("phone stand", page=1)
        page_two = lazada.search_url("phone stand", page=2)
        page_three = lazada.search_url("phone stand", page=3)
        tag_base = "https://www.lazada.com.my/tag/phone-stand/"

        self.assertTrue(
            runner._search_page_url_matches(
                lazada,
                page_one,
                f"{tag_base}?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertTrue(
            runner._search_page_url_matches(
                lazada,
                page_two,
                f"{tag_base}?q=phone+stand&catalog_redirect_tag=true&page=2",
            )
        )
        self.assertTrue(
            runner._search_page_url_matches(
                lazada,
                page_three,
                f"{tag_base}?q=phone%20stand&catalog_redirect_tag=true&page=3",
            )
        )
        self.assertTrue(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://www.lazada.com.my/tag/PHONE%2Dstand/"
                "?q=PHONE+STAND&catalog_redirect_tag=TRUE",
            )
        )
        self.assertTrue(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://www.lazada.com.my:443/tag/phone-stand/"
                "?q=phone+stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_two,
                f"{tag_base}?q=phone%20stand&catalog_redirect_tag=true&page=3",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                f"{tag_base}?q=phone%20holder&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                f"{tag_base}?q=phone%20stand",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_two,
                "https://www.lazada.com.my/tag/laptop/"
                "?q=phone%20stand&catalog_redirect_tag=true&page=2",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://www.lazada.com.my/tag/phone-stands/"
                "?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "http://www.lazada.com.my/tag/phone-stand/"
                "?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                page_one.replace("https://", "http://"),
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://www.lazada.com.my:444/tag/phone-stand/"
                "?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                page_one.replace("www.lazada.com.my", "www.lazada.com.my:444"),
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://u:p@www.lazada.com.my/tag/phone-stand/"
                "?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                page_one.replace("https://", "https://u:p@"),
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                page_one.replace("www.lazada.com.my", "lazada.com.my"),
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                f"{tag_base}?q=phone%20stand&q=laptop&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_two,
                f"{tag_base}?q=phone%20stand&catalog_redirect_tag=true&page=2&page=3",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                f"{tag_base}?q=phone%20stand&catalog_redirect_tag=true"
                "&catalog_redirect_tag=false",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://www.lazada.com.my/captcha/?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://www.lazada.com.my/404/?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://example.test/tag/phone-stand/?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                lazada.search_url("404", page=1),
                "https://www.lazada.com.my/tag/404/?q=404&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                lazada.search_url("captcha", page=1),
                "https://www.lazada.com.my/tag/%63aptcha/"
                "?q=captcha&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://www.lazada.com.my/tag/phone%2Fstand/"
                "?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://www.lazada.com.my/tag/phone%FFstand/"
                "?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://www.lazada.com.my/tag/phone%00stand/"
                "?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://www.lazada.com.my/tag/phone%EF%BC%8Fstand/"
                "?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                "https://www.lazada.com.my/tag/phone%E2%88%95stand/"
                "?q=phone%20stand&catalog_redirect_tag=true",
            )
        )
        self.assertFalse(
            runner._search_page_url_matches(
                lazada,
                page_one,
                f"{page_one}&q=laptop",
            )
        )

    def test_stale_grid_rejects_old_virtualized_subset_but_allows_new_page(self):
        previous_page = {f"item:{index}" for index in range(60)}
        old_viewport = {f"item:{index}" for index in range(20)}
        new_page_with_repeated_ads = {
            "item:3",
            "item:8",
            *{f"item:new-{index}" for index in range(18)},
        }
        mostly_old_with_one_rotating_ad = {
            *{f"item:{index}" for index in range(19)},
            "item:dynamic-ad",
        }

        self.assertTrue(runner._grid_is_stale(old_viewport, previous_page))
        self.assertTrue(
            runner._grid_is_stale(mostly_old_with_one_rotating_ad, previous_page)
        )
        self.assertFalse(runner._grid_is_stale(new_page_with_repeated_ads, previous_page))

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
            return (
                adapter.search_url("指甲贴", page=3),
                "results",
                [raw],
                [listing],
                [warning],
                [{"page": 2, "completion_reason": "failed"}],
            )

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
        self.assertEqual(
            health["shopee"]["page_diagnostics"],
            [{"page": 2, "completion_reason": "failed"}],
        )
        self.assertNotEqual(health["shopee"]["status"], "healthy")

    async def test_less_than_half_target_structure_warning_becomes_platform_error(self):
        adapter = runner.ADAPTERS["shopee"]
        listings = [
            adapter.parse_card(shopee_card(str(400 + index), f"Nail Sticker {index}"), index + 1)
            for index in range(20)
        ]
        raw = [{"item_id": f"raw-{index}"} for index in range(120)]
        keyword = Mock(
            id=12,
            keyword="指甲贴",
            platforms=["shopee"],
            results_limit=15,
            search_pages=3,
        )

        async def resident_result(*_args, **_kwargs):
            return (
                adapter.search_url("指甲贴", page=3),
                "results",
                raw,
                listings,
                [],
                [{"page": 3, "completion_reason": "target_count_stable"}],
            )

        with (
            patch.object(runner, "ensure_browser"),
            patch.object(runner, "_require_lease"),
            patch.object(runner, "_collect_resident_tab", resident_result),
            patch.object(runner, "_persist_platform"),
        ):
            results, errors, health = await runner._collect(
                keyword,
                run_id=85,
                worker_id="worker",
            )

        self.assertEqual(len(results["shopee"]), 20)
        self.assertEqual(health["shopee"]["status"], "degraded")
        self.assertIn("页面结构可能已经变化", errors["shopee"])

    async def test_first_page_failure_keeps_page_diagnostics_in_health(self):
        keyword = Mock(
            id=10,
            keyword="指甲贴",
            platforms=["shopee"],
            results_limit=20,
            search_pages=3,
        )
        diagnostics = [{
            "page": 1,
            "navigation_confirmed": True,
            "first_results_ready": False,
            "completion_reason": "failed",
            "error": "商品网格未挂载",
        }]

        async def failed_resident(*_args, **_kwargs):
            raise runner.PageCollectionError("商品网格未挂载", diagnostics)

        with (
            patch.object(runner, "ensure_browser"),
            patch.object(runner, "_require_lease"),
            patch.object(runner, "_collect_resident_tab", failed_resident),
        ):
            results, errors, health = await runner._collect(
                keyword,
                run_id=80,
                worker_id="worker",
            )

        self.assertEqual(results["shopee"], [])
        self.assertIn("商品网格未挂载", errors["shopee"])
        self.assertEqual(health["shopee"]["page_diagnostics"], diagnostics)
        self.assertEqual(health["shopee"]["status"], "error")

    async def test_verification_persists_prior_pages_before_interrupting_run(self):
        adapter = runner.ADAPTERS["shopee"]
        raw = shopee_card("300", "Nail Sticker")
        listing = adapter.parse_card(raw, 1)
        keyword = Mock(
            id=11,
            keyword="指甲贴",
            platforms=["shopee"],
            results_limit=20,
            search_pages=3,
        )
        diagnostics = [
            {"page": 1, "completion_reason": "target_count_stable"},
            {
                "page": 2,
                "completion_reason": "verification_required",
                "tab_id": "88",
            },
        ]

        async def verification_result(*_args, **_kwargs):
            return (
                "https://shopee.xiapibuy.com/verify",
                "captcha verification",
                [raw],
                [listing],
                [],
                diagnostics,
            )

        with (
            patch.object(runner, "ensure_browser"),
            patch.object(runner, "_require_lease"),
            patch.object(runner, "_collect_resident_tab", verification_result),
            patch.object(runner, "_persist_platform") as persist,
        ):
            with self.assertRaises(runner.VerificationRequired) as raised:
                await runner._collect(keyword, run_id=84, worker_id="worker")

        persist.assert_called_once_with(84, 11, "shopee", [listing], "worker")
        self.assertEqual(raised.exception.context["preserved_listing_count"], 1)
        self.assertEqual(raised.exception.context["tab_id"], "88")
        self.assertEqual(raised.exception.context["lock_key"], "run:84:shopee")
        self.assertEqual(raised.exception.context["page_diagnostics"], diagnostics)

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
        extraction_calls = {0: 0, 1: 0, 2: 0}

        class FakeExtensionSocket:
            def __init__(self, platform: str, url: str, lock_key: str):
                attachments.append((platform, url, lock_key))

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
            if expression.startswith("window.__MY_MARKET_RADAR_DOCUMENT_NONCE__ ="):
                return {"result": {"value": True}}
            if "my-market-radar-page-state" in expression:
                return {"result": {"value": {
                    "href": state["url"],
                    "readyState": "complete",
                    "visibilityState": "visible",
                    "bodyText": "Nail Sticker results",
                    "scrollY": 0,
                    "scrollHeight": 1000,
                    "atBottom": False,
                }}}
            if expression == "window.scrollTo(0, 0); true":
                return {"result": {"value": True}}
            if "document.querySelectorAll" in expression:
                extraction_calls[state["page_index"]] += 1
                if state["page_index"] == 1 and extraction_calls[1] <= 2:
                    # The page=2 URL has changed, but the virtual grid still contains page 1.
                    return {"result": {"value": page_cards[0]}}
                return {"result": {"value": page_cards[state["page_index"]]}}
            self.fail(f"unexpected Runtime.evaluate expression: {expression}")

        async def no_sleep(_seconds):
            return None

        with (
            patch.object(runner.settings, "BROWSER_MODE", "extension"),
            patch.object(runner, "_ExtensionCDPSocket", FakeExtensionSocket),
            patch.object(runner, "_cdp_call", fake_cdp_call),
            patch.object(runner, "_require_lease") as require_lease,
            patch.object(runner.asyncio, "sleep", no_sleep),
        ):
            current_url, _body, raw_cards, listings, warnings, diagnostics = await runner._collect_resident_tab(
                adapter,
                "指甲贴",
                limit=2,
                search_pages=3,
                run_id=77,
                worker_id="test-worker",
            )

        expected_urls = [adapter.search_url("指甲贴", page=page) for page in (1, 2, 3)]
        self.assertEqual(attachments, [("shopee", expected_urls[0], "run:77:shopee")])
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
        self.assertEqual([row["page"] for row in diagnostics], [1, 2, 3])
        self.assertTrue(all(row["navigation_confirmed"] for row in diagnostics))
        self.assertTrue(all(row["dom_stable"] for row in diagnostics))
        self.assertGreaterEqual(extraction_calls[1], 5)
        progress_values = [
            call.kwargs["progress"]
            for call in require_lease.call_args_list
            if "progress" in call.kwargs
        ]
        self.assertEqual(progress_values, sorted(progress_values))
        self.assertEqual(progress_values[0], 10)
        self.assertGreater(progress_values[2], 10)
        self.assertEqual(progress_values[-1], 65)

    async def test_waits_for_visible_tab_and_delayed_first_grid(self):
        adapter = runner.ADAPTERS["shopee"]
        cards = [shopee_card("150", "Nail Sticker A"), shopee_card("151", "Nail Sticker B")]
        state = {"url": "", "observations": 0, "extractions": 0, "bring_to_front": 0}

        class FakeExtensionSocket:
            def __init__(self, _platform: str, _url: str, _lock_key: str):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return None

        async def fake_cdp_call(_socket, _request_id, method, params=None):
            params = params or {}
            if method in {"Page.enable", "Runtime.enable"}:
                return {}
            if method == "Page.bringToFront":
                state["bring_to_front"] += 1
                return {}
            if method == "Page.navigate":
                state["url"] = params["url"]
                return {}
            expression = params.get("expression", "")
            if expression.startswith("window.__MY_MARKET_RADAR_DOCUMENT_NONCE__ ="):
                return {"result": {"value": True}}
            if "my-market-radar-page-state" in expression:
                state["observations"] += 1
                return {"result": {"value": {
                    "href": state["url"],
                    "readyState": "complete",
                    "visibilityState": "hidden" if state["observations"] == 1 else "visible",
                    "bodyText": "Nail Sticker results",
                    "scrollY": 0,
                    "scrollHeight": 1000,
                    "atBottom": False,
                }}}
            if expression == "window.scrollTo(0, 0); true":
                return {"result": {"value": True}}
            if "document.querySelectorAll" in expression:
                state["extractions"] += 1
                delayed = [] if state["extractions"] <= 2 else cards
                return {"result": {"value": delayed}}
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
            _url, _body, raw_cards, listings, warnings, diagnostics = (
                await runner._collect_resident_tab(
                    adapter,
                    "指甲贴",
                    limit=2,
                    search_pages=1,
                    run_id=81,
                    worker_id="test-worker",
                )
            )

        self.assertEqual([listing.item_id for listing in listings], ["150", "151"])
        self.assertEqual(len(raw_cards), 2)
        self.assertEqual(warnings, [])
        self.assertGreaterEqual(state["extractions"], 5)
        self.assertGreaterEqual(state["bring_to_front"], 4)
        self.assertEqual(diagnostics[0]["completion_reason"], "target_count_stable")
        self.assertTrue(diagnostics[0]["first_results_ready"])
        self.assertTrue(diagnostics[0]["dom_stable"])

    async def test_navigation_ignores_old_captcha_dom_until_target_url_arrives(self):
        adapter = runner.ADAPTERS["shopee"]
        cards = [shopee_card("160", "Nail Sticker A"), shopee_card("161", "Nail Sticker B")]
        state = {
            "url": "https://shopee.xiapibuy.com/verify?stale=1",
            "target": "",
            "observations": 0,
        }

        class FakeExtensionSocket:
            def __init__(self, _platform: str, _url: str, _lock_key: str):
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
                state["target"] = params["url"]
                return {}
            expression = params.get("expression", "")
            if expression.startswith("window.__MY_MARKET_RADAR_DOCUMENT_NONCE__ ="):
                return {"result": {"value": True}}
            if "my-market-radar-page-state" in expression:
                state["observations"] += 1
                if state["observations"] >= 2:
                    state["url"] = state["target"]
                stale = state["url"].startswith("https://shopee.xiapibuy.com")
                return {"result": {"value": {
                    "href": state["url"],
                    "readyState": "complete",
                    "visibilityState": "visible",
                    "bodyText": "captcha verification" if stale else "Nail Sticker results",
                    "scrollY": 0,
                    "scrollHeight": 1000,
                    "atBottom": False,
                }}}
            if expression == "window.scrollTo(0, 0); true":
                return {"result": {"value": True}}
            if "document.querySelectorAll" in expression:
                return {"result": {"value": cards}}
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
            current_url, _body, _raw, listings, warnings, diagnostics = (
                await runner._collect_resident_tab(
                    adapter,
                    "指甲贴",
                    limit=2,
                    search_pages=1,
                    run_id=82,
                    worker_id="test-worker",
                )
            )

        self.assertEqual(current_url, adapter.search_url("指甲贴"))
        self.assertEqual([listing.item_id for listing in listings], ["160", "161"])
        self.assertEqual(warnings, [])
        self.assertGreaterEqual(state["observations"], 2)
        self.assertEqual(diagnostics[0]["completion_reason"], "target_count_stable")

    async def test_same_url_navigation_waits_for_a_new_document_before_extracting(self):
        adapter = runner.ADAPTERS["shopee"]
        target_url = adapter.search_url("指甲贴")
        old_cards = [shopee_card("old-1", "Old Nail A"), shopee_card("old-2", "Old Nail B")]
        new_cards = [shopee_card("new-1", "New Nail A"), shopee_card("new-2", "New Nail B")]
        state = {
            "url": target_url,
            "nonce": "",
            "observations": 0,
            "committed": False,
            "extracted_before_commit": False,
        }

        class FakeExtensionSocket:
            def __init__(self, _platform: str, _url: str, _lock_key: str):
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
                self.assertEqual(params["url"], target_url)
                return {}
            expression = params.get("expression", "")
            if expression.startswith("window.__MY_MARKET_RADAR_DOCUMENT_NONCE__ ="):
                encoded = expression.split(" = ", 1)[1].rsplit("; true", 1)[0]
                state["nonce"] = json.loads(encoded)
                return {"result": {"value": True}}
            if "my-market-radar-page-state" in expression:
                state["observations"] += 1
                if state["observations"] >= 3:
                    state["committed"] = True
                    state["nonce"] = ""
                return {"result": {"value": {
                    "href": state["url"],
                    "readyState": "complete",
                    "visibilityState": "visible",
                    "collectorDocumentNonce": state["nonce"],
                    "bodyText": "New Nail results" if state["committed"] else "Old Nail results",
                    "scrollY": 0,
                    "scrollHeight": 1000,
                    "atBottom": False,
                }}}
            if expression == "window.scrollTo(0, 0); true":
                return {"result": {"value": True}}
            if "document.querySelectorAll" in expression:
                if not state["committed"]:
                    state["extracted_before_commit"] = True
                return {"result": {"value": new_cards if state["committed"] else old_cards}}
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
            _url, _body, _raw, listings, warnings, diagnostics = (
                await runner._collect_resident_tab(
                    adapter,
                    "指甲贴",
                    limit=2,
                    search_pages=1,
                    run_id=83,
                    worker_id="test-worker",
                )
            )

        self.assertEqual([listing.item_id for listing in listings], ["new-1", "new-2"])
        self.assertFalse(state["extracted_before_commit"])
        self.assertGreaterEqual(state["observations"], 3)
        self.assertEqual(warnings, [])
        self.assertTrue(diagnostics[0]["new_document_confirmed"])

    async def test_page_deadline_after_first_grid_keeps_partial_page_results(self):
        adapter = runner.ADAPTERS["shopee"]
        cards = [shopee_card("deadline-1", "Nail A"), shopee_card("deadline-2", "Nail B")]
        state = {
            "url": "",
            "first_grid": False,
            "deadline_crossed": False,
        }

        class FakeExtensionSocket:
            def __init__(self, _platform: str, _url: str, _lock_key: str):
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
                return {}
            expression = params.get("expression", "")
            if expression.startswith("window.__MY_MARKET_RADAR_DOCUMENT_NONCE__ ="):
                return {"result": {"value": True}}
            if "my-market-radar-page-state" in expression:
                return {"result": {"value": {
                    "href": state["url"],
                    "readyState": "complete",
                    "visibilityState": "visible",
                    "collectorDocumentNonce": "",
                    "bodyText": "Nail results",
                    "scrollY": 0,
                    "scrollHeight": 1000,
                    "atBottom": False,
                }}}
            if expression == "window.scrollTo(0, 0); true":
                return {"result": {"value": True}}
            if "document.querySelectorAll" in expression:
                state["first_grid"] = True
                return {"result": {"value": cards}}
            self.fail(f"unexpected CDP call: {method} {expression}")

        async def cross_deadline_after_first_grid(_seconds):
            if state["first_grid"]:
                state["deadline_crossed"] = True

        def controlled_clock():
            return 61.0 if state["deadline_crossed"] else 0.0

        with (
            patch.object(runner.settings, "BROWSER_MODE", "extension"),
            patch.object(runner, "_ExtensionCDPSocket", FakeExtensionSocket),
            patch.object(runner, "_cdp_call", fake_cdp_call),
            patch.object(runner, "_require_lease"),
            patch.object(runner.asyncio, "sleep", cross_deadline_after_first_grid),
            patch.object(runner.time, "monotonic", controlled_clock),
        ):
            _url, _body, raw_cards, listings, warnings, diagnostics = (
                await runner._collect_resident_tab(
                    adapter,
                    "指甲贴",
                    limit=2,
                    search_pages=1,
                    run_id=84,
                    worker_id="test-worker",
                )
            )

        self.assertEqual([listing.item_id for listing in listings], ["deadline-1", "deadline-2"])
        self.assertEqual(len(raw_cards), 2)
        self.assertEqual(diagnostics[0]["completion_reason"], "page_timeout_with_results")
        self.assertFalse(diagnostics[0]["dom_stable"])
        self.assertIn("已保留 2 条结果", warnings[0])

    async def test_page_two_verification_keeps_page_one_results_and_run_lock(self):
        adapter = runner.ADAPTERS["shopee"]
        page_one = [shopee_card("170", "Nail Sticker A"), shopee_card("171", "Nail Sticker B")]
        verification_url = "https://shopee.xiapibuy.com/verify?page=2"
        state = {"page": 0, "url": "", "clock": 0.0}
        sockets = []

        class FakeExtensionSocket:
            def __init__(self, _platform: str, _url: str, lock_key: str):
                self.lock_key = lock_key
                self.tab_id = "42"
                self.preserved = False
                sockets.append(self)

            def preserve_lock(self):
                self.preserved = True

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return None

        async def fake_cdp_call(_socket, _request_id, method, params=None):
            params = params or {}
            if method in {"Page.enable", "Runtime.enable", "Page.bringToFront"}:
                return {}
            if method == "Page.navigate":
                state["page"] += 1
                state["url"] = params["url"] if state["page"] == 1 else verification_url
                return {}
            expression = params.get("expression", "")
            if expression.startswith("window.__MY_MARKET_RADAR_DOCUMENT_NONCE__ ="):
                return {"result": {"value": True}}
            if "my-market-radar-page-state" in expression:
                verification = state["page"] == 2
                return {"result": {"value": {
                    "href": state["url"],
                    "readyState": "complete",
                    "visibilityState": "visible",
                    "bodyText": "captcha verification" if verification else "Nail Sticker results",
                    "scrollY": 0,
                    "scrollHeight": 1000,
                    "atBottom": False,
                }}}
            if expression == "window.scrollTo(0, 0); true":
                return {"result": {"value": True}}
            if "document.querySelectorAll" in expression:
                return {"result": {"value": page_one}}
            self.fail(f"unexpected CDP call: {method} {expression}")

        async def no_sleep(_seconds):
            return None

        def advancing_clock():
            state["clock"] += 0.5
            return state["clock"]

        with (
            patch.object(runner.settings, "BROWSER_MODE", "extension"),
            patch.object(runner, "_ExtensionCDPSocket", FakeExtensionSocket),
            patch.object(runner, "_cdp_call", fake_cdp_call),
            patch.object(runner, "_require_lease"),
            patch.object(runner.asyncio, "sleep", no_sleep),
            patch.object(runner.time, "monotonic", advancing_clock),
        ):
            current_url, body, raw_cards, listings, warnings, diagnostics = (
                await runner._collect_resident_tab(
                    adapter,
                    "指甲贴",
                    limit=2,
                    search_pages=2,
                    run_id=83,
                    worker_id="test-worker",
                )
            )

        self.assertEqual(current_url, verification_url)
        self.assertIn("captcha", body)
        self.assertEqual(len(raw_cards), 2)
        self.assertEqual([listing.item_id for listing in listings], ["170", "171"])
        self.assertEqual(warnings, [])
        self.assertEqual(diagnostics[-1]["completion_reason"], "verification_required")
        self.assertEqual(diagnostics[-1]["tab_id"], "42")
        self.assertTrue(sockets[0].preserved)
        self.assertEqual(sockets[0].lock_key, "run:83:shopee")

    async def test_second_page_exception_returns_warning_and_keeps_other_pages(self):
        adapter = runner.ADAPTERS["shopee"]
        page_cards = {
            0: [shopee_card("200", "Nail Sticker A"), shopee_card("201", "Nail Sticker B")],
            2: [shopee_card("203", "Nail Sticker D"), shopee_card("204", "Nail Sticker E")],
        }
        navigations: list[str] = []
        state = {"page_index": 0, "url": ""}

        class FakeExtensionSocket:
            def __init__(self, _platform: str, _url: str, _lock_key: str):
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
            if expression.startswith("window.__MY_MARKET_RADAR_DOCUMENT_NONCE__ ="):
                return {"result": {"value": True}}
            if "my-market-radar-page-state" in expression:
                return {"result": {"value": {
                    "href": state["url"],
                    "readyState": "complete",
                    "visibilityState": "visible",
                    "bodyText": "Nail Sticker results",
                    "scrollY": 0,
                    "scrollHeight": 1000,
                    "atBottom": False,
                }}}
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
            _url, _body, raw_cards, listings, warnings, diagnostics = await runner._collect_resident_tab(
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
        self.assertEqual(diagnostics[1]["page"], 2)
        self.assertEqual(diagnostics[1]["completion_reason"], "failed")


if __name__ == "__main__":
    unittest.main()
