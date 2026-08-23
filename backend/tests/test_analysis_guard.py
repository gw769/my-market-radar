import unittest

from app.services.marketplace.analysis_guard import finalize_analysis_evidence


class AnalysisGuardTests(unittest.TestCase):
    def test_overall_insufficient_keeps_segments_exploratory(self):
        analysis = {
            "opportunity_score": None,
            "opportunity_segments": [{
                "label": "stainless",
                "opportunity_score": 82.0,
                "sample_size": 12,
                "confidence": 80.0,
                "platform_coverage": 100.0,
            }],
            "recommendations": [
                "当前证据不足，不生成强选品结论。",
                "自动拆分的商品族中，当前排序最高的是“stainless”（机会分 82.0）。",
            ],
        }

        result = finalize_analysis_evidence(analysis)

        self.assertEqual(result["opportunity_segments"][0]["evidence_status"], "exploratory")
        self.assertFalse(result["segment_evidence_supported"])
        self.assertFalse(any(text.startswith("自动拆分的商品族中") for text in result["recommendations"]))
        self.assertTrue(any("不要仅凭该排序直接备货" in text for text in result["recommendations"]))

    def test_cross_platform_well_supported_segment_gets_action_advice(self):
        analysis = {
            "opportunity_score": 74.0,
            "opportunity_segments": [{
                "label": "stainless",
                "opportunity_score": 79.0,
                "sample_size": 9,
                "confidence": 72.0,
                "platform_coverage": 100.0,
            }],
            "recommendations": [
                "自动拆分的商品族中，当前排序最高的是“stainless”（机会分 79.0）。",
            ],
        }

        result = finalize_analysis_evidence(analysis)

        self.assertEqual(result["opportunity_segments"][0]["evidence_status"], "supported")
        self.assertTrue(result["segment_evidence_supported"])
        self.assertTrue(any("跨所选平台都有充分证据" in text for text in result["recommendations"]))

    def test_small_or_single_platform_segment_stays_exploratory(self):
        analysis = {
            "opportunity_score": 72.0,
            "opportunity_segments": [
                {
                    "label": "kids",
                    "opportunity_score": 88.0,
                    "sample_size": 4,
                    "confidence": 80.0,
                    "platform_coverage": 100.0,
                },
                {
                    "label": "glass",
                    "opportunity_score": 84.0,
                    "sample_size": 10,
                    "confidence": 75.0,
                    "platform_coverage": 50.0,
                },
            ],
            "recommendations": [],
        }

        result = finalize_analysis_evidence(analysis)

        self.assertTrue(all(segment["evidence_status"] == "exploratory" for segment in result["opportunity_segments"]))
        self.assertFalse(result["segment_evidence_supported"])


if __name__ == "__main__":
    unittest.main()
