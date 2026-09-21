from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.core.database import connect_database
from app.paths import CATALOG_DB_FILE
from app.services.market_api import save_prices
from app.ui import qt_app


class WindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.catalog = root / "catalog.db"
        self.market = root / "market.db"
        self.user = root / "user.db"
        shutil.copy2(CATALOG_DB_FILE, self.catalog)

        def open_test_database():
            return connect_database(self.catalog, self.market, self.user)

        self.database_patch = patch.object(
            qt_app, "open_db", side_effect=open_test_database
        )
        self.database_patch.start()
        with qt_app.open_db() as conn:
            save_prices(
                conn,
                [{
                    "item_id": "T4_LEATHER_LEVEL1@1",
                    "city": "Lymhurst",
                    "quality": 1,
                    "sell_price_min": 100,
                    "buy_price_max": 90,
                }],
            )

    def tearDown(self) -> None:
        self.database_patch.stop()
        self.temporary_directory.cleanup()

    def test_window_builds_with_split_databases(self) -> None:
        window = qt_app.AlbionWindow()
        self.app.processEvents()
        self.assertEqual(window.pages.count(), 10)
        self.assertTrue(window.item_splitter.childrenCollapsible() is False)
        with qt_app.open_db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT market_id,enchantment FROM items WHERE uniquename=?",
                    ("T4_LEATHER_LEVEL1",),
                ).fetchone(),
                ("T4_LEATHER_LEVEL1@1", 1),
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0],
                1,
            )
        window.show_craft()
        window.search_recipes()
        self.assertGreater(len(window.craft_cards.cards), 0)
        window.select_craft_card(window.craft_cards.cards[0].row)
        self.app.processEvents()
        self.assertFalse(window.craft_detail.isHidden())
        window.calculate_craft()
        self.assertTrue(window.craft_output.toPlainText())
        window.show_flips()
        window.search_flip_items()
        self.assertGreater(len(window.flip_cards.cards), 0)
        window.show_dashboard()
        self.assertTrue(hasattr(window, "dashboard_timeline"))
        self.assertTrue(hasattr(window, "dashboard_items"))
        window.show_orders()
        self.assertEqual(window.pages.currentIndex(), 6)
        window.show_statistics()
        self.assertEqual(window.pages.currentIndex(), 7)
        window.open_inventory_price_window("T4_LEATHER", 1)
        self.app.processEvents()
        self.assertEqual(len(window.inventory_price_windows), 1)
        for price_window in list(window.inventory_price_windows):
            price_window.close()
        if hasattr(window, "craft_detail_animation"):
            window.craft_detail_animation.stop()
        window.close()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
