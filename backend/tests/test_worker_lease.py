import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.marketplace import AnalysisRun, TrackedKeyword
from app.models.user import User
from app.services.marketplace import runner


class WorkerLeaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        db = self.Session()
        user = User(username="lease-user", email="lease@example.com", password_hash="x")
        db.add(user)
        db.flush()
        keyword = TrackedKeyword(user_id=user.id, keyword="bottle")
        db.add(keyword)
        db.flush()
        run = AnalysisRun(
            keyword_id=keyword.id,
            status="running",
            progress=10,
            worker_id="worker-new",
            heartbeat_at=runner._utcnow(),
        )
        db.add(run)
        db.commit()
        self.run_id = run.id
        db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_expired_worker_cannot_update_run_state(self):
        original = runner.SessionLocal
        runner.SessionLocal = self.Session
        try:
            self.assertFalse(runner._save_state(self.run_id, "worker-old", progress=99))
            self.assertTrue(runner._save_state(self.run_id, "worker-new", progress=25))
        finally:
            runner.SessionLocal = original

        db = self.Session()
        run = db.query(AnalysisRun).filter_by(id=self.run_id).one()
        self.assertEqual(run.progress, 25)
        self.assertEqual(run.worker_id, "worker-new")
        db.close()

    def test_expired_worker_require_lease_raises(self):
        original = runner.SessionLocal
        runner.SessionLocal = self.Session
        try:
            with self.assertRaises(runner.WorkerLeaseLost):
                runner._require_lease(self.run_id, "worker-old")
        finally:
            runner.SessionLocal = original


if __name__ == "__main__":
    unittest.main()
