import json
import unittest
from pathlib import Path

from app.services.marketplace.adapters import (
    LazadaMalaysiaAdapter,
    ShopeeMalaysiaAdapter,
    parse_compact_count,
    parse_money,
    parse_money_range,
)

FIXTURES = Path(__file__).parent / "fixtures"


class AdapterTests(unittest.TestCase):
    def test_formats_keep_public_values(self):
        self.assertEqual(parse_money("Bottle 12oz RM 19.90"), 19.90)
        self.assertEqual(parse_compact_count("1.2k+ sold"), 1200)
        self.assertEqual(parse_compact_count("2m terjual"), 2_000_000)
        self.assertEqual(parse_money_range("RM 10.00 - RM 25.50"), (10.0, 25.5))
        self.assertEqual(parse_money_range("RM 25 ~ 10"), (10.0, 25.0))
        self.assertIsNone(parse_money_range("RM 20.00\nRM 30.00"))
        self.assertIsNone(parse_compact_count(None))

    def test_missing_currency_does_not_turn_title_number_into_price(self):
        self.assertIsNone(parse_money("1L bottle 4.9 stars", require_currency=True))
        self.assertIsNone(parse_money("1L bottle 4.9 stars"))

    def test_shopee_json_parse_deduplicates_and_preserves_missing(self):
        cards = json.loads((FIXTURES / "shopee_cards.json").read_text())
        rows = ShopeeMalaysiaAdapter().parse_cards(cards, 20)
        self.assertEqual([row.item_id for row in rows], ["2001", "2002"])
        self.assertEqual(rows[0].shop_id, "1001")
        self.assertEqual(rows[0].price, 19.9)
        self.assertEqual(rows[0].sold_count, 1200)
        self.assertEqual(rows[0].review_count, 345)
        self.assertTrue(rows[0].is_sponsored)
        self.assertIsNone(rows[1].rating)

    def test_plus_sold_and_review_counts_survive_python_fallback(self):
        raw = {
            "href": "https://shopee.com.my/Bottle-i.1001.9100",
            "title": "Bottle",
            "text": "Bottle\nRM 20.00\n1.2k+ sold\n4.8 rating\n2.4k+ reviews\nSelangor",
            "price": "RM 20.00",
        }
        row = ShopeeMalaysiaAdapter().parse_card(raw, 1)
        self.assertEqual(row.sold_count, 1200)
        self.assertEqual(row.review_count, 2400)

    def test_variant_price_range_is_not_scored_as_minimum_price(self):
        shopee = ShopeeMalaysiaAdapter().parse_card({
            "href": "https://shopee.com.my/Bottle-i.1001.9200",
            "title": "Bottle variants",
            "text": "Bottle variants\nRM 10.00 - RM 40.00\n500 sold",
            "price": "RM 10.00",
            "sold": "500 sold",
        }, 1)
        self.assertIsNone(shopee.price)

        lazada = LazadaMalaysiaAdapter().parse_card({
            "href": "https://www.lazada.com.my/products/bottle-i9201-s.html",
            "item_id": "9201",
            "title": "Bottle variants",
            "text": "Bottle variants\nRM 15.00 – RM 35.00\n300 sold",
            "price": "RM 15.00",
            "sold": "300 sold",
        }, 1)
        self.assertIsNone(lazada.price)

    def test_lazada_json_parse_discount_and_page_variant(self):
        cards = json.loads((FIXTURES / "lazada_cards.json").read_text())
        rows = LazadaMalaysiaAdapter().parse_cards(cards, 20)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].discount_percent, 20.0)
        self.assertEqual(rows[0].sold_count, 2500)
        self.assertEqual(rows[0].review_count, 1100)
        self.assertEqual(rows[1].item_id, "3002")
        self.assertIsNone(rows[1].original_price)

    def test_lazada_invalid_original_price_is_dropped(self):
        row = LazadaMalaysiaAdapter().parse_card({
            "href": "https://www.lazada.com.my/products/bottle-i9300-s.html",
            "item_id": "9300",
            "title": "Bottle",
            "text": "Bottle\nRM 30.00\nRM 20.00\n100 sold",
            "price": "RM 30.00",
            "original_price": "RM 20.00",
            "sold": "100 sold",
        }, 1)
        self.assertEqual(row.price, 30.0)
        self.assertIsNone(row.original_price)
        self.assertIsNone(row.discount_percent)

    def test_sponsored_label_is_not_saved_as_location(self):
        row = LazadaMalaysiaAdapter().parse_card({
            "href": "https://www.lazada.com.my/products/bottle-i9301-s.html",
            "item_id": "9301",
            "title": "Bottle",
            "text": "Bottle\nRM 20.00\n100 sold\nSponsored",
            "price": "RM 20.00",
            "sold": "100 sold",
            "location": "Sponsored",
            "sponsored": True,
        }, 1)
        self.assertIsNone(row.seller_location)

    def test_parenthesized_title_number_is_not_assumed_to_be_reviews(self):
        raw = {
            "href": "https://shopee.com.my/Bottle-2026-i.1001.9001",
            "title": "Bottle New Edition (2026)",
            "text": "Bottle New Edition (2026)\nRM 4.90\n120 sold",
            "price": "RM 4.90",
            "sold": "120 sold",
        }
        row = ShopeeMalaysiaAdapter().parse_card(raw, 1)
        self.assertIsNotNone(row)
        self.assertEqual(row.price, 4.9)
        self.assertIsNone(row.rating)
        self.assertIsNone(row.review_count)

    def test_explicit_review_label_is_still_parsed_without_dom_field(self):
        raw = {
            "href": "https://www.lazada.com.my/products/bottle-i9002-s.html",
            "item_id": "9002",
            "title": "Bottle 1L",
            "text": "Bottle 1L\nRM 20.00\n4.8 rating\n1.2k reviews",
            "price": "RM 20.00",
        }
        row = LazadaMalaysiaAdapter().parse_card(raw, 1)
        self.assertIsNotNone(row)
        self.assertEqual(row.review_count, 1200)
        self.assertIsNone(row.rating)

    def test_verification_html_is_detected_without_verified_seller_false_positive(self):
        html = (FIXTURES / "verification_page.html").read_text()
        adapter = ShopeeMalaysiaAdapter()
        self.assertTrue(adapter.is_verification_page("https://shopee.com.my/search", html))
        self.assertTrue(adapter.is_verification_page("https://shopee.xiapibuy.com/verify", ""))
        self.assertTrue(adapter.is_verification_page("https://shopee.com.my/verify", ""))
        self.assertTrue(adapter.is_verification_page("https://shopee.com.my/search", "Please verify your identity to continue"))
        self.assertFalse(adapter.is_verification_page("https://shopee.com.my/search", "Verified Seller · 4.9 rating · No products found"))
        self.assertFalse(adapter.is_verification_page("https://shopee.com.my/search", "Verification tools and verified accessories"))


if __name__ == "__main__":
    unittest.main()
