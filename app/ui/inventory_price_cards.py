"""Editable inventory valuation cards for market-price windows."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.domain.market import FeePolicy
from app.ui.market_cards import relative_age
from app.ui.safe_inputs import SafeSpinBox


def _silver(value: int | None) -> str:
    return f"{value:,} silver" if value is not None and value > 0 else "nincs adat"


class InventoryValuationCard(QFrame):
    """A city quote plus an editable sell-order revenue scenario."""

    def __init__(self, row: tuple, quantity: int, fees: FeePolicy) -> None:
        super().__init__()
        (
            self.city, quality, enchantment,
            sell_min, sell_min_date, sell_max, sell_max_date,
            buy_min, buy_min_date, buy_max, buy_max_date,
        ) = row
        self.quantity = quantity
        self.fees = fees
        self.instant_unit_price = int(buy_max or 0)
        self.setObjectName("inventoryValueCard")
        self.setMinimumWidth(360)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 13, 14, 13)
        root.setSpacing(9)

        header = QHBoxLayout()
        city = QLabel(f"{self.city} · Q{quality} · .{enchantment}")
        city.setObjectName("inventoryValueCity")
        header.addWidget(city, 1)
        stock = QLabel(f"{quantity:,} db")
        stock.setObjectName("inventoryValueStock")
        header.addWidget(stock)
        self.stock_label = stock
        root.addLayout(header)

        snapshot = QGridLayout()
        snapshot.setHorizontalSpacing(12)
        snapshot.addWidget(QLabel("Sell order minimum"), 0, 0)
        sell_value = QLabel(_silver(sell_min))
        sell_value.setObjectName("inventoryValueMarketPrice")
        snapshot.addWidget(sell_value, 0, 1)
        snapshot.addWidget(QLabel(relative_age(sell_min_date)), 0, 2)
        snapshot.addWidget(QLabel("Buy order maximum"), 1, 0)
        buy_value = QLabel(_silver(buy_max))
        buy_value.setObjectName("inventoryValueMarketPrice")
        snapshot.addWidget(buy_value, 1, 1)
        snapshot.addWidget(QLabel(relative_age(buy_max_date)), 1, 2)
        snapshot.setColumnStretch(0, 1)
        root.addLayout(snapshot)

        instant = QFrame()
        instant.setObjectName("inventoryValueInstant")
        instant_layout = QVBoxLayout(instant)
        instant_layout.setContentsMargins(10, 8, 10, 8)
        instant_title = QLabel("Azonnali eladás buy orderre")
        instant_title.setObjectName("inventoryValueCaption")
        self.instant_result = QLabel()
        self.instant_result.setObjectName("inventoryValueResult")
        instant_layout.addWidget(instant_title)
        instant_layout.addWidget(self.instant_result)
        root.addWidget(instant)

        scenario = QFrame()
        scenario.setObjectName("inventoryValueScenario")
        scenario_layout = QGridLayout(scenario)
        scenario_layout.setContentsMargins(10, 9, 10, 9)
        scenario_layout.addWidget(QLabel("Tervezett eladási darabár"), 0, 0)
        self.unit_price = SafeSpinBox()
        self.unit_price.setRange(0, 2_000_000_000)
        self.unit_price.setSingleStep(10)
        self.unit_price.setValue(int(sell_min or buy_max or 0))
        self.unit_price.setSuffix(" silver/db")
        scenario_layout.addWidget(self.unit_price, 0, 1)
        self.gross = QLabel()
        self.tax = QLabel()
        self.setup = QLabel()
        self.net = QLabel()
        self.net.setObjectName("inventoryValueResult")
        for row_index, (caption, target) in enumerate((
            ("Bruttó készletérték", self.gross),
            ("Tranzakciós adó", self.tax),
            ("Setup fee", self.setup),
            ("Várható nettó bevétel", self.net),
        ), start=1):
            scenario_layout.addWidget(QLabel(caption), row_index, 0)
            scenario_layout.addWidget(target, row_index, 1)
        root.addWidget(scenario)
        self.unit_price.valueChanged.connect(self.recalculate)
        self.recalculate()

    def set_quantity(self, quantity: int) -> None:
        self.quantity = quantity
        self.stock_label.setText(f"{quantity:,} db")
        self.recalculate()

    def set_fees(self, fees: FeePolicy) -> None:
        self.fees = fees
        self.recalculate()

    def recalculate(self, _value: int = 0) -> None:
        instant_gross = self.instant_unit_price * self.quantity
        instant_tax = self.fees.transaction_tax(instant_gross)
        instant_net = instant_gross - instant_tax
        self.instant_result.setText(
            f"{self.instant_unit_price:,}/db × {self.quantity:,} = "
            f"{instant_gross:,} bruttó · {instant_net:,} nettó"
            if self.instant_unit_price else "Nincs használható buy order"
        )
        gross = self.unit_price.value() * self.quantity
        tax = self.fees.transaction_tax(gross)
        setup = self.fees.setup_fee(gross)
        net = gross - tax - setup
        self.gross.setText(f"{gross:,} silver")
        self.tax.setText(f"− {tax:,} silver")
        self.setup.setText(f"− {setup:,} silver")
        self.net.setText(f"{net:,} silver")


class InventoryValuationGrid(QScrollArea):
    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("inventoryValueScroll")
        self.content = QWidget()
        self.content.setObjectName("inventoryValueContent")
        self.grid = QGridLayout(self.content)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grid.setSpacing(10)
        self.setWidget(self.content)
        self.cards: list[InventoryValuationCard] = []
        self.quantity = 0
        self.fees = FeePolicy()

    def set_quotes(self, rows: list[tuple], quantity: int, fees: FeePolicy) -> None:
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        while self.grid.takeAt(0) is not None:
            pass
        self.quantity = quantity
        self.fees = fees
        self.cards = [InventoryValuationCard(row, quantity, fees) for row in rows]
        self._relayout()

    def set_quantity(self, quantity: int) -> None:
        self.quantity = quantity
        for card in self.cards:
            card.set_quantity(quantity)

    def set_fees(self, fees: FeePolicy) -> None:
        self.fees = fees
        for card in self.cards:
            card.set_fees(fees)

    def _relayout(self) -> None:
        while self.grid.takeAt(0) is not None:
            pass
        columns = 2 if self.viewport().width() >= 820 else 1
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self.grid.setColumnStretch(column, 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()
