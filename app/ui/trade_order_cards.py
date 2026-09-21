"""Card widgets for trade-order editing and statistics."""

from __future__ import annotations

import weakref

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from app.services.market_api import CITIES
from app.ui.item_cards import ItemIconLoader, item_icon_url
from app.ui.safe_inputs import SafeComboBox, SafeSpinBox

STATUS_LABELS = {"draft": "Tervezett", "active": "Aktív", "closed": "Lezárt"}


class TradeOrderCard(QFrame):
    selected = pyqtSignal(int)

    def __init__(self, row: tuple, loader: ItemIconLoader) -> None:
        super().__init__()
        self.row = row
        order_id, name, status, item_id, item_name, quality, quantity, sold, planned, actual, city = row[:11]
        self.setObjectName("tradeOrderCard")
        self.setProperty("status", status)
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 9, 10, 9)
        icon = QLabel("IMG")
        icon.setObjectName("tradeOrderIcon")
        icon.setFixedSize(54, 54)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(icon)
        body = QVBoxLayout()
        header = QHBoxLayout()
        title = QLabel(str(name))
        title.setObjectName("tradeOrderTitle")
        title.setWordWrap(True)
        badge = QLabel(STATUS_LABELS.get(str(status), str(status)))
        badge.setObjectName("tradeOrderStatus")
        badge.setProperty("status", status)
        header.addWidget(title, 1)
        header.addWidget(badge)
        price = actual if actual is not None else planned
        meta = QLabel(f"{item_name} · Q{quality} · {sold:,}/{quantity:,} db · {city}")
        meta.setObjectName("tradeOrderMeta")
        amount = QLabel(f"{int(price or 0):,} silver/db")
        amount.setObjectName("tradeOrderAmount")
        body.addLayout(header)
        body.addWidget(meta)
        body.addWidget(amount)
        root.addLayout(body, 1)
        target = weakref.ref(icon)

        def apply(pixmap: QPixmap | None) -> None:
            label = target()
            if label is None or pixmap is None:
                return
            try:
                label.setText("")
                label.setPixmap(pixmap.scaled(
                    label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ))
            except RuntimeError:
                return

        loader.request_icon(item_icon_url(str(item_id), int(quality), 96), apply)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(int(self.row[0]))
        super().mousePressEvent(event)


class TradeOrderCardList(QScrollArea):
    selected = pyqtSignal(int)

    def __init__(self, loader: ItemIconLoader) -> None:
        super().__init__()
        self.loader = loader
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("tradeOrderScroll")
        self.content = QWidget()
        self.layout = QVBoxLayout(self.content)
        self.layout.setContentsMargins(0, 2, 4, 2)
        self.layout.setSpacing(8)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(self.content)
        self.cards: list[TradeOrderCard] = []

    def set_orders(self, rows: list[tuple], selected_id: int | None = None) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.cards = []
        for row in rows:
            card = TradeOrderCard(row, self.loader)
            card.selected.connect(self._select)
            card.set_selected(int(row[0]) == selected_id)
            self.cards.append(card)
            self.layout.addWidget(card)

    def _select(self, order_id: int) -> None:
        for card in self.cards:
            card.set_selected(int(card.row[0]) == order_id)
        self.selected.emit(order_id)


class OrderMaterialEditor(QFrame):
    remove_requested = pyqtSignal(object)

    def __init__(
        self,
        item_id: str,
        name: str,
        quality: int,
        purchased: int = 0,
        inventory: int = 0,
        unit_price: int = 0,
        city: str = "Caerleon",
    ) -> None:
        super().__init__()
        self.item_id = item_id
        self.quality = quality
        self.setObjectName("orderMaterialCard")
        root = QGridLayout(self)
        root.setContentsMargins(10, 9, 10, 9)
        title = QLabel(name or item_id)
        title.setObjectName("orderMaterialTitle")
        identifier = QLabel(f"{item_id} · Q{quality}")
        identifier.setObjectName("orderMaterialId")
        self.purchased = SafeSpinBox()
        self.purchased.setRange(0, 2_000_000_000)
        self.purchased.setValue(purchased)
        self.purchased.setSuffix(" vásárolt db")
        self.inventory = SafeSpinBox()
        self.inventory.setRange(0, 2_000_000_000)
        self.inventory.setValue(inventory)
        self.inventory.setSuffix(" inventory db")
        self.unit_price = SafeSpinBox()
        self.unit_price.setRange(0, 2_000_000_000)
        self.unit_price.setValue(unit_price)
        self.unit_price.setSuffix(" silver/db")
        self.city = SafeComboBox()
        self.city.addItems(CITIES)
        if city in CITIES:
            self.city.setCurrentText(city)
        remove = QPushButton()
        remove.setObjectName("orderMaterialRemove")
        remove.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        remove.setToolTip("Alapanyag eltávolítása")
        remove.setFixedSize(30, 30)
        remove.clicked.connect(lambda: self.remove_requested.emit(self))
        root.addWidget(title, 0, 0, 1, 4)
        root.addWidget(identifier, 1, 0, 1, 4)
        root.addWidget(self.purchased, 2, 0, 1, 2)
        root.addWidget(self.inventory, 2, 2, 1, 2)
        root.addWidget(self.unit_price, 3, 0, 1, 2)
        root.addWidget(self.city, 3, 2, 1, 2)
        root.addWidget(remove, 0, 4, 2, 1)
        for column in range(4):
            root.setColumnStretch(column, 1)

    def set_read_only(self, read_only: bool) -> None:
        for widget in (self.purchased, self.inventory, self.unit_price, self.city):
            widget.setEnabled(not read_only)
        button = self.findChild(QPushButton, "orderMaterialRemove")
        if button is not None:
            button.setVisible(not read_only)


class StatisticsCardGrid(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grid.setSpacing(9)
        self.setWidget(self.content)

    def set_rows(self, rows: list[dict[str, object]]) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for index, row in enumerate(rows):
            card = QFrame()
            card.setObjectName("statisticsOrderCard")
            layout = QVBoxLayout(card)
            title = QLabel(str(row["label"]))
            title.setObjectName("statisticsCardTitle")
            title.setWordWrap(True)
            title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            detail = QLabel(
                f"{int(row['quantity']):,} db · bruttó {int(row['gross']):,} · "
                f"anyag {int(row['purchase_cost']):,} · díjak {int(row.get('fees', 0)):,}"
            )
            detail.setWordWrap(True)
            detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            profit = QLabel(f"Nettó profit: {int(row['net_profit']):,} silver")
            profit.setObjectName("statisticsProfit")
            profit.setWordWrap(True)
            profit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout.addWidget(title)
            layout.addWidget(detail)
            layout.addWidget(profit)
            self.grid.addWidget(card, index, 0)


class CompactInventoryList(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.layout = QVBoxLayout(self.content)
        self.layout.setContentsMargins(0, 0, 4, 0)
        self.layout.setSpacing(5)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(self.content)

    def set_rows(self, rows: list[tuple]) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for item_id, name, quantity, _updated, tier, *_rest in rows:
            card = QFrame()
            card.setObjectName("statisticsInventoryCard")
            line = QHBoxLayout(card)
            line.setContentsMargins(9, 6, 9, 6)
            title = QLabel(f"T{tier} · {name or item_id}")
            title.setObjectName("statisticsInventoryTitle")
            amount = QLabel(f"{int(quantity):,} db")
            amount.setObjectName("statisticsInventoryAmount")
            line.addWidget(title, 1)
            line.addWidget(amount)
            self.layout.addWidget(card)
