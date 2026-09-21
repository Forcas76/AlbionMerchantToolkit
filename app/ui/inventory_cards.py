"""Card-based inventory editor and overlay item picker."""

from __future__ import annotations

import weakref
from collections.abc import Callable

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from app.services.inventory import STACK_SIZE, quantity_from_stacks, split_stacks
from app.ui.category_picker import CategoryPopupButton
from app.ui.item_cards import ItemCardGrid, ItemIconLoader, item_icon_url
from app.ui.safe_inputs import SafeComboBox as QComboBox, SafeSpinBox as QSpinBox


class InventoryCard(QFrame):
    save_requested = pyqtSignal(str, int, int)
    remove_requested = pyqtSignal(str, int)
    price_requested = pyqtSignal(str, int)

    def __init__(self, row: tuple, loader: ItemIconLoader) -> None:
        super().__init__()
        (
            item_id, name, quantity, updated_at, tier,
            category, subcategory, subcategory2, subcategory3,
            enchantment, quality,
        ) = row
        self.item_id = str(item_id)
        self.quality = int(quality)
        self.loader = loader
        self.icon_requested = False
        self.setObjectName("inventoryCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumSize(310, 184)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 11, 12, 11)
        root.setSpacing(8)
        header = QHBoxLayout()
        self.icon = QLabel(f"T{tier}.{enchantment}")
        self.icon.setObjectName("inventoryIcon")
        self.icon.setFixedSize(68, 68)
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.icon)
        identity = QVBoxLayout()
        title = QLabel(str(name or item_id))
        title.setObjectName("inventoryTitle")
        title.setWordWrap(True)
        category_path = tuple(
            value for value in (category, subcategory, subcategory2, subcategory3) if value
        )
        category_label = " › ".join(_category_label(value) for value in category_path)
        meta = QLabel(f"T{tier}.{enchantment} · Q{quality} · {category_label or 'Egyéb'}")
        meta.setObjectName("inventoryMeta")
        identifier = QLabel(self.item_id)
        identifier.setObjectName("inventoryId")
        identifier.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        identity.addWidget(title)
        identity.addWidget(meta)
        identity.addWidget(identifier)
        header.addLayout(identity, 1)
        root.addLayout(header)

        stacks, loose = split_stacks(int(quantity))
        amount_row = QHBoxLayout()
        self.stacks = QSpinBox()
        self.stacks.setRange(0, 2_000_000)
        self.stacks.setValue(stacks)
        self.stacks.setSuffix(" stack")
        self.loose = QSpinBox()
        self.loose.setRange(0, STACK_SIZE - 1)
        self.loose.setValue(loose)
        self.loose.setSuffix(" db")
        amount_row.addWidget(self.stacks, 1)
        amount_row.addWidget(self.loose, 1)
        root.addLayout(amount_row)
        self.total = QLabel()
        self.total.setObjectName("inventoryTotal")
        self.updated = QLabel(f"Utolsó mentés: {updated_at}")
        self.updated.setObjectName("inventoryUpdated")
        root.addWidget(self.total)
        root.addWidget(self.updated)

        actions = QHBoxLayout()
        save = QPushButton("Mentés")
        save.setObjectName("inventorySave")
        prices = QPushButton("Árak")
        prices.setObjectName("inventoryPrices")
        self.price_button = prices
        remove = QPushButton()
        remove.setObjectName("inventoryRemove")
        save.clicked.connect(self._save)
        prices.clicked.connect(
            lambda: self.price_requested.emit(self.item_id, self.quality)
        )
        remove.clicked.connect(
            lambda: self.remove_requested.emit(self.item_id, self.quality)
        )
        remove.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        remove.setIconSize(QSize(14, 14))
        remove.setToolTip("Item törlése az inventoryból")
        remove.setFixedSize(28, 28)
        actions.addWidget(save)
        actions.addWidget(prices)
        actions.addStretch()
        actions.addWidget(remove, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        root.addLayout(actions)
        self.stacks.valueChanged.connect(self._update_total)
        self.loose.valueChanged.connect(self._update_total)
        self._update_total()

    def quantity(self) -> int:
        return quantity_from_stacks(self.stacks.value(), self.loose.value())

    def _save(self) -> None:
        self.save_requested.emit(self.item_id, self.quality, self.quantity())

    def _update_total(self, _value: int = 0) -> None:
        self.total.setText(f"Összesen: {self.quantity():,} db")

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

        self.loader.request_icon(item_icon_url(self.item_id, self.quality, 96), apply)


def _category_label(category_id: str) -> str:
    return category_id.replace("_", " ").strip().title() or "Egyéb"


class InventoryCategorySection(QFrame):
    """One flat, expandable leaf category with a breadcrumb header."""

    toggled = pyqtSignal(object, bool)

    def __init__(self, path: tuple[str, ...], count: int, expanded: bool) -> None:
        super().__init__()
        self.path = path
        self.label = "  ›  ".join(_category_label(part) for part in path)
        self.setObjectName("inventoryCategorySection")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 4)
        root.setSpacing(6)
        self.toggle = QPushButton()
        self.toggle.setObjectName("inventoryCategoryToggle")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggle.clicked.connect(self._toggle)
        root.addWidget(self.toggle)
        self.body = QWidget()
        self.body.setObjectName("inventoryCategoryBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(7)
        root.addWidget(self.body)
        self.count = count
        self._apply_state(expanded)

    def _toggle(self, expanded: bool) -> None:
        self._apply_state(expanded)
        self.toggled.emit(self.path, expanded)

    def _apply_state(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        arrow = "▾" if expanded else "▸"
        self.toggle.setText(f"{arrow}  {self.label}  ({self.count})")


class InventoryCardGrid(QScrollArea):
    save_requested = pyqtSignal(str, int, int)
    remove_requested = pyqtSignal(str, int)
    price_requested = pyqtSignal(str, int)

    def __init__(self, loader: ItemIconLoader) -> None:
        super().__init__()
        self.loader = loader
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("inventoryScroll")
        self.content = QWidget()
        self.content.setObjectName("inventoryContent")
        self.sections_layout = QVBoxLayout(self.content)
        self.sections_layout.setContentsMargins(0, 2, 4, 2)
        self.sections_layout.setSpacing(8)
        self.sections_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.cards: list[InventoryCard] = []
        self.sections: list[InventoryCategorySection] = []
        self.card_grids: list[QGridLayout] = []
        self._collapsed_paths: set[tuple[str, ...]] = set()
        self.setWidget(self.content)
        self.verticalScrollBar().valueChanged.connect(self._schedule_lazy_load)
        self._lazy_timer = QTimer(self)
        self._lazy_timer.setSingleShot(True)
        self._lazy_timer.setInterval(40)
        self._lazy_timer.timeout.connect(self.load_visible_icons)

    def set_inventory(self, rows: list[tuple]) -> None:
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        while self.sections_layout.count():
            item = self.sections_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.cards = []
        self.sections = []
        self.card_grids = []
        groups: dict[tuple[str, ...], list[tuple]] = {}
        for row in rows:
            path = tuple(str(value) for value in row[5:9] if value) or ("other",)
            groups.setdefault(path, []).append(row)
        for path, grouped_rows in sorted(
            groups.items(),
            key=lambda entry: tuple(_category_label(part) for part in entry[0]),
        ):
            self.sections_layout.addWidget(self._build_section(path, grouped_rows))
        if not rows:
            empty = QLabel("Az inventory még üres. Az „Item hozzáadása” gombbal vehetsz fel készletet.")
            empty.setObjectName("inventoryEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.sections_layout.addWidget(empty)
        self._relayout()
        self.verticalScrollBar().setValue(0)
        self._schedule_lazy_load()

    def _relayout(self) -> None:
        columns = max(1, self.viewport().width() // 340)
        for grid in self.card_grids:
            widgets = []
            while grid.count():
                item = grid.takeAt(0)
                if item.widget() is not None:
                    widgets.append(item.widget())
            for index, card in enumerate(widgets):
                grid.addWidget(card, index // columns, index % columns)
            for column in range(columns):
                grid.setColumnStretch(column, 1)

    def _build_section(
        self, path: tuple[str, ...], rows: list[tuple]
    ) -> InventoryCategorySection:
        expanded = path not in self._collapsed_paths
        section = InventoryCategorySection(path, len(rows), expanded)
        section.toggled.connect(self._remember_section_state)
        self.sections.append(section)
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        self.card_grids.append(grid)
        for index, row in enumerate(rows):
            card = InventoryCard(row, self.loader)
            card.save_requested.connect(self.save_requested)
            card.remove_requested.connect(self.remove_requested)
            card.price_requested.connect(self.price_requested)
            self.cards.append(card)
            grid.addWidget(card, index, 0)
        section.body_layout.addWidget(holder)
        return section

    def _remember_section_state(self, path: tuple[str, ...], expanded: bool) -> None:
        if expanded:
            self._collapsed_paths.discard(path)
        else:
            self._collapsed_paths.add(path)
        QTimer.singleShot(0, self.load_visible_icons)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()
        self._schedule_lazy_load()

    def _schedule_lazy_load(self) -> None:
        self._lazy_timer.start()

    def load_visible_icons(self) -> None:
        preload_area = self.viewport().rect().adjusted(0, -240, 0, 240)
        for card in self.cards:
            top_left = card.mapTo(self.viewport(), QPoint(0, 0))
            if QRect(top_left, card.size()).intersects(preload_area):
                card.load_icon()


class InventoryPickerDialog(QDialog):
    """Modal overlay using the common item-card browsing pattern."""

    def __init__(
        self,
        loader: ItemIconLoader,
        search_items: Callable[[str, int, int, tuple[str, ...]], list[tuple]],
        category_rows: list[tuple],
        category_counts: dict[tuple[str, ...], int],
        parent=None,
        *,
        show_quantity: bool = True,
        action_label: str = "Hozzáadás",
        window_title: str = "Item hozzáadása az inventoryhoz",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(window_title)
        self.setModal(True)
        self.resize(1080, 720)
        self.setMinimumSize(820, 560)
        self.search_items = search_items
        self.selected_row: tuple | None = None

        root = QVBoxLayout(self)
        heading = QLabel("Item kiválasztása")
        heading.setObjectName("pageTitle")
        root.addWidget(heading)
        filters = QGridLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Item neve vagy azonosítója")
        self.category = CategoryPopupButton(self)
        self.category.set_categories(category_rows, category_counts)
        self.tier = QComboBox()
        self.tier.addItem("Mind", 0)
        for value in range(1, 9):
            self.tier.addItem(f"Tier {value}", value)
        self.enchantment = QComboBox()
        self.enchantment.addItem("Mind", -1)
        for value in range(5):
            self.enchantment.addItem(f".{value}", value)
        self.quality = QComboBox()
        for value, label in (
            (1, "Normal"), (2, "Good"), (3, "Outstanding"),
            (4, "Excellent"), (5, "Masterpiece"),
        ):
            self.quality.addItem(f"{value} · {label}", value)
        search_button = QPushButton("Keresés")
        search_button.clicked.connect(self.refresh_results)
        for column, label in enumerate(("Keresés", "Kategória", "Tier", "Enchant", "Quality")):
            filters.addWidget(QLabel(label), 0, column)
        for column, widget in enumerate((self.search, self.category, self.tier, self.enchantment, self.quality)):
            filters.addWidget(widget, 1, column)
        filters.addWidget(search_button, 1, 5)
        filters.setColumnStretch(0, 2)
        root.addLayout(filters)

        self.results = ItemCardGrid(loader, show_favorites=False)
        self.results.item_selected.connect(self._select)
        root.addWidget(self.results, 1)

        footer = QHBoxLayout()
        self.selection = QLabel("Nincs kiválasztott item")
        self.selection.setObjectName("inventorySelection")
        self.stacks = QSpinBox()
        self.stacks.setRange(0, 2_000_000)
        self.stacks.setSuffix(" stack")
        self.loose = QSpinBox()
        self.loose.setRange(0, STACK_SIZE - 1)
        self.loose.setValue(1)
        self.loose.setSuffix(" db")
        self.stacks.setVisible(show_quantity)
        self.loose.setVisible(show_quantity)
        cancel = QPushButton("Mégse")
        cancel.clicked.connect(self.reject)
        self.add_button = QPushButton(action_label)
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self.accept)
        footer.addWidget(self.selection, 1)
        footer.addWidget(self.stacks)
        footer.addWidget(self.loose)
        footer.addWidget(cancel)
        footer.addWidget(self.add_button)
        root.addLayout(footer)

        self.search.returnPressed.connect(self.refresh_results)
        self.category.selection_changed.connect(self.refresh_results)
        self.tier.currentIndexChanged.connect(self.refresh_results)
        self.enchantment.currentIndexChanged.connect(self.refresh_results)
        self.quality.currentIndexChanged.connect(self.refresh_results)
        self.refresh_results()

    def refresh_results(self, _value=0) -> None:
        rows = self.search_items(
            self.search.text().strip(),
            int(self.tier.currentData()),
            int(self.enchantment.currentData()),
            self.category.selected_path,
        )
        self.selected_row = None
        self.add_button.setEnabled(False)
        self.selection.setText(f"{len(rows)} találat · válassz egy kártyát")
        self.results.set_items(rows, quality=int(self.quality.currentData()))

    def _select(self, row: tuple) -> None:
        self.selected_row = row
        self.selection.setText(f"Kiválasztva: {row[1] or row[0]}")
        self.add_button.setEnabled(True)

    def quantity(self) -> int:
        return quantity_from_stacks(self.stacks.value(), self.loose.value())

    def selected_quality(self) -> int:
        return int(self.quality.currentData())
