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
from app.models.marketplace import AnalysisRun, TrackedKeyword
from app.models.user import User
from app.services.marketplace import runner


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))
        return Mock()


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
            {"keyword": "water bottle", "platforms": ["shopee", "lazada"], "results_limit": 20},
        )

        keyword.platforms = ["shopee"]
        keyword.results_limit = 40
        db.commit()
        request = runner._collection_request(run, keyword)
        self.assertEqual(request.platforms, ["shopee", "lazada"])
        self.assertEqual(request.results_limit, 20)
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


if __name__ == "__main__":
    unittest.main()
