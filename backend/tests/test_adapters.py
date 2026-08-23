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

    def test_verification_html_is_detected(self):
        html = (FIXTURES / "verification_page.html").read_text()
        adapter = ShopeeMalaysiaAdapter()
        self.assertTrue(adapter.is_verification_page("https://shopee.com.my/search", html))
        self.assertTrue(adapter.is_verification_page("https://shopee.xiapibuy.com/verify", ""))
        self.assertFalse(adapter.is_verification_page("https://shopee.com.my/search", "No products found"))


if __name__ == "__main__":
    unittest.main()
