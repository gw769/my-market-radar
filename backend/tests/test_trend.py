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

    def _snapshot(
        self,
        db,
        run,
        item_id,
        title,
        sold,
        reviews=10,
        rank=1,
        price=20.0,
        platform="shopee",
        keyword_id=None,
    ):
        db.add(ListingSnapshot(
            run_id=run.id,
            keyword_id=keyword_id or self.keyword_id,
            platform=platform,
            item_id=item_id,
            title=title,
            product_url=f"https://example.test/{run.id}/{item_id}",
            price=price,
            sold_count=sold,
            review_count=reviews,
            search_rank=rank,
            data_quality=1.0,
        ))

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
            self._snapshot(
                db, previous, item_id, f"Water Bottle {index}", 100 + index,
                reviews=20 + index, rank=index + 2, price=20.0,
            )
            self._snapshot(
                db, current, item_id, f"Water Bottle {index}", 110 + index,
                reviews=21 + index, rank=index + 1, price=22.0 if index < 5 else 20.0,
            )
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
            self._snapshot(db, previous, f"item-{index}", f"Water Bottle {index}", 100, platform="lazada")
            self._snapshot(db, current, f"item-{index}", f"Water Bottle {index}", 105, platform="lazada")
        db.commit()
        trend = build_keyword_trend(db, self.keyword_id)
        self.assertEqual(trend["status"], "weak")
        self.assertIsNone(trend["overall"]["median_sold_velocity_per_day"])
        self.assertTrue(any("少于 6 小时" in text for text in trend["recommendations"]))
        db.close()

    def test_accessory_growth_is_excluded_from_temporal_demand(self):
        db = self.Session()
        previous = AnalysisRun(
            keyword_id=self.keyword_id,
            status="completed",
            completed_at=datetime(2026, 8, 22, 12, 0, 0),
        )
        current = AnalysisRun(
            keyword_id=self.keyword_id,
            status="completed",
            completed_at=datetime(2026, 8, 23, 12, 0, 0),
        )
        db.add_all([previous, current])
        db.flush()

        for index in range(6):
            self._snapshot(db, previous, f"core-{index}", f"Water Bottle Stainless {index}", 100)
            self._snapshot(db, current, f"core-{index}", f"Water Bottle Stainless {index}", 100)

        # This accessory is growing very fast, but the main scoring path classifies it as an accessory.
        self._snapshot(db, previous, "lid", "Replacement Lid for Water Bottle", 10)
        self._snapshot(db, current, "lid", "Replacement Lid for Water Bottle", 1010)
        db.commit()

        trend = build_keyword_trend(db, self.keyword_id)
        self.assertEqual(trend["overall"]["current_items"], 6)
        self.assertEqual(trend["overall"]["matched_items"], 6)
        self.assertEqual(trend["overall"]["activity_share"], 0.0)
        self.assertEqual(trend["overall"]["median_sold_delta"], 0.0)
        self.assertTrue(any("累计已售" in text for text in trend["recommendations"]))
        db.close()

    def test_chinese_keyword_tracks_english_and_malay_nail_sticker_titles(self):
        db = self.Session()
        user = db.query(User).first()
        keyword = TrackedKeyword(user_id=user.id, keyword="指甲贴")
        db.add(keyword)
        db.flush()
        previous = AnalysisRun(
            keyword_id=keyword.id,
            status="completed",
            completed_at=datetime(2026, 8, 22, 12, 0, 0),
        )
        current = AnalysisRun(
            keyword_id=keyword.id,
            status="completed",
            completed_at=datetime(2026, 8, 23, 12, 0, 0),
        )
        db.add_all([previous, current])
        db.flush()

        titles = [
            "Disney Nail Sticker Floral",
            "Cute Toenail Sticker Glitter",
            "Pelekat Kuku Bunga",
            "Sanrio Nail Decal",
            "Nail Stickers Colorful",
            "Toenail Stickers Summer",
        ]
        for index, title in enumerate(titles):
            item_id = f"nail-{index}"
            self._snapshot(
                db, previous, item_id, title, 100, reviews=20,
                keyword_id=keyword.id,
            )
            self._snapshot(
                db, current, item_id, title, 110, reviews=21,
                keyword_id=keyword.id,
            )

        # Strong growth in unrelated categories must not enter the localized temporal signal.
        for item_id, title in (("henna", "Henna Nail Art Kit"), ("moxa", "Moxibustion Health Patch")):
            self._snapshot(db, previous, item_id, title, 10, keyword_id=keyword.id)
            self._snapshot(db, current, item_id, title, 1010, keyword_id=keyword.id)
        db.commit()

        trend = build_keyword_trend(db, keyword.id)
        self.assertEqual(trend["status"], "usable")
        self.assertEqual(trend["overall"]["current_items"], 6)
        self.assertEqual(trend["overall"]["matched_items"], 6)
        self.assertEqual(trend["overall"]["activity_share"], 100.0)
        self.assertEqual(trend["overall"]["median_sold_delta"], 10.0)
        db.close()


if __name__ == "__main__":
    unittest.main()
