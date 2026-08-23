import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.discovery import deep_scan_keyword
from app.core.database import Base
from app.models.marketplace import AnalysisRun, TrackedKeyword
from app.models.user import User
from app.schemas.marketplace import RunCreate


class DiscoveryDeepScanTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        db = self.Session()
        user = User(username="discovery-user", email="discovery@example.com", password_hash="x")
        db.add(user)
        db.flush()
        keyword = TrackedKeyword(
            user_id=user.id,
            keyword="water bottle",
            platforms=["shopee", "lazada"],
            results_limit=15,
            tracking_enabled=False,
        )
        db.add(keyword)
        db.commit()
        self.user_id = user.id
        self.keyword_id = keyword.id
        db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_deep_scan_freezes_override_without_mutating_keyword(self):
        db = self.Session()
        user = db.query(User).filter_by(id=self.user_id).one()
        with patch("app.api.discovery.submit_run", return_value=True):
            response = deep_scan_keyword(
                self.keyword_id,
                RunCreate(results_limit=40, platforms=["shopee", "lazada"]),
                db=db,
                current_user=user,
            )

        keyword = db.query(TrackedKeyword).filter_by(id=self.keyword_id).one()
        run = db.query(AnalysisRun).filter_by(id=response["run"]["id"]).one()
        self.assertTrue(response["queued"])
        self.assertEqual(run.trigger, "discovery_deep")
        expected_request = {
            "keyword": "water bottle",
            "marketplace_query": "water bottle",
            "platforms": ["shopee", "lazada"],
            "results_limit": 40,
            "search_pages": 3,
            "max_results_per_platform": 120,
        }
        self.assertEqual(run.analysis["request_config"], expected_request)
        self.assertEqual(response["request_config"], expected_request)
        self.assertEqual(run.analysis["scan_mode"], "discovery_deep")
        self.assertEqual(keyword.results_limit, 15)
        self.assertFalse(keyword.tracking_enabled)
        self.assertEqual(response["keyword_defaults"]["results_limit"], 15)
        db.close()

    def test_active_run_is_returned_without_overwriting_config(self):
        db = self.Session()
        user = db.query(User).filter_by(id=self.user_id).one()
        active = AnalysisRun(
            keyword_id=self.keyword_id,
            status="pending",
            trigger="manual",
            analysis={"request_config": {"keyword": "water bottle", "platforms": ["shopee"], "results_limit": 15}},
        )
        db.add(active)
        db.commit()
        active_id = active.id

        with patch("app.api.discovery.submit_run") as submit:
            response = deep_scan_keyword(
                self.keyword_id,
                RunCreate(results_limit=40),
                db=db,
                current_user=user,
            )

        self.assertFalse(response["queued"])
        self.assertEqual(response["reason"], "active_run")
        self.assertEqual(response["run"]["id"], active_id)
        db.refresh(active)
        self.assertEqual(active.analysis["request_config"]["results_limit"], 15)
        submit.assert_not_called()
        db.close()


if __name__ == "__main__":
    unittest.main()
