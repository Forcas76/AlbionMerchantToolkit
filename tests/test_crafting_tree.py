from __future__ import annotations

import sqlite3
import unittest

from app.services.crafting import build_crafting_dependency_tree


class CraftingDependencyTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE items(id INTEGER PRIMARY KEY, uniquename TEXT, name_en TEXT);
            CREATE TABLE recipes(
                id INTEGER PRIMARY KEY, item_uniquename TEXT, variant_index INTEGER,
                output_amount INTEGER, silver_cost INTEGER
            );
            CREATE TABLE recipe_materials(
                recipe_id INTEGER, material_uniquename TEXT, amount INTEGER
            );
            CREATE TABLE market_prices(
                item_uniquename TEXT, city TEXT, sell_price_min INTEGER
            );
            INSERT INTO items VALUES
                (1, 'T4_SWORD', 'Adept Sword'),
                (2, 'T4_PLANKS', 'Pine Planks'),
                (3, 'T4_WOOD', 'Pine Logs');
            INSERT INTO recipes VALUES
                (10, 'T4_SWORD', 0, 1, 20),
                (20, 'T4_PLANKS', 0, 2, 5);
            INSERT INTO recipe_materials VALUES
                (10, 'T4_PLANKS', 4),
                (20, 'T4_WOOD', 3);
            INSERT INTO market_prices VALUES
                ('T4_PLANKS', 'Lymhurst', 100),
                ('T4_WOOD', 'Lymhurst', 10);
            """
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_inventory_reduces_nested_crafting_without_reducing_root(self) -> None:
        root = build_crafting_dependency_tree(
            self.conn,
            "T4_SWORD",
            2,
            cities=["Lymhurst"],
            inventory={"T4_SWORD": 99, "T4_PLANKS": 3},
        )
        self.assertEqual((root.required_quantity, root.inventory_used, root.batches), (2, 0, 2))
        planks = root.children[0]
        self.assertEqual(
            (planks.required_quantity, planks.inventory_used, planks.missing_quantity, planks.batches),
            (8, 3, 5, 3),
        )
        wood = planks.children[0]
        self.assertEqual((wood.required_quantity, wood.missing_quantity), (9, 9))


if __name__ == "__main__":
    unittest.main()
