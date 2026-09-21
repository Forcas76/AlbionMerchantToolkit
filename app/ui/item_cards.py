"""Responsive item-card grid with asynchronous, lazy-loaded Albion icons."""

from __future__ import annotations

import weakref
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from PyQt6.QtCore import QByteArray, QPoint, QRect, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent, QPixmap
from PyQt6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkDiskCache,
    QNetworkReply,
    QNetworkRequest,
)
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def item_icon_url(uniquename: str, quality: int = 1, size: int = 128) -> str:
    """Return a plain Albion render API URL, never Markdown-formatted text."""

    if not uniquename:
        raise ValueError("Item uniquename cannot be empty")
    if quality not in range(1, 6):
        raise ValueError("Item quality must be between 1 and 5")
    if not 1 <= size <= 217:
        raise ValueError("Item icon size must be between 1 and 217")
    item_id = quote(uniquename, safe="@_-")
    return (
        f"https://render.albiononline.com/v1/item/{item_id}.png"
        f"?quality={quality}&size={size}"
    )


class ItemIconLoader(QNetworkAccessManager):
    """Shared async loader with memory cache, disk cache and request coalescing."""

    def __init__(self, cache_directory: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        try:
            cache_directory.mkdir(parents=True, exist_ok=True)
            disk_cache = QNetworkDiskCache(self)
            disk_cache.setCacheDirectory(str(cache_directory))
            disk_cache.setMaximumCacheSize(100 * 1024 * 1024)
            self.setCache(disk_cache)
        except OSError:
            # The in-memory cache still keeps scrolling light in read-only installs.
            pass
        self._memory: dict[str, QPixmap] = {}
        self._waiting: dict[str, list[Callable[[QPixmap | None], None]]] = {}

    def request_icon(
        self,
        url: str,
        callback: Callable[[QPixmap | None], None],
    ) -> None:
        cached = self._memory.get(url)
        if cached is not None:
            QTimer.singleShot(0, lambda: callback(cached))
            return
        if url in self._waiting:
            self._waiting[url].append(callback)
            return
        self._waiting[url] = [callback]
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", b"AlbionMerchantToolkit/1.0")
        reply = self.get(request)

        def finished() -> None:
            pixmap: QPixmap | None = None
            if reply.error() == QNetworkReply.NetworkError.NoError:
                data: QByteArray = reply.readAll()
                candidate = QPixmap()
                if candidate.loadFromData(data):
                    pixmap = candidate
                    self._memory[url] = candidate
            callbacks = self._waiting.pop(url, [])
            reply.deleteLater()
            for waiting_callback in callbacks:
                waiting_callback(pixmap)

        reply.finished.connect(finished)


class ItemCard(QFrame):
    selected = pyqtSignal(object)
    favorite_requested = pyqtSignal(str)

    def __init__(
        self,
        row: tuple,
        quality: int,
        icon_loader: ItemIconLoader,
        favorite: bool = False,
        compact: bool = False,
        show_favorite: bool = True,
    ) -> None:
        super().__init__()
        self.row = row
        self.quality = quality
        self.render_quality = quality or 1
        self.icon_loader = icon_loader
        self.compact = compact
        self.icon_requested = False
        self.setObjectName("itemCard")
        self.setProperty("compact", compact)
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(190 if compact else 245, 78 if compact else 118)
        self.setMaximumHeight(86 if compact else 128)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        unique_name, name, tier, category, enchantment = row
        root = QHBoxLayout(self)
        margin = 8 if compact else 12
        root.setContentsMargins(margin, 7 if compact else 10, margin, 7 if compact else 10)
        root.setSpacing(8 if compact else 12)

        self.icon = QLabel(f"T{tier}.{enchantment}")
        self.icon.setObjectName("itemIcon")
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_size = 48 if compact else 78
        self.icon.setFixedSize(icon_size, icon_size)
        root.addWidget(self.icon)

        text = QVBoxLayout()
        text.setSpacing(4)
        title_row = QHBoxLayout()
        title = QLabel(name or unique_name)
        title.setObjectName("itemCardTitle")
        title.setWordWrap(True)
        self.favorite_button = QPushButton()
        self.favorite_button.setObjectName("favoriteButton")
        favorite_size = 24 if compact else 30
        self.favorite_button.setFixedSize(favorite_size, favorite_size)
        self.favorite_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.favorite_button.clicked.connect(
            lambda: self.favorite_requested.emit(unique_name)
        )
        self.favorite_button.setVisible(show_favorite)
        self.set_favorite(favorite)
        title_row.addWidget(title, 1)
        title_row.addWidget(self.favorite_button)
        quality_label = f"Q{quality}" if quality else "Minden quality"
        badges = QLabel(
            f"T{tier}.{enchantment}   ·   {quality_label}   ·   {category or 'egyéb'}"
        )
        badges.setObjectName("itemCardMeta")
        identifier = QLabel(unique_name)
        identifier.setObjectName("itemCardId")
        identifier.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.addLayout(title_row)
        text.addWidget(badges)
        text.addStretch()
        text.addWidget(identifier)
        root.addLayout(text, 1)

    def set_favorite(self, favorite: bool) -> None:
        self.favorite_button.setText("★" if favorite else "☆")
        self.favorite_button.setToolTip(
            "Eltávolítás a kedvencekből" if favorite else "Hozzáadás a kedvencekhez"
        )
        self.favorite_button.setProperty("favorite", favorite)
        self.favorite_button.style().unpolish(self.favorite_button)
        self.favorite_button.style().polish(self.favorite_button)

    def load_icon(self) -> None:
        if self.icon_requested:
            return
        self.icon_requested = True
        url = item_icon_url(self.row[0], self.render_quality, 128)
        card_ref = weakref.ref(self)

        def apply_icon(pixmap: QPixmap | None) -> None:
            card = card_ref()
            if card is None:
                return
            try:
                if pixmap is None:
                    card.icon.setText("Nincs kép")
                    card.icon.setProperty("failed", True)
                else:
                    card.icon.setText("")
                    card.icon.setPixmap(
                        pixmap.scaled(
                            card.icon.size(),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
            except RuntimeError:
                # The card may have been deleted while a network reply was in flight.
                return

        self.icon_loader.request_icon(url, apply_icon)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.row)
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.selected.emit(self.row)
            event.accept()
            return
        super().keyPressEvent(event)


class ItemCardGrid(QScrollArea):
    """Card result viewport that only requests icons close to the visible area."""

    item_selected = pyqtSignal(object)
    favorite_requested = pyqtSignal(str)

    def __init__(
        self,
        icon_loader: ItemIconLoader,
        compact: bool = False,
        show_favorites: bool = True,
    ) -> None:
        super().__init__()
        self.icon_loader = icon_loader
        self.compact = compact
        self.show_favorites = show_favorites
        self.setObjectName("itemCardScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumHeight(270)
        self.content = QWidget()
        self.content.setObjectName("itemCardContent")
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 2, 4, 2)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(self.content)
        self.cards: list[ItemCard] = []
        self.current_card: ItemCard | None = None
        self.verticalScrollBar().valueChanged.connect(self._schedule_lazy_load)
        self._lazy_timer = QTimer(self)
        self._lazy_timer.setSingleShot(True)
        self._lazy_timer.setInterval(40)
        self._lazy_timer.timeout.connect(self.load_visible_icons)

    def set_items(
        self,
        rows: list[tuple],
        quality: int,
        selected_id: str | None = None,
        favorite_ids: set[str] | None = None,
    ) -> None:
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        while self.grid.takeAt(0) is not None:
            pass
        self.cards = []
        self.current_card = None
        for row in rows:
            card = ItemCard(
                row,
                quality,
                self.icon_loader,
                row[0] in (favorite_ids or set()),
                self.compact,
                self.show_favorites,
            )
            card.selected.connect(lambda selected_row, source=card: self._select(source, selected_row))
            card.favorite_requested.connect(self.favorite_requested)
            self.cards.append(card)
            if row[0] == selected_id:
                self.current_card = card
                card.set_selected(True)
        self._relayout()
        self.verticalScrollBar().setValue(0)
        self._schedule_lazy_load()

    def set_favorite_state(self, item_id: str, enabled: bool) -> None:
        for card in self.cards:
            if card.row[0] == item_id:
                card.set_favorite(enabled)

    def _select(self, card: ItemCard, row: tuple) -> None:
        if self.current_card is not None and self.current_card is not card:
            self.current_card.set_selected(False)
        self.current_card = card
        card.set_selected(True)
        self.item_selected.emit(row)

    def _column_count(self) -> int:
        return max(1, self.viewport().width() // (220 if self.compact else 285))

    def _relayout(self) -> None:
        while self.grid.takeAt(0) is not None:
            pass
        columns = self._column_count()
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self.grid.setColumnStretch(column, 1)

    def _schedule_lazy_load(self) -> None:
        self._lazy_timer.start()

    def load_visible_icons(self) -> None:
        preload_area = self.viewport().rect().adjusted(0, -220, 0, 220)
        for card in self.cards:
            top_left = card.mapTo(self.viewport(), QPoint(0, 0))
            if QRect(top_left, card.size()).intersects(preload_area):
                card.load_icon()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()
        self._schedule_lazy_load()
