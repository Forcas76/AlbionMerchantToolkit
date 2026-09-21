from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QFocusEvent
from PyQt6.QtWidgets import QApplication

from app.domain.game_rules import market_fee_policy
from app.ui.inventory_price_cards import InventoryValuationCard
from app.ui.safe_inputs import SafeSpinBox


class IgnoredWheelEvent:
    def __init__(self) -> None:
        self.ignored = False

    def ignore(self) -> None:
        self.ignored = True


class InventoryPriceCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_custom_unit_price_recalculates_whole_inventory_after_fees(self) -> None:
        row = (
            "Lymhurst", 1, 0,
            120, "2026-01-01T00:00:00Z", 140, "2026-01-01T00:00:00Z",
            90, "2026-01-01T00:00:00Z", 100, "2026-01-01T00:00:00Z",
        )
        card = InventoryValuationCard(row, 10, market_fee_policy(True))
        card.unit_price.setValue(100)
        self.assertEqual(card.gross.text(), "1,000 silver")
        self.assertEqual(card.tax.text(), "− 40 silver")
        self.assertEqual(card.setup.text(), "− 25 silver")
        self.assertEqual(card.net.text(), "935 silver")
        self.assertIn("960 nettó", card.instant_result.text())

    def test_price_wheel_is_locked_until_click_and_relocks_on_focus_loss(self) -> None:
        field = SafeSpinBox()
        event = IgnoredWheelEvent()
        field.wheelEvent(event)  # type: ignore[arg-type]
        self.assertTrue(event.ignored)
        field._wheel_armed = True
        field.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        self.assertFalse(field._wheel_armed)


if __name__ == "__main__":
    unittest.main()
