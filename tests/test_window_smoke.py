from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.ui.qt_app import AlbionWindow, open_db


class WindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_window_builds_with_split_databases(self) -> None:
        window = AlbionWindow()
        self.app.processEvents()
        self.assertGreaterEqual(window.pages.count(), 5)
        self.assertTrue(window.item_splitter.childrenCollapsible() is False)
        with open_db() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT market_id,enchantment FROM items WHERE uniquename=?",
                    ("T4_LEATHER_LEVEL1",),
                ).fetchone(),
                ("T4_LEATHER_LEVEL1@1", 1),
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0],
                478_840,
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
        self.assertEqual(set(window.dashboard_favorite_tables), {"price", "crafting", "flip"})
        window.close()


if __name__ == "__main__":
    unittest.main()
