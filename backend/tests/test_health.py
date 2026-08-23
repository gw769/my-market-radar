import unittest
from types import SimpleNamespace

from app.services.marketplace.evidence import build_evidence_summary
from app.services.marketplace.health import assess_collection_health, summarize_collector_health


def listing(price=20, sold=100, reviews=30, rating=4.7, shop_id="shop-1"):
    return SimpleNamespace(
        price=price,
        sold_count=sold,
        review_count=reviews,
        rating=rating,
        shop_id=shop_id,
        seller_name=None,
    )


class CollectorHealthTests(unittest.TestCase):
    def test_raw_cards_with_low_parse_ratio_are_unhealthy(self):
        health = assess_collection_health([{} for _ in range(20)], [listing(), listing()], 20)
        self.assertEqual(health["status"], "unhealthy")
        self.assertEqual(health["raw_count"], 20)
        self.assertEqual(health["parsed_count"], 2)
        self.assertLess(health["parse_ratio"], 50)
        self.assertTrue(any("页面结构变化" in warning for warning in health["warnings"]))

    def test_good_collection_is_healthy(self):
        rows = [listing(shop_id=f"shop-{index}") for index in range(20)]
        raw = [{"item_id": f"item-{index}"} for index in range(20)]
        health = assess_collection_health(raw, rows, 20)
        self.assertEqual(health["status"], "healthy")
        self.assertGreaterEqual(health["health_score"], 90)

    def test_duplicate_raw_anchors_count_as_one_product(self):
        rows = [listing(shop_id=f"shop-{index}") for index in range(10)]
        raw = []
        for index in range(10):
            href = f"https://example.test/item-{index}?src=search"
            raw.extend([
                {"item_id": f"item-{index}", "href": href, "title": f"Bottle {index}"},
                {"item_id": f"item-{index}", "href": href, "title": f"Bottle {index}"},
            ])
        health = assess_collection_health(raw, rows, 10)
        self.assertEqual(health["raw_rows"], 20)
        self.assertEqual(health["raw_count"], 10)
        self.assertEqual(health["parse_ratio"], 100.0)
        self.assertEqual(health["status"], "healthy")

    def test_error_platform_makes_overall_health_unhealthy(self):
        good_raw = [{"item_id": f"item-{index}"} for index in range(20)]
        good = assess_collection_health(good_raw, [listing() for _ in range(20)], 20)
        bad = assess_collection_health([], [], 20)
        bad["status"] = "error"
        bad["health_score"] = 0
        summary = summarize_collector_health({"shopee": good, "lazada": bad}, ["shopee", "lazada"])
        self.assertEqual(summary["status"], "unhealthy")
        self.assertEqual(summary["unhealthy_platforms"], ["lazada"])


class EvidenceTests(unittest.TestCase):
    def test_two_platform_high_quality_evidence_gets_a(self):
        scores = {
            "shopee": {"eligible": True, "confidence": 90, "sample_size": 20},
            "lazada": {"eligible": True, "confidence": 88, "sample_size": 20},
        }
        health = {"health_score": 90, "unhealthy_platforms": []}
        evidence = build_evidence_summary(scores, health, ["shopee", "lazada"])
        self.assertEqual(evidence["grade"], "A")

    def test_unhealthy_collector_forces_weakest_grade(self):
        scores = {
            "shopee": {"eligible": True, "confidence": 90, "sample_size": 20},
            "lazada": {"eligible": True, "confidence": 90, "sample_size": 20},
        }
        health = {"health_score": 30, "unhealthy_platforms": ["lazada"]}
        evidence = build_evidence_summary(scores, health, ["shopee", "lazada"])
        self.assertEqual(evidence["grade"], "D")
        self.assertTrue(any("采集器" in reason or "页面结构" in reason for reason in evidence["reasons"]))

    def test_single_platform_good_evidence_caps_at_b(self):
        scores = {"shopee": {"eligible": True, "confidence": 90, "sample_size": 20}}
        health = {"health_score": 90, "unhealthy_platforms": []}
        evidence = build_evidence_summary(scores, health, ["shopee"])
        self.assertEqual(evidence["grade"], "B")


if __name__ == "__main__":
    unittest.main()
