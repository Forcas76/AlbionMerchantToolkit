from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.core.database import connect_database
from app.services.market_api import fetch_item_ids, save_prices


class MarketIdentityStorageTests(unittest.TestCase):
    def test_market_id_is_fetched_and_maps_back_to_catalogue_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.db"
            with closing(sqlite3.connect(catalog)) as setup:
                with setup:
                    setup.execute(
                        """CREATE TABLE items(
                               id INTEGER PRIMARY KEY, uniquename TEXT UNIQUE,
                               market_id TEXT, enchantment INTEGER
                           )"""
                    )
                    setup.execute(
                        "INSERT INTO items VALUES(1,'T4_LEATHER_LEVEL1','T4_LEATHER_LEVEL1@1',1)"
                    )
            with closing(
                connect_database(catalog, root / "market.db", root / "user.db")
            ) as conn:
                self.assertEqual(fetch_item_ids(conn), ["T4_LEATHER_LEVEL1@1"])
                saved = save_prices(
                    conn,
                    [{
                        "item_id": "T4_LEATHER_LEVEL1@1",
                        "city": "Lymhurst",
                        "quality": 1,
                        "sell_price_min": 99,
                    }],
                )
                self.assertEqual(saved, 1)
                row = conn.execute(
                    "SELECT item_uniquename,enchantment,sell_price_min FROM market_prices"
                ).fetchone()
                self.assertEqual(row, ("T4_LEATHER_LEVEL1", 1, 99))


if __name__ == "__main__":
    unittest.main()
