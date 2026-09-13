from __future__ import annotations

import unittest

from app.domain.item_identity import normalize_item_identity


class ItemIdentityTests(unittest.TestCase):
    def test_explicit_resource_enchantment_creates_market_id(self) -> None:
        result = normalize_item_identity(
            "T4_LEATHER_LEVEL1", "1", "crafting", "refinedresources"
        )
        self.assertEqual(result.catalogue_id, "T4_LEATHER_LEVEL1")
        self.assertEqual(result.market_id, "T4_LEATHER_LEVEL1@1")
        self.assertEqual(result.enchantment, 1)

    def test_explicit_zero_blocks_level_fallback(self) -> None:
        result = normalize_item_identity("T1_FISHSAUCE_LEVEL2", "0", "products", "food")
        self.assertEqual(result.market_id, "T1_FISHSAUCE_LEVEL2")
        self.assertEqual(result.enchantment, 0)

    def test_resource_level_is_used_only_as_missing_metadata_fallback(self) -> None:
        result = normalize_item_identity(
            "T5_TEST_LEVEL3", None, "crafting", "resources"
        )
        self.assertEqual((result.market_id, result.enchantment), ("T5_TEST_LEVEL3@3", 3))
        ordinary = normalize_item_identity("T5_TEST_LEVEL3", None, "equipment", "mainhand")
        self.assertEqual((ordinary.market_id, ordinary.enchantment), ("T5_TEST_LEVEL3", 0))

    def test_at_suffix_is_already_a_market_id(self) -> None:
        result = normalize_item_identity("T4_MAIN_SWORD@2", 0, "equipment", "mainhand")
        self.assertEqual((result.market_id, result.enchantment), ("T4_MAIN_SWORD@2", 2))


if __name__ == "__main__":
    unittest.main()
