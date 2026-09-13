from __future__ import annotations

import unittest

from app.services.crafting import _recipe_entries


class CraftingImportTests(unittest.TestCase):
    def test_base_variants_and_enchanted_recipe_are_extracted(self) -> None:
        item = {
            "@uniquename": "T4_TEST",
            "craftingrequirements": [
                {"craftresource": {"@uniquename": "T4_WOOD", "@count": "8"}},
                {"@amountcrafted": "2", "craftresource": []},
            ],
            "enchantments": {
                "enchantment": {
                    "@enchantmentlevel": "1",
                    "craftingrequirements": {
                        "@amountcrafted": "5",
                        "craftresource": [],
                    },
                }
            },
        }
        entries = _recipe_entries(item)
        self.assertEqual([entry[:2] for entry in entries], [
            ("T4_TEST", 0), ("T4_TEST", 1), ("T4_TEST@1", 0),
        ])
        self.assertEqual(entries[2][2]["@amountcrafted"], "5")


if __name__ == "__main__":
    unittest.main()
