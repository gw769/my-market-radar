import json
import unittest
from pathlib import Path

from app.services.marketplace.adapters import (
    LazadaMalaysiaAdapter,
    ShopeeMalaysiaAdapter,
    parse_compact_count,
    parse_money,
)

FIXTURES = Path(__file__).parent / "fixtures"


class AdapterTests(unittest.TestCase):
    def test_formats_keep_public_values(self):
        self.assertEqual(parse_money("Bottle 12oz RM 19.90"), 19.90)
        self.assertEqual(parse_compact_count("1.2k sold"), 1200)
        self.assertEqual(parse_compact_count("2m terjual"), 2_000_000)
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

    def test_lazada_json_parse_discount_and_page_variant(self):
        cards = json.loads((FIXTURES / "lazada_cards.json").read_text())
        rows = LazadaMalaysiaAdapter().parse_cards(cards, 20)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].discount_percent, 20.0)
        self.assertEqual(rows[0].sold_count, 2500)
        self.assertEqual(rows[0].review_count, 1100)
        self.assertEqual(rows[1].item_id, "3002")
        self.assertIsNone(rows[1].original_price)

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
        # Python parsing intentionally does not infer a rating from arbitrary card text.
        self.assertIsNone(row.rating)

    def test_verification_html_is_detected(self):
        html = (FIXTURES / "verification_page.html").read_text()
        adapter = ShopeeMalaysiaAdapter()
        self.assertTrue(adapter.is_verification_page("https://shopee.com.my/search", html))
        self.assertTrue(adapter.is_verification_page("https://shopee.xiapibuy.com/verify", ""))
        self.assertFalse(adapter.is_verification_page("https://shopee.com.my/search", "No products found"))


if __name__ == "__main__":
    unittest.main()
