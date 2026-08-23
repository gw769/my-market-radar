import unittest

from app.services.marketplace.scoring import build_analysis, score_platform


def samples(count: int, sold_base: int = 100):
    return [
        {
            "price": 10 + index,
            "sold_count": sold_base + index * 5,
            "rating": 4.4 + (index % 5) / 10,
            "review_count": 20 + index,
            "is_sponsored": index % 4 == 0,
        }
        for index in range(count)
    ]


class ScoringTests(unittest.TestCase):
    def test_missing_values_reduce_confidence_without_becoming_zero(self):
        complete = score_platform(samples(20))
        missing = score_platform([{"price": 15 + index} for index in range(20)])
        self.assertLess(missing["confidence"], complete["confidence"])
        self.assertIsNone(missing["dimensions"]["demand"])
        self.assertIsNone(missing["metrics"]["median_sold"])
        self.assertGreater(missing["score"], 0)

    def test_combined_requires_ten_valid_results_from_each_platform(self):
        insufficient = build_analysis("bottle", {"shopee": samples(10), "lazada": samples(9)})
        self.assertIsNone(insufficient["opportunity_score"])
        self.assertEqual(insufficient["verdict"], "数据不足")
        enough = build_analysis("bottle", {"shopee": samples(10), "lazada": samples(10, 900)})
        expected = round((enough["platform_scores"]["shopee"]["score"] + enough["platform_scores"]["lazada"]["score"]) / 2, 1)
        self.assertEqual(enough["opportunity_score"], expected)

    def test_scoring_is_reproducible_and_platform_specific(self):
        first = build_analysis("lamp", {"shopee": samples(12, 20), "lazada": samples(12, 1500)})
        second = build_analysis("lamp", {"shopee": samples(12, 20), "lazada": samples(12, 1500)})
        self.assertEqual(first, second)
        self.assertNotEqual(first["platform_scores"]["shopee"]["score"], first["platform_scores"]["lazada"]["score"])
        self.assertIn(first["platform_scores"]["shopee"]["verdict"], {"建议尝试", "谨慎观察", "暂不建议"})


if __name__ == "__main__":
    unittest.main()
