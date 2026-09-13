from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing

from app.core.database import apply_migrations
from app.services.item_catalog import iter_category_rows, sync_item_categories


SAMPLE_DATA = {
    "items": {
        "shopcategories": {
            "shopcategory": {
                "@id": "equipment",
                "shopsubcategory": {
                    "@id": "armor",
                    "shopsubcategory2": {"@id": "cloth"},
                },
            }
        },
        "equipmentitem": {
            "@uniquename": "T4_TEST_ROBE",
            "@shopcategory": "equipment",
            "@shopsubcategory1": "armor",
            "@shopsubcategory2": "cloth",
        },
    }
}


class ItemCatalogTests(unittest.TestCase):
    def test_category_paths_keep_hierarchy(self) -> None:
        rows = list(iter_category_rows(SAMPLE_DATA))
        self.assertEqual(
            [(row[0], row[2], row[3]) for row in rows],
            [
                ("equipment", 0, None),
                ("equipment/armor", 1, "equipment"),
                ("equipment/armor/cloth", 2, "equipment/armor"),
            ],
        )

    def test_sync_updates_base_and_enchanted_items(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, uniquename TEXT)")
            conn.executemany(
                "INSERT INTO items(uniquename) VALUES (?)",
                [("T4_TEST_ROBE",), ("T4_TEST_ROBE@1",)],
            )
            conn.execute("CREATE TABLE saved_market_data(value TEXT)")
            conn.execute("INSERT INTO saved_market_data VALUES ('keep me')")
            apply_migrations(conn)
            category_count, item_count = sync_item_categories(conn, SAMPLE_DATA)
            self.assertEqual((category_count, item_count), (3, 2))
            self.assertEqual(
                conn.execute(
                    """SELECT shopcategory, shopsubcategory, shopsubcategory2
                       FROM items WHERE uniquename = 'T4_TEST_ROBE@1'"""
                ).fetchone(),
                ("equipment", "armor", "cloth"),
            )
            self.assertEqual(
                conn.execute("SELECT value FROM saved_market_data").fetchone(),
                ("keep me",),
            )


if __name__ == "__main__":
    unittest.main()
