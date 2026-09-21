from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.ui.refining_cards import RefiningRecipeCardGrid


class FakeIconLoader:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def request_icon(self, url, callback) -> None:
        self.requested.append(url)
        callback(None)


class RefiningCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_cards_are_unified_selectable_and_lazy_loaded(self) -> None:
        loader = FakeIconLoader()
        grid = RefiningRecipeCardGrid(loader)
        grid.resize(1040, 300)
        rows = [
            (f"T4_TEST_{index}", f"Recipe {index}", "T4", "2 alapanyag", "1 output", 0)
            for index in range(100)
        ]
        selected: list[int] = []
        grid.selected.connect(selected.append)
        grid.set_recipes(rows)
        grid.show()
        self.app.processEvents()
        grid.load_visible_icons()
        self.assertEqual(len(grid.cards), 100)
        self.assertGreater(len(loader.requested), 0)
        self.assertLess(len(loader.requested), len(rows))
        grid.cards[2].selected.emit(2)
        self.assertEqual(selected, [2])
        self.assertTrue(grid.cards[2].property("selected"))
        grid.close()


if __name__ == "__main__":
    unittest.main()
