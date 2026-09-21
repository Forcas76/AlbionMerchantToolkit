"""Responsive refining recipe cards with lazy item icons."""

from __future__ import annotations

import weakref

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.item_cards import ItemIconLoader, item_icon_url


class RefiningRecipeCard(QFrame):
    selected = pyqtSignal(int)

    def __init__(self, index: int, row: tuple, loader: ItemIconLoader) -> None:
        super().__init__()
        self.index = index
        self.row = row
        self.loader = loader
        self.icon_requested = False
        item_id, name, tier, material_count, output_amount, variant = row
        self.setObjectName("refiningRecipeCard")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(300, 108)
        self.setMaximumHeight(122)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 11, 12, 11)
        root.setSpacing(12)
        self.icon = QLabel(str(tier))
        self.icon.setObjectName("refiningRecipeIcon")
        self.icon.setFixedSize(72, 72)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.icon)
        body = QVBoxLayout()
        body.setSpacing(4)
        title_row = QHBoxLayout()
        title = QLabel(str(name or item_id))
        title.setObjectName("refiningRecipeTitle")
        title.setWordWrap(True)
        variant_badge = QLabel(f"Recept {int(variant) + 1}")
        variant_badge.setObjectName("refiningRecipeBadge")
        title_row.addWidget(title, 1)
        title_row.addWidget(variant_badge, 0, Qt.AlignmentFlag.AlignTop)
        facts = QLabel(f"{tier}  ·  {material_count}  ·  {output_amount}")
        facts.setObjectName("refiningRecipeMeta")
        identifier = QLabel(str(item_id))
        identifier.setObjectName("refiningRecipeId")
        identifier.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addLayout(title_row)
        body.addWidget(facts)
        body.addStretch()
        body.addWidget(identifier)
        root.addLayout(body, 1)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def load_icon(self) -> None:
        if self.icon_requested:
            return
        self.icon_requested = True
        target = weakref.ref(self.icon)

        def apply(pixmap: QPixmap | None) -> None:
            label = target()
            if label is None:
                return
            try:
                if pixmap is None:
                    label.setText("Nincs kép")
                else:
                    label.setText("")
                    label.setPixmap(pixmap.scaled(
                        label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
            except RuntimeError:
                return

        self.loader.request_icon(item_icon_url(str(self.row[0]), 1, 96), apply)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.index)
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.selected.emit(self.index)
            event.accept()
            return
        super().keyPressEvent(event)


class RefiningRecipeCardGrid(QScrollArea):
    selected = pyqtSignal(int)

    def __init__(self, loader: ItemIconLoader) -> None:
        super().__init__()
        self.loader = loader
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("refiningRecipeScroll")
        self.setMinimumHeight(250)
        self.setMaximumHeight(390)
        self.content = QWidget()
        self.content.setObjectName("refiningRecipeContent")
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 2, 4, 2)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(self.content)
        self.cards: list[RefiningRecipeCard] = []
        self.selected_index = -1
        self.verticalScrollBar().valueChanged.connect(self._schedule_lazy_load)
        self._lazy_timer = QTimer(self)
        self._lazy_timer.setSingleShot(True)
        self._lazy_timer.setInterval(40)
        self._lazy_timer.timeout.connect(self.load_visible_icons)

    def set_recipes(self, rows: list[tuple]) -> None:
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        while self.grid.takeAt(0) is not None:
            pass
        self.cards = []
        self.selected_index = -1
        for index, row in enumerate(rows):
            card = RefiningRecipeCard(index, row, self.loader)
            card.selected.connect(self._select)
            self.cards.append(card)
        self._relayout()
        self.verticalScrollBar().setValue(0)
        self._schedule_lazy_load()

    def _select(self, index: int) -> None:
        self.selected_index = index
        for number, card in enumerate(self.cards):
            card.set_selected(number == index)
        self.selected.emit(index)

    def _relayout(self) -> None:
        while self.grid.takeAt(0) is not None:
            pass
        columns = max(1, self.viewport().width() // 330)
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self.grid.setColumnStretch(column, 1)

    def _schedule_lazy_load(self) -> None:
        self._lazy_timer.start()

    def load_visible_icons(self) -> None:
        preload_area = self.viewport().rect().adjusted(0, -220, 0, 220)
        for card in self.cards:
            position = card.mapTo(self.viewport(), QPoint(0, 0))
            if QRect(position, card.size()).intersects(preload_area):
                card.load_icon()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()
        self._schedule_lazy_load()
