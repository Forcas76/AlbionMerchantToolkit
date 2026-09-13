from __future__ import annotations

import unittest

from app.ui.item_cards import item_icon_url


class ItemIconUrlTests(unittest.TestCase):
    def test_enchanted_item_url_is_plain_and_valid(self) -> None:
        url = item_icon_url("T4_BAG@2", quality=4, size=128)
        self.assertEqual(
            url,
            "https://render.albiononline.com/v1/item/T4_BAG@2.png?quality=4&size=128",
        )
        self.assertNotIn("[", url)

    def test_invalid_quality_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            item_icon_url("T4_BAG", quality=0)


if __name__ == "__main__":
    unittest.main()
