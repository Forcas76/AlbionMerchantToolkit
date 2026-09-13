from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.ui.category_picker import CategoryPopupButton


class CategoryPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_tree_is_rendered_in_popup_and_selects_full_path(self) -> None:
        picker = CategoryPopupButton()
        picker.set_categories(
            [
                ("armors", "armors", None, 0),
                ("armors/cloth_armor", "cloth_armor", "armors", 0),
            ],
            {("armors",): 20, ("armors", "cloth_armor"): 8},
        )
        self.assertTrue(picker.popup.windowFlags() & Qt.WindowType.Popup)
        child = picker.tree.topLevelItem(0).child(0).child(0)
        picker._choose_item(child)
        self.assertEqual(picker.selected_path, ("armors", "cloth_armor"))
        self.assertIn("Cloth Armor", picker.text())


if __name__ == "__main__":
    unittest.main()
