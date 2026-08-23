import unittest

from app.services.marketplace.scoring import build_analysis, relevance_score, score_platform


def samples(count: int, sold_base: int = 100, title: str = "water bottle stainless 1L"):
    return [
        {
            "title": title,
            "price": 20 + (index % 8),
            "sold_count": sold_base + index * 5,
            "rating": 4.4 + (index % 5) / 10,
            "review_count": 20 + index,
            "is_sponsored": index % 4 == 0,
        }
        for index in range(count)
    ]


class ScoringTests(unittest.TestCase):
    def test_missing_core_values_never_produce_strong_verdict(self):
        missing = score_platform([{"title": "water bottle", "price": 15 + index} for index in range(20)], keyword="water bottle")
        self.assertFalse(missing["eligible"])
        self.assertEqual(missing["verdict"], "数据不足")
        self.assertIn("销量/评论需求证据不足", missing["eligibility_reasons"])

    def test_complete_data_is_eligible_and_reproducible(self):
        first = score_platform(samples(20), keyword="water bottle")
        second = score_platform(samples(20), keyword="water bottle")
        self.assertTrue(first["eligible"])
        self.assertEqual(first, second)
        self.assertGreaterEqual(first["confidence"], 55)

    def test_single_selected_platform_can_generate_overall_score(self):
        result = build_analysis("water bottle", {"shopee": samples(15)})
        self.assertIsNotNone(result["opportunity_score"])
        self.assertNotEqual(result["verdict"], "数据不足")

    def test_all_selected_platforms_must_be_eligible(self):
        result = build_analysis("water bottle", {
            "shopee": samples(15),
            "lazada": [{"title": "water bottle", "price": 10 + i} for i in range(15)],
        })
        self.assertIsNone(result["opportunity_score"])
        self.assertEqual(result["verdict"], "数据不足")

    def test_irrelevant_search_drift_is_excluded(self):
        items = samples(10) + samples(10, title="replacement bottle cap")
        result = score_platform(items, keyword="water bottle")
        self.assertEqual(result["raw_sample_size"], 20)
        self.assertEqual(result["sample_size"], 10)
        self.assertEqual(result["excluded_irrelevant"], 10)

    def test_relevance_guard_requires_phrase_evidence(self):
        self.assertEqual(relevance_score("water bottle", "water bottle stainless 1L"), 1.0)
        self.assertLess(relevance_score("water bottle", "replacement bottle cap"), 0.6)

    def test_extreme_price_spread_is_not_automatically_best(self):
        items = samples(20)
        for index, item in enumerate(items):
            item["price"] = 5 if index < 10 else 200
        result = score_platform(items, keyword="water bottle")
        self.assertLess(result["dimensions"]["price_room"], 100)


if __name__ == "__main__":
    unittest.main()
