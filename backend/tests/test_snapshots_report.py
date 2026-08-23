import unittest
from io import BytesIO
from unittest.mock import patch

from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.marketplace import AnalysisRun, ListingSnapshot, TrackedKeyword
from app.models.user import User
from app.services.marketplace.adapters import MarketplaceListing
from app.services.marketplace.report import build_report
from app.services.marketplace import runner


class SnapshotAndReportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        db = self.Session()
        user = User(username="tester", email="tester@example.com", password_hash="x")
        db.add(user)
        db.flush()
        keyword = TrackedKeyword(user_id=user.id, keyword="water bottle", platforms=["shopee", "lazada"])
        db.add(keyword)
        db.flush()
        run1 = AnalysisRun(keyword_id=keyword.id, status="completed", opportunity_score=61.2, verdict="谨慎观察", confidence=80)
        run2 = AnalysisRun(keyword_id=keyword.id, status="partial")
        db.add_all([run1, run2])
        db.commit()
        self.keyword_id, self.run1_id, self.run2_id = keyword.id, run1.id, run2.id
        db.close()

    def tearDown(self):
        self.engine.dispose()

    def test_later_empty_run_does_not_overwrite_previous_success(self):
        listing = MarketplaceListing(
            platform="shopee", item_id="item-1", title="Bottle", product_url="https://example.test/1",
            price=19.9, sold_count=120, search_rank=1,
        )
        with patch.object(runner, "SessionLocal", self.Session):
            runner._persist_platform(self.run1_id, self.keyword_id, "shopee", [listing])
            runner._persist_platform(self.run2_id, self.keyword_id, "shopee", [])
        db = self.Session()
        self.assertEqual(db.query(ListingSnapshot).filter_by(run_id=self.run1_id).count(), 1)
        self.assertEqual(db.query(ListingSnapshot).filter_by(run_id=self.run2_id).count(), 0)
        db.close()

    def test_excel_has_required_sheets_and_dash_for_missing_values(self):
        db = self.Session()
        run = db.query(AnalysisRun).filter_by(id=self.run1_id).one()
        run.platform_scores = {
            "shopee": {"score": 62, "verdict": "谨慎观察", "sample_size": 12, "confidence": 81},
            "lazada": {"score": 60, "verdict": "谨慎观察", "sample_size": 11, "confidence": 79},
        }
        run.analysis = {"recommendations": ["先小批量测试。"]}
        db.add(ListingSnapshot(
            run_id=run.id, keyword_id=self.keyword_id, platform="lazada", item_id="l1",
            title="Bottle", product_url="https://example.test/l1", price=20, search_rank=1, data_quality=0.5,
        ))
        db.commit()
        output = build_report(db, run)
        workbook = load_workbook(BytesIO(output.getvalue()))
        self.assertEqual(
            workbook.sheetnames,
            ["综合结论", "Shopee竞品", "Lazada竞品", "每日价格与排名趋势", "数据口径说明"],
        )
        lazada = workbook["Lazada竞品"]
        self.assertEqual(lazada.cell(2, 4).value, "—")
        self.assertIn("Lazada 结论", [row[0].value for row in workbook["综合结论"].iter_rows()])
        db.close()


if __name__ == "__main__":
    unittest.main()
