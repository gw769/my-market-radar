import unittest

from app.services.marketplace.runner import _apply_evidence_gate


class EvidenceGateTests(unittest.TestCase):
    def test_grade_d_suppresses_segment_verdict_and_top_recommendation(self):
        analysis = {
            "opportunity_score": 82.0,
            "verdict": "建议尝试",
            "opportunity_segments": [
                {"label": "kids", "opportunity_score": 88.0, "verdict": "建议尝试"},
            ],
            "recommendations": [
                "自动拆分的商品族中，校准后排序最高的是“kids”（机会分 88.0）",
                "保留的一般建议",
            ],
        }
        _apply_evidence_gate(analysis, {"grade": "D"})
        self.assertIsNone(analysis["opportunity_score"])
        self.assertEqual(analysis["verdict"], "数据不足")
        self.assertEqual(analysis["opportunity_segments"][0]["verdict"], "数据不足")
        self.assertFalse(any(text.startswith("自动拆分的商品族中，") for text in analysis["recommendations"]))
        self.assertIn("保留的一般建议", analysis["recommendations"])
        self.assertIn("证据等级为 D", analysis["recommendations"][0])

    def test_grade_c_downgrades_only_strong_overall_verdict(self):
        analysis = {"opportunity_score": 75, "verdict": "建议尝试", "recommendations": []}
        _apply_evidence_gate(analysis, {"grade": "C"})
        self.assertEqual(analysis["verdict"], "谨慎观察")
        self.assertTrue(any("证据等级为 C" in text for text in analysis["recommendations"]))


if __name__ == "__main__":
    unittest.main()
