import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.marketplace import dashboard
from app.core.database import Base
from app.models.marketplace import AnalysisRun, ListingSnapshot, TrackedKeyword
from app.models.user import User


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_dashboard_counts_all_verification_runs_but_only_latest_stable_snapshots(self):
        db = self.Session()
        user = User(username="dash", email="dash@example.com", password_hash="x")
        db.add(user)
        db.flush()
        keyword = TrackedKeyword(user_id=user.id, keyword="bottle", platforms=["shopee"])
        db.add(keyword)
        db.flush()

        old_verification = AnalysisRun(keyword_id=keyword.id, status="needs_verification")
        db.add(old_verification)
        db.flush()

        old_completed = AnalysisRun(keyword_id=keyword.id, status="completed", opportunity_score=40)
        db.add(old_completed)
        db.flush()
        db.add(ListingSnapshot(
            run_id=old_completed.id, keyword_id=keyword.id, platform="shopee", item_id="old-1",
            title="Old 1", product_url="https://example.test/old1", search_rank=1,
        ))
        db.add(ListingSnapshot(
            run_id=old_completed.id, keyword_id=keyword.id, platform="shopee", item_id="old-2",
            title="Old 2", product_url="https://example.test/old2", search_rank=2,
        ))

        latest_completed = AnalysisRun(keyword_id=keyword.id, status="completed", opportunity_score=60)
        db.add(latest_completed)
        db.flush()
        db.add(ListingSnapshot(
            run_id=latest_completed.id, keyword_id=keyword.id, platform="shopee", item_id="new-1",
            title="New 1", product_url="https://example.test/new1", search_rank=1,
        ))

        # Push the verification run outside the recent-50 window.
        for index in range(55):
            db.add(AnalysisRun(keyword_id=keyword.id, status="failed", error_message=f"failure-{index}"))
        db.commit()
        db.refresh(user)

        data = dashboard(db=db, current_user=user)["data"]
        self.assertEqual(data["needs_verification"], 1)
        self.assertEqual(data["completed_runs"], 2)
        self.assertEqual(data["platform_counts"], {"shopee": 1})
        self.assertLessEqual(len(data["latest_runs"]), 8)
        self.assertLessEqual(len(data["score_history"]), 30)
        db.close()


if __name__ == "__main__":
    unittest.main()
