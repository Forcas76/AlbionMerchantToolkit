"""City-based market cards for a selected Albion item."""

from __future__ import annotations

from datetime import datetime, timezone

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from app.domain.market import age_minutes, parse_aodp_timestamp
from app.ui.item_cards import ItemIconLoader, item_icon_url


def relative_age(value: str | None, now: datetime | None = None) -> str:
    minutes = age_minutes(parse_aodp_timestamp(value), now)
    if minutes is None:
        return "nincs adat"
    rounded = int(minutes)
    if rounded < 1:
        return "most"
    if rounded < 60:
        return f"{rounded} perce"
    hours = rounded // 60
    if hours < 24:
        remainder = rounded % 60
        return f"{hours} óra {remainder} perce" if remainder else f"{hours} órája"
    days = hours // 24
    return f"{days} napja"


def _silver(value: int | None) -> str:
    return f"{value:,} silver" if value and value > 0 else "nincs adat"


class MarketCityCard(QFrame):
    def __init__(self, item_id: str, quality: int, row: tuple, loader: ItemIconLoader) -> None:
        super().__init__()
        self.setObjectName("marketCityCard")
        self.setMinimumWidth(300)
        city, row_quality, enchantment, sell_min, sell_min_date, sell_max, sell_max_date, buy_min, buy_min_date, buy_max, buy_max_date = row
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        icon = QLabel(f".{enchantment}")
        icon.setObjectName("marketCardIcon")
        icon.setFixedSize(72, 72)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        body = QVBoxLayout()
        title = QLabel(f"{city}  ·  Q{row_quality}  ·  .{enchantment}")
        title.setObjectName("marketCardTitle")
        title_row = QHBoxLayout()
        title_row.addWidget(title, 1)
        observed_ages = [
            age_minutes(parse_aodp_timestamp(value))
            for value in (sell_min_date, sell_max_date, buy_min_date, buy_max_date)
        ]
        known_ages = [age for age in observed_ages if age is not None]
        worst_age = max(known_ages) if known_ages else None
        if worst_age is None:
            freshness, caption = "missing", "Nincs adat"
        elif worst_age <= 30:
            freshness, caption = "fresh", "Friss"
        elif worst_age <= 120:
            freshness, caption = "aging", "Öregedő"
        else:
            freshness, caption = "stale", "Régi"
        badge = QLabel(caption)
        badge.setObjectName("freshnessBadge")
        badge.setProperty("freshness", freshness)
        title_row.addWidget(badge)
        body.addLayout(title_row)
        quotes = (
            ("Sell order minimum", sell_min, sell_min_date),
            ("Sell order maximum", sell_max, sell_max_date),
            ("Buy order minimum", buy_min, buy_min_date),
            ("Buy order maximum", buy_max, buy_max_date),
        )
        for label, price, observed in quotes:
            line = QHBoxLayout()
            caption = QLabel(label)
            caption.setObjectName("marketCardLabel")
            amount = QLabel(_silver(price))
            amount.setObjectName("marketCardPrice")
            age = QLabel(relative_age(observed))
            age.setObjectName("marketCardAge")
            line.addWidget(caption, 1)
            line.addWidget(amount)
            line.addWidget(age)
            body.addLayout(line)
        root.addLayout(body, 1)

        def apply_icon(pixmap: QPixmap | None) -> None:
            if pixmap is not None:
                icon.setText("")
                icon.setPixmap(pixmap.scaled(icon.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        loader.request_icon(item_icon_url(item_id, quality or 1, 128), apply_icon)


class MarketCityCardGrid(QScrollArea):
    def __init__(self, loader: ItemIconLoader) -> None:
        super().__init__()
        self.loader = loader
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grid.setSpacing(10)
        self.setWidget(self.content)
        self.cards: list[MarketCityCard] = []

    def set_quotes(self, item_id: str, quality: int, rows: list[tuple]) -> None:
        for card in self.cards:
            card.deleteLater()
        while self.grid.takeAt(0) is not None:
            pass
        self.cards = [MarketCityCard(item_id, quality, row, self.loader) for row in rows]
        columns = 2 if self.viewport().width() >= 700 else 1
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.cards:
            rows = list(self.cards)
            while self.grid.takeAt(0) is not None:
                pass
            columns = 2 if self.viewport().width() >= 700 else 1
            for index, card in enumerate(rows):
                self.grid.addWidget(card, index // columns, index % columns)
