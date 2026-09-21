from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.ui.inventory_cards import InventoryCard, InventoryCardGrid, InventoryPickerDialog


class FakeIconLoader:
    def request_icon(self, _url, callback) -> None:
        callback(None)


class InventoryCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_card_edits_stack_quantity_and_emits_quality(self) -> None:
        card = InventoryCard(
            (
                "T4_TEST", "Test item", 1000, "now", 4,
                "equipment", "melee", "sword", "", 0, 3,
            ),
            FakeIconLoader(),
        )
        saved: list[tuple] = []
        prices: list[tuple] = []
        card.save_requested.connect(lambda *values: saved.append(values))
        card.price_requested.connect(lambda *values: prices.append(values))
        self.assertEqual((card.stacks.value(), card.loose.value()), (1, 1))
        card.loose.setValue(7)
        card._save()
        self.assertEqual(saved, [("T4_TEST", 3, 1006)])
        card.price_button.click()
        self.assertEqual(prices, [("T4_TEST", 3)])

    def test_picker_uses_cards_and_selected_quality(self) -> None:
        rows = [("T4_TEST", "Test item", 4, "test", 0)]
        dialog = InventoryPickerDialog(
            FakeIconLoader(), lambda *_args: rows, [], {},
        )
        self.assertEqual(len(dialog.results.cards), 1)
        dialog.quality.setCurrentIndex(3)
        dialog._select(rows[0])
        self.assertEqual(dialog.selected_quality(), 4)
        self.assertTrue(dialog.add_button.isEnabled())
        dialog.close()

    def test_inventory_builds_only_non_empty_category_branches(self) -> None:
        grid = InventoryCardGrid(FakeIconLoader())
        grid.set_inventory([
            (
                "T4_SWORD", "Sword", 1, "now", 4,
                "equipment", "melee", "sword", "", 0, 1,
            ),
            (
                "T4_LEATHER", "Leather", 999, "now", 4,
                "materials", "hide", "leather", "", 0, 1,
            ),
        ])
        self.assertEqual(
            {section.path for section in grid.sections},
            {
                ("equipment", "melee", "sword"),
                ("materials", "hide", "leather"),
            },
        )
        equipment = next(
            section for section in grid.sections
            if section.path == ("equipment", "melee", "sword")
        )
        self.assertFalse(equipment.body.isHidden())
        equipment.toggle.click()
        self.assertFalse(equipment.body.isVisible())
        grid.close()


if __name__ == "__main__":
    unittest.main()
