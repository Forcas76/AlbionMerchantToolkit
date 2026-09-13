from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.core.database import connect_database
from app.services.favorites import favorite_ids, list_favorites, toggle_favorite


class FavoriteTests(unittest.TestCase):
    def test_favorites_are_separate_per_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.db"
            with closing(sqlite3.connect(catalog)) as setup:
                with setup:
                    setup.execute(
                        """CREATE TABLE items(
                            id INTEGER PRIMARY KEY, uniquename TEXT,
                            name_en TEXT, tier INTEGER, enchantment INTEGER
                        )"""
                    )
                    setup.execute(
                        "INSERT INTO items VALUES(1,'T4_TEST','Test Item',4,0)"
                    )
            with closing(
                connect_database(catalog, root / "market.db", root / "user.db")
            ) as conn:
                self.assertTrue(toggle_favorite(conn, "price", "T4_TEST"))
                self.assertTrue(toggle_favorite(conn, "crafting", "T4_TEST"))
                self.assertEqual(favorite_ids(conn, "price"), {"T4_TEST"})
                self.assertEqual(len(list_favorites(conn)), 2)
                self.assertFalse(toggle_favorite(conn, "price", "T4_TEST"))
                self.assertEqual(favorite_ids(conn, "price"), set())
                self.assertEqual(favorite_ids(conn, "crafting"), {"T4_TEST"})


if __name__ == "__main__":
    unittest.main()
