import unittest
from unittest.mock import Mock, patch

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
            # Simulates /resume changing the same run back to pending before the old worker's
            # finally block has removed its queue marker.
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

        async def empty_collect(_request, _run_id):
            return {"shopee": [], "lazada": []}, {
                "shopee": "公开搜索页没有返回可解析商品",
                "lazada": "公开搜索页没有返回可解析商品",
            }

        with patch.object(runner, "SessionLocal", self.Session), patch.object(runner, "_collect", empty_collect):
            runner.execute_run_sync(run_id)

        db = self.Session()
        completed = db.query(AnalysisRun).filter_by(id=run_id).one()
        self.assertEqual(completed.status, "failed")
        self.assertEqual(completed.current_step, "未采集到有效商品")
        self.assertIsNone(completed.opportunity_score)
        self.assertIn("Shopee", completed.error_message)
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


if __name__ == "__main__":
    unittest.main()
