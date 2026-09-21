from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QFocusEvent
from PyQt6.QtWidgets import QApplication

from app.ui.safe_inputs import SafeComboBox, SafeSpinBox


class IgnoredWheelEvent:
    def __init__(self) -> None:
        self.ignored = False

    def ignore(self) -> None:
        self.ignored = True


class SafeInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_spinbox_ignores_hover_wheel_and_relocks(self) -> None:
        field = SafeSpinBox()
        event = IgnoredWheelEvent()
        field.wheelEvent(event)  # type: ignore[arg-type]
        self.assertTrue(event.ignored)
        field._wheel_armed = True
        field.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        self.assertFalse(field._wheel_armed)

    def test_combobox_ignores_hover_wheel_and_relocks(self) -> None:
        field = SafeComboBox()
        event = IgnoredWheelEvent()
        field.wheelEvent(event)  # type: ignore[arg-type]
        self.assertTrue(event.ignored)
        field._wheel_armed = True
        field.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        self.assertFalse(field._wheel_armed)


if __name__ == "__main__":
    unittest.main()
