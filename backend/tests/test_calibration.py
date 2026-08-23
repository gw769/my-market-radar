import unittest

from app.services.marketplace.calibration import calibrate_analysis, confidence_weighted_score


class CalibrationTests(unittest.TestCase):
    def test_high_confidence_platform_has_more_weight(self):
        score = confidence_weighted_score([
            {"eligible": True, "score": 80, "confidence": 90},
            {"eligible": True, "score": 40, "confidence": 30},
        ])
        self.assertAlmostEqual(score, 70.0)

    def test_combined_analysis_uses_confidence_weighting(self):
        analysis = {
            "opportunity_score": 60,
            "verdict": "谨慎观察",
            "platform_scores": {
                "shopee": {"eligible": True, "score": 80, "confidence": 90},
                "lazada": {"eligible": True, "score": 40, "confidence": 30},
            },
            "opportunity_segments": [],
            "recommendations": [],
        }
        calibrated = calibrate_analysis(analysis)
        self.assertEqual(calibrated["opportunity_score"], 70.0)
        self.assertEqual(calibrated["verdict"], "建议尝试")
        self.assertEqual(calibrated["aggregation"]["method"], "confidence_weighted")

    def test_small_single_platform_segment_is_shrunk_toward_neutral(self):
        analysis = {
            "platform_scores": {
                "shopee": {"eligible": True, "score": 70, "confidence": 80},
                "lazada": {"eligible": True, "score": 65, "confidence": 80},
            },
            "opportunity_segments": [
                {
                    "label": "tiny",
                    "opportunity_score": 95,
                    "confidence": 80,
                    "sample_size": 4,
                    "platform_scores": {
                        "shopee": {"eligible": True, "score": 95, "confidence": 80},
                    },
                },
                {
                    "label": "broad",
                    "opportunity_score": 78,
                    "confidence": 80,
                    "sample_size": 16,
                    "platform_scores": {
                        "shopee": {"eligible": True, "score": 78, "confidence": 80},
                        "lazada": {"eligible": True, "score": 76, "confidence": 80},
                    },
                },
            ],
            "recommendations": ["自动拆分的商品族中，当前排序最高的是“tiny”（旧文案）"],
        }
        calibrated = calibrate_analysis(analysis)
        by_label = {item["label"]: item for item in calibrated["opportunity_segments"]}
        self.assertLess(by_label["tiny"]["opportunity_score"], 80)
        self.assertGreater(by_label["broad"]["opportunity_score"], by_label["tiny"]["opportunity_score"])
        self.assertLess(by_label["tiny"]["ranking_reliability"], by_label["broad"]["ranking_reliability"])
        self.assertTrue(any("校准后排序最高" in text for text in calibrated["recommendations"]))

    def test_ineligible_platform_keeps_combined_score_unchanged_for_evidence_gate(self):
        analysis = {
            "opportunity_score": None,
            "verdict": "数据不足",
            "platform_scores": {
                "shopee": {"eligible": True, "score": 75, "confidence": 80},
                "lazada": {"eligible": False, "score": 90, "confidence": 40},
            },
            "opportunity_segments": [],
            "recommendations": [],
        }
        calibrated = calibrate_analysis(analysis)
        self.assertIsNone(calibrated["opportunity_score"])
        self.assertEqual(calibrated["verdict"], "数据不足")


if __name__ == "__main__":
    unittest.main()
