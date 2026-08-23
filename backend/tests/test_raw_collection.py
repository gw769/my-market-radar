import unittest

from app.services.marketplace.raw_collection import RawCardAccumulator, raw_card_key


class RawCardAccumulatorTests(unittest.TestCase):
    def test_keeps_products_from_multiple_scroll_rounds_in_first_seen_order(self):
        acc = RawCardAccumulator(max_cards=20)
        acc.add([
            {"item_id": "1", "title": "Water Bottle A", "price": "RM 20"},
            {"item_id": "2", "title": "Water Bottle B", "price": "RM 25"},
        ])
        acc.add([
            {"item_id": "3", "title": "Water Bottle C", "price": "RM 30"},
            {"item_id": "4", "title": "Water Bottle D", "price": "RM 35"},
        ])
        self.assertEqual([row["item_id"] for row in acc.cards()], ["1", "2", "3", "4"])

    def test_repeated_product_enriches_missing_fields_without_changing_rank_order(self):
        acc = RawCardAccumulator(max_cards=20)
        acc.add([
            {"item_id": "1", "title": "Water Bottle A", "sold": None},
            {"item_id": "2", "title": "Water Bottle B", "sold": "10 sold"},
        ])
        acc.add([
            {"item_id": "2", "title": "Water Bottle B", "sold": "15 sold"},
            {"item_id": "1", "title": "Water Bottle A - Stainless Steel", "sold": "100 sold"},
        ])
        rows = acc.cards()
        self.assertEqual([row["item_id"] for row in rows], ["1", "2"])
        self.assertEqual(rows[0]["sold"], "100 sold")
        self.assertEqual(rows[0]["title"], "Water Bottle A - Stainless Steel")
        self.assertEqual(rows[1]["sold"], "15 sold")

    def test_tracking_query_does_not_duplicate_same_href(self):
        first = {"href": "https://shopee.com.my/product/123?sp_atk=aaa", "title": "Bottle"}
        second = {"href": "https://shopee.com.my/product/123?utm_source=test", "title": "Bottle"}
        self.assertEqual(raw_card_key(first), raw_card_key(second))
        acc = RawCardAccumulator()
        acc.add([first])
        acc.add([second])
        self.assertEqual(len(acc), 1)

    def test_item_id_is_preferred_over_changing_href(self):
        acc = RawCardAccumulator()
        acc.add([{"item_id": "42", "href": "https://example.test/a", "title": "Bottle"}])
        acc.add([{"item_id": "42", "href": "https://example.test/b", "sold": "9 sold"}])
        self.assertEqual(len(acc), 1)
        self.assertEqual(acc.cards()[0]["sold"], "9 sold")


if __name__ == "__main__":
    unittest.main()
