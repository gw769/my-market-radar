import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.marketplace import AnalysisRun, ListingSnapshot, TrackedKeyword
from app.models.user import User
from app.services.marketplace.trend import build_keyword_trend


class TrendEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        db = self.Session()
        user = User(username="trend-user", email="trend@example.com", password_hash="x")
        db.add(user)
        db.flush()
        keyword = TrackedKeyword(user_id=user.id, keyword="water bottle")
        db.add(keyword)
        db.commit()
        self.keyword_id = keyword.id
        db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_one_stable_run_is_insufficient_history(self):
        db = self.Session()
        run = AnalysisRun(
            keyword_id=self.keyword_id,
            status="completed",
            completed_at=datetime(2026, 8, 22, 12, 0, 0),
        )
        db.add(run)
        db.commit()
        trend = build_keyword_trend(db, self.keyword_id)
        self.assertEqual(trend["status"], "insufficient_history")
        self.assertEqual(trend["current_run_id"], run.id)
        db.close()

    def test_two_daily_snapshots_produce_momentum_metrics(self):
        db = self.Session()
        previous_time = datetime(2026, 8, 22, 12, 0, 0)
        current_time = previous_time + timedelta(hours=24)
        previous = AnalysisRun(keyword_id=self.keyword_id, status="completed", completed_at=previous_time)
        current = AnalysisRun(keyword_id=self.keyword_id, status="completed", completed_at=current_time)
        db.add_all([previous, current])
        db.flush()

        for index in range(10):
            item_id = f"item-{index}"
            db.add(ListingSnapshot(
                run_id=previous.id,
                keyword_id=self.keyword_id,
                platform="shopee",
                item_id=item_id,
                title=f"Bottle {index}",
                product_url=f"https://example.test/{item_id}",
                price=20.0,
                sold_count=100 + index,
                review_count=20 + index,
                search_rank=index + 2,
                data_quality=1.0,
            ))
            db.add(ListingSnapshot(
                run_id=current.id,
                keyword_id=self.keyword_id,
                platform="shopee",
                item_id=item_id,
                title=f"Bottle {index}",
                product_url=f"https://example.test/{item_id}",
                price=22.0 if index < 5 else 20.0,
                sold_count=110 + index,
                review_count=21 + index,
                search_rank=index + 1,
                data_quality=1.0,
            ))
        db.commit()

        trend = build_keyword_trend(db, self.keyword_id)
        self.assertEqual(trend["status"], "usable")
        self.assertEqual(trend["interval_hours"], 24.0)
        self.assertEqual(trend["overall"]["matched_items"], 10)
        self.assertEqual(trend["overall"]["match_rate"], 100.0)
        self.assertEqual(trend["overall"]["activity_share"], 100.0)
        self.assertEqual(trend["overall"]["median_sold_delta"], 10.0)
        self.assertEqual(trend["overall"]["median_sold_velocity_per_day"], 10.0)
        self.assertEqual(trend["overall"]["median_review_delta"], 1.0)
        self.assertEqual(trend["overall"]["median_rank_change"], 1.0)
        self.assertGreaterEqual(trend["overall"]["reliability"], 90)
        self.assertTrue(any("当前活跃" in text for text in trend["recommendations"]))
        db.close()

    def test_short_interval_is_weak_even_with_many_matches(self):
        db = self.Session()
        previous_time = datetime(2026, 8, 23, 10, 0, 0)
        current_time = previous_time + timedelta(hours=2)
        previous = AnalysisRun(keyword_id=self.keyword_id, status="completed", completed_at=previous_time)
        current = AnalysisRun(keyword_id=self.keyword_id, status="partial", completed_at=current_time)
        db.add_all([previous, current])
        db.flush()
        for index in range(10):
            for run, sold in ((previous, 100), (current, 105)):
                db.add(ListingSnapshot(
                    run_id=run.id,
                    keyword_id=self.keyword_id,
                    platform="lazada",
                    item_id=f"item-{index}",
                    title=f"Bottle {index}",
                    product_url=f"https://example.test/{run.id}/{index}",
                    price=20,
                    sold_count=sold,
                    review_count=10,
                    search_rank=index + 1,
                    data_quality=1.0,
                ))
        db.commit()
        trend = build_keyword_trend(db, self.keyword_id)
        self.assertEqual(trend["status"], "weak")
        self.assertIsNone(trend["overall"]["median_sold_velocity_per_day"])
        self.assertTrue(any("少于 6 小时" in text for text in trend["recommendations"]))
        db.close()


if __name__ == "__main__":
    unittest.main()
