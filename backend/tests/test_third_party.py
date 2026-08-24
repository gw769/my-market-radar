import unittest
from types import SimpleNamespace

from app.services.marketplace.third_party import summarize_shopdora


class ThirdPartySummaryTests(unittest.TestCase):
    def test_shopdora_summary_is_bounded_and_labeled_as_estimated(self):
        rows = [
            SimpleNamespace(raw_data={"shopdora": {
                "provider": "Shopdora", "estimated": True,
                "sales_30d": sales, "sales_30d_growth_percent": growth,
                "revenue_30d_myr": revenue, "total_sales_estimate": total,
                "gmv_estimate_myr": revenue * 10, "listing_age_days": age,
                "like_count": likes, "seller_type": "本土",
                "category_path": category,
            }})
            for sales, growth, revenue, total, age, likes, category in (
                (100, 10.0, 500.0, 1000, 300, 20, "家居-毛巾"),
                (300, -5.0, 900.0, 3000, 700, 40, "家居-毛巾"),
            )
        ]
        rows.append(SimpleNamespace(raw_data={"shopdora": None}))

        summary = summarize_shopdora({"shopee": rows, "lazada": []})

        self.assertEqual(summary["provider"], "Shopdora")
        self.assertTrue(summary["estimated"])
        self.assertEqual(summary["sample_size"], 2)
        self.assertEqual(summary["snapshot_sample_size"], 3)
        self.assertEqual(summary["metrics"]["median_sales_30d"], 200.0)
        self.assertEqual(summary["metrics"]["median_sales_30d_growth_percent"], 2.5)
        self.assertEqual(summary["local_seller_share"], 100.0)
        self.assertEqual(summary["top_categories"][0], {"category": "家居-毛巾", "count": 2})
        self.assertIn("不参与确定性机会分", summary["disclaimer"])

    def test_no_plugin_data_returns_none(self):
        self.assertIsNone(summarize_shopdora({"shopee": [SimpleNamespace(raw_data={})]}))


if __name__ == "__main__":
    unittest.main()
