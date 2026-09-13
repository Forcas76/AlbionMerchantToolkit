from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.ui.item_cards import ItemCardGrid


class FakeIconLoader:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def request_icon(self, url, callback) -> None:
        self.requested.append(url)
        callback(None)


class ItemCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_lazy_loader_only_requests_visible_neighbourhood(self) -> None:
        loader = FakeIconLoader()
        grid = ItemCardGrid(loader)
        grid.resize(620, 300)
        rows = [
            (f"T4_TEST_{index}", f"Test {index}", 4, "test", 0)
            for index in range(100)
        ]
        grid.set_items(rows, quality=1)
        grid.show()
        self.app.processEvents()
        grid.load_visible_icons()
        self.assertGreater(len(loader.requested), 0)
        self.assertLess(len(loader.requested), len(rows))
        grid.close()


if __name__ == "__main__":
    unittest.main()
