"""Input widgets protected from accidental hover-wheel value changes."""

from __future__ import annotations

from PyQt6.QtGui import QFocusEvent, QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import QComboBox, QSpinBox


class SafeSpinBox(QSpinBox):
    """Accept wheel changes only after an explicit click, until focus is lost."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._wheel_armed = False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._wheel_armed = True
        super().mousePressEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self._wheel_armed = False
        super().focusOutEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not self._wheel_armed:
            event.ignore()
            return
        super().wheelEvent(event)


class SafeComboBox(QComboBox):
    """Prevent a hovered closed combo box from changing with the mouse wheel."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._wheel_armed = False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._wheel_armed = True
        super().mousePressEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self._wheel_armed = False
        super().focusOutEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not self._wheel_armed:
            event.ignore()
            return
        super().wheelEvent(event)
