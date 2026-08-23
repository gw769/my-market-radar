import unittest

from app.services.marketplace.scoring import (
    build_analysis,
    build_opportunity_segments,
    relevance_score,
    score_platform,
)


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


def nail_samples(titles: list[str]):
    return [
        {
            "title": title,
            "price": 6 + (index % 6),
            "sold_count": 150 + index * 20,
            "rating": 4.5 + (index % 4) / 10,
            "review_count": 25 + index,
            "is_sponsored": index % 5 == 0,
        }
        for index, title in enumerate(titles)
    ]


class ScoringTests(unittest.TestCase):
    def test_missing_core_values_never_produce_strong_verdict(self):
        missing = score_platform(
            [{"title": "water bottle", "price": 15 + index} for index in range(20)],
            keyword="water bottle",
        )
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
        result = build_analysis(
            "water bottle",
            {
                "shopee": samples(15),
                "lazada": [{"title": "water bottle", "price": 10 + i} for i in range(15)],
            },
        )
        self.assertIsNone(result["opportunity_score"])
        self.assertEqual(result["verdict"], "数据不足")

    def test_irrelevant_search_drift_is_excluded(self):
        items = samples(10) + samples(10, title="replacement bottle cap")
        result = score_platform(items, keyword="water bottle")
        self.assertEqual(result["raw_sample_size"], 20)
        self.assertEqual(result["sample_size"], 10)
        self.assertEqual(result["excluded_irrelevant"], 10)
        self.assertEqual(result["exclusion_breakdown"]["low_relevance"], 10)

    def test_accessory_with_exact_keyword_is_excluded(self):
        items = samples(10) + samples(6, title="silicone cover for water bottle")
        result = score_platform(items, keyword="water bottle")
        self.assertEqual(result["sample_size"], 10)
        self.assertEqual(result["exclusion_breakdown"]["accessory"], 6)
        self.assertLess(relevance_score("water bottle", "water bottle replacement lid"), 0.6)

    def test_product_with_included_accessory_is_not_misclassified(self):
        self.assertEqual(relevance_score("water bottle", "water bottle with cover 1L"), 1.0)
        self.assertEqual(relevance_score("water bottle", "water bottle with strap 750ml"), 1.0)

    def test_multipacks_do_not_distort_unit_price_scoring(self):
        items = samples(10) + samples(5, title="2pcs water bottle bundle")
        result = score_platform(items, keyword="water bottle")
        self.assertEqual(result["sample_size"], 10)
        self.assertEqual(result["exclusion_breakdown"]["bundle"], 5)
        self.assertLess(relevance_score("water bottle", "2pcs water bottle bundle"), 0.6)
        self.assertLess(relevance_score("water bottle", "2 units water bottle"), 0.6)
        self.assertLess(relevance_score("water bottle", "water bottle 1+1 deal"), 0.6)
        self.assertLess(relevance_score("water bottle", "buy 1 free 1 water bottle"), 0.6)
        self.assertEqual(relevance_score("water bottle set", "2pcs water bottle set"), 1.0)
        self.assertEqual(relevance_score("aircon unit", "aircon unit inverter"), 1.0)

    def test_single_ascii_token_requires_token_boundary(self):
        self.assertEqual(relevance_score("pen", "ballpoint pen blue"), 1.0)
        self.assertLess(relevance_score("pen", "pencil case"), 0.6)

    def test_relevance_guard_requires_phrase_evidence(self):
        self.assertEqual(relevance_score("water bottle", "water bottle stainless 1L"), 1.0)
        self.assertLess(relevance_score("water bottle", "replacement bottle cap"), 0.6)

    def test_nail_sticker_localized_aliases_are_core_products(self):
        for title in (
            "Disney Nail Sticker Floral",
            "Cute Toenail Sticker Set",
            "Pelekat Kuku Bunga",
            "Sanrio 美甲贴纸 Nail Sticker Cute",
        ):
            with self.subTest(title=title):
                self.assertGreaterEqual(relevance_score("指甲贴", title), 0.6)
        for title in ("Henna Nail Art Kit", "Moxibustion Health Patch"):
            with self.subTest(drift_title=title):
                self.assertLess(relevance_score("指甲贴", title), 0.6)

    def test_nail_sticker_breakdown_keeps_aliases_but_rejects_drift_and_bundles(self):
        core_titles = [
            "Disney Nail Sticker Floral",
            "Cute Toenail Sticker Glitter",
            "Pelekat Kuku Bunga",
            "Sanrio 美甲贴纸 Nail Sticker Cute",
        ] * 3
        items = nail_samples(core_titles + [
            "Henna Nail Art Kit",
            "Moxibustion Health Patch",
            "6pcs Nail Sticker Bundle",
        ])

        result = score_platform(items, keyword="指甲贴")

        self.assertEqual(result["raw_sample_size"], 15)
        self.assertEqual(result["sample_size"], 12)
        self.assertEqual(
            result["exclusion_breakdown"],
            {"accessory": 0, "bundle": 1, "low_relevance": 2},
        )
        self.assertTrue(result["eligible"])

    def test_water_bottle_sticker_remains_an_accessory(self):
        result = score_platform(
            samples(10) + samples(1, title="Water Bottle Sticker Waterproof"),
            keyword="water bottle",
        )
        self.assertEqual(result["sample_size"], 10)
        self.assertEqual(result["exclusion_breakdown"]["accessory"], 1)
        self.assertLess(relevance_score("water bottle", "Water Bottle Sticker Waterproof"), 0.6)

    def test_extreme_price_spread_is_not_automatically_best(self):
        items = samples(20)
        for index, item in enumerate(items):
            item["price"] = 5 if index < 10 else 200
        result = score_platform(items, keyword="water bottle")
        self.assertLess(result["dimensions"]["price_room"], 100)

    def test_seller_concentration_penalizes_head_store_dominance(self):
        concentrated = samples(20)
        dispersed = samples(20)
        for item in concentrated:
            item["shop_id"] = "head-store"
        for index, item in enumerate(dispersed):
            item["shop_id"] = f"seller-{index}"

        concentrated_result = score_platform(concentrated, keyword="water bottle")
        dispersed_result = score_platform(dispersed, keyword="water bottle")

        self.assertGreater(
            concentrated_result["metrics"]["seller_concentration"],
            dispersed_result["metrics"]["seller_concentration"],
        )
        self.assertLess(concentrated_result["score"], dispersed_result["score"])
        self.assertEqual(concentrated_result["metrics"]["seller_count"], 1)
        self.assertEqual(dispersed_result["metrics"]["seller_count"], 20)

    def test_partial_seller_identity_coverage_reduces_concentration_weight(self):
        full = samples(20)
        partial = samples(20)
        for item in full:
            item["shop_id"] = "head-store"
        for index, item in enumerate(partial[:10]):
            item["shop_id"] = "head-store"

        full_result = score_platform(full, keyword="water bottle")
        partial_result = score_platform(partial, keyword="water bottle")

        self.assertEqual(full_result["metrics"]["seller_concentration"], 100.0)
        self.assertEqual(partial_result["metrics"]["seller_concentration"], 100.0)
        self.assertEqual(full_result["metrics"]["seller_evidence_reliability"], 100.0)
        self.assertEqual(partial_result["metrics"]["seller_evidence_reliability"], 62.5)
        # Same observed monopoly, but only half the rows have a seller identity. It should
        # influence the platform score less strongly instead of pretending coverage is complete.
        self.assertGreater(partial_result["score"], full_result["score"])

    def test_low_seller_coverage_does_not_emit_strong_concentration_warning(self):
        items = samples(20)
        for item in items[:10]:
            item["shop_id"] = "head-store"
        analysis = build_analysis("water bottle", {"shopee": items})
        seller_warnings = [text for text in analysis["recommendations"] if "头部集中度" in text]
        self.assertEqual(seller_warnings, [])

    def test_small_sample_top5_floor_does_not_fake_concentration(self):
        items = samples(5, sold_base=100)
        for index, item in enumerate(items):
            item["shop_id"] = f"seller-{index}"
            item["sold_count"] = 100
        result = score_platform(items, keyword=None, min_sample_size=4)
        self.assertEqual(result["metrics"]["top5_seller_listing_share"], 100.0)
        self.assertEqual(result["metrics"]["top5_seller_demand_share"], 100.0)
        self.assertEqual(result["metrics"]["seller_listing_hhi"], 0.0)
        self.assertEqual(result["metrics"]["seller_demand_hhi"], 0.0)
        self.assertEqual(result["metrics"]["seller_concentration"], 0.0)

    def test_missing_seller_identity_does_not_invent_concentration(self):
        result = score_platform(samples(20), keyword="water bottle")
        self.assertIsNone(result["metrics"]["seller_concentration"])
        self.assertEqual(result["metrics"]["seller_identity_coverage"], 0.0)
        self.assertEqual(result["metrics"]["seller_evidence_reliability"], 0.0)

    def test_repeated_title_attributes_create_rankable_product_segments(self):
        items = (
            samples(6, sold_base=600, title="water bottle stainless thermal")
            + samples(6, sold_base=250, title="water bottle kids straw")
            + samples(6, sold_base=80, title="water bottle glass portable")
        )
        segments = build_opportunity_segments("water bottle", {"shopee": items})
        labels = {segment["label"] for segment in segments}

        self.assertGreaterEqual(len(segments), 3)
        self.assertTrue({"stainless", "thermal"} & labels)
        self.assertTrue({"kids", "straw"} & labels)
        self.assertTrue({"glass", "portable"} & labels)
        self.assertTrue(all(segment["sample_size"] >= 4 for segment in segments))
        self.assertGreaterEqual(segments[0]["opportunity_score"], segments[-1]["opportunity_score"])

    def test_analysis_exposes_product_segment_ranking(self):
        items = (
            samples(8, sold_base=700, title="water bottle stainless thermal")
            + samples(8, sold_base=180, title="water bottle kids straw")
        )
        analysis = build_analysis("water bottle", {"shopee": items})
        self.assertIn("opportunity_segments", analysis)
        self.assertTrue(analysis["opportunity_segments"])
        self.assertIn("representative_titles", analysis["opportunity_segments"][0])

    def test_localized_product_family_tokens_do_not_become_segments(self):
        items = nail_samples(
            [f"Nail Sticker Floral Design {index}" for index in range(8)]
            + [f"Pelekat Kuku Glitter Design {index}" for index in range(8)]
        )

        segments = build_opportunity_segments("指甲贴", {"lazada": items})
        tokens = {str(segment.get("token") or "").lower() for segment in segments}

        self.assertTrue(segments)
        self.assertTrue({"floral", "glitter"} & tokens)
        self.assertTrue(
            tokens.isdisjoint({"nail", "sticker", "stickers", "toenail", "decal", "decals", "pelekat", "kuku"})
        )


if __name__ == "__main__":
    unittest.main()
