"""Modern PyQt6 interface for Albion Prize Shower."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QEasingCurve, QObject, QStandardPaths, QThread, QTimer, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QAction, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHeaderView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.services.market_api import (
    CITIES,
    DB_FILE,
    do_fetch,
    fetch_prices_for_chunk,
    save_prices,
)
from app.services.history_api import fetch_history, save_history
from app.core.database import connect_database
from app.domain.game_rules import market_fee_policy
from app.domain.market import MarketStrategy, OpportunityRules
from app.services.opportunity_scanner import scan_opportunities
from app.services.item_catalog import import_item_categories as sync_item_category_data
from app.services.favorites import favorite_ids, list_favorites, toggle_favorite
from app.services.user_settings import get_int as get_user_int, set_int as set_user_int
from app.services.refining import (
    RefiningCityConfig,
    calculate_refining as calculate_refining_result,
    import_refining_recipes,
    list_refining_recipes,
    load_refining_cities,
    market_prices_for_route,
    save_refining_city,
)
from app.ui.rich_menu import CraftSettings, load_craft_settings, save_craft_settings
from app.ui.item_cards import ItemCardGrid, ItemIconLoader
from app.ui.market_cards import MarketCityCardGrid
from app.ui.category_picker import CategoryPopupButton

PROJECT_ROOT = Path(__file__).resolve().parents[2]
QUALITY_LABELS = {
    1: "Normal",
    2: "Good",
    3: "Outstanding",
    4: "Excellent",
    5: "Masterpiece",
}


def open_db() -> sqlite3.Connection:
    return connect_database(DB_FILE)


class Worker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)

    def __init__(self, operation: Callable, accepts_progress: bool = False) -> None:
        super().__init__()
        self.operation = operation
        self.accepts_progress = accepts_progress

    def run(self) -> None:
        try:
            result = (
                self.operation(self.progress.emit)
                if self.accepts_progress
                else self.operation()
            )
            self.finished.emit(result)
        except Exception as error:
            self.failed.emit(str(error))


class SidebarButton(QPushButton):
    def __init__(self, text: str, icon: str) -> None:
        super().__init__(f"  {icon}   {text}")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class CitySelector(QFrame):
    """Compact reusable location filter used by every market-aware page."""

    def __init__(
        self,
        title: str,
        cities: tuple[str, ...] = CITIES,
        checked: bool = True,
    ) -> None:
        super().__init__()
        self.setObjectName("filterPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        header = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("filterTitle")
        all_button = QPushButton("Mind")
        all_button.setObjectName("miniButton")
        all_button.clicked.connect(lambda: self.set_all(True))
        none_button = QPushButton("Egyik sem")
        none_button.setObjectName("miniButton")
        none_button.clicked.connect(lambda: self.set_all(False))
        header.addWidget(label)
        header.addStretch()
        header.addWidget(all_button)
        header.addWidget(none_button)
        layout.addLayout(header)
        chips = QGridLayout()
        chips.setSpacing(6)
        self.checkboxes: list[QCheckBox] = []
        for city in cities:
            checkbox = QCheckBox(city)
            checkbox.setObjectName("cityChip")
            checkbox.setChecked(checked)
            chips.addWidget(checkbox, len(self.checkboxes) // 3, len(self.checkboxes) % 3)
            self.checkboxes.append(checkbox)
        layout.addLayout(chips)

    def set_all(self, checked: bool) -> None:
        for checkbox in self.checkboxes:
            checkbox.setChecked(checked)

    def selected_cities(self) -> list[str]:
        return [checkbox.text() for checkbox in self.checkboxes if checkbox.isChecked()]


class AlbionWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Albion Prize Shower")
        self.resize(1440, 900)
        self.setMinimumSize(1050, 700)
        self.selected_ids: list[str] = []
        self.selected_name = ""
        self.item_rows: list[tuple] = []
        self.thread: QThread | None = None
        self.worker: Worker | None = None
        self.nav_buttons: list[SidebarButton] = []
        self._splitter_save_timers: dict[str, QTimer] = {}
        cache_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        )
        cache_root = Path(cache_location) if cache_location else PROJECT_ROOT / ".cache"
        self.item_icon_loader = ItemIconLoader(cache_root / "item-icons", self)

        self._setup_actions()
        self._build_shell()
        self._apply_theme()
        self.refresh_dashboard()

    def _setup_actions(self) -> None:
        self.exit_action = QAction("Kilépés", self)
        self.exit_action.triggered.connect(self.close)
        self.menuBar().addMenu("Albion Prize Shower").addAction(self.exit_action)

    def _build_shell(self) -> None:
        root = QWidget()
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(248)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 24, 18, 18)
        sidebar_layout.setSpacing(8)

        brand = QLabel("ALBION\nPRIZE SHOWER")
        brand.setObjectName("brand")
        sidebar_layout.addWidget(brand)
        subtitle = QLabel("Market intelligence")
        subtitle.setObjectName("sidebarSubtitle")
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addSpacing(24)

        navigation = (
            ("Áttekintés", "⌂", self.show_dashboard),
            ("Item keresés", "⌕", self.show_items),
            ("Market Scanner", "↗", self.show_flips),
            ("Crafting rendszer", "⚒", self.show_craft),
            ("Refining Calculator", "◈", self.show_refining),
            ("Kedvencek", "★", self.show_favorites),
            ("Adatközpont", "⛁", self.show_database),
        )
        for label, icon, callback in navigation:
            button = SidebarButton(label, icon)
            button.clicked.connect(callback)
            sidebar_layout.addWidget(button)
            self.nav_buttons.append(button)
        sidebar_layout.addStretch()
        hint = QLabel("Piaci döntésekhez.\nAdatokból, nem érzésből.")
        hint.setObjectName("sidebarHint")
        sidebar_layout.addWidget(hint)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._dashboard_page())
        self.pages.addWidget(self._items_page())
        self.pages.addWidget(self._flips_page())
        self.pages.addWidget(self._craft_page())
        self.pages.addWidget(self._refining_page())
        self.pages.addWidget(self._favorites_page())
        self.pages.addWidget(self._database_page())
        shell.addWidget(sidebar)
        shell.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Készen áll")
        self.show_dashboard()

    def _page(self, title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.setSpacing(18)
        scroll.setWidget(content)
        page_layout.addWidget(scroll)

        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        subheading = QLabel(description)
        subheading.setObjectName("pageDescription")
        subheading.setWordWrap(True)
        layout.addWidget(subheading)
        return page, layout

    def _dashboard_page(self) -> QWidget:
        page, layout = self._page(
            "Áttekintés",
            "A három munkafolyamat kézzel összeállított kedvencei egy helyen.",
        )
        summary = QHBoxLayout()
        summary.setSpacing(14)
        self.dashboard_favorite_tables: dict[str, QTableWidget] = {}
        for context, title in (
            ("price", "Item-ár kedvencek"),
            ("crafting", "Crafting kedvencek"),
            ("flip", "Market flip kedvencek"),
        ):
            group = QGroupBox(title)
            group_layout = QVBoxLayout(group)
            table = self._table()
            self.dashboard_favorite_tables[context] = table
            group_layout.addWidget(table)
            summary.addWidget(group, 1)
        layout.addLayout(summary, 1)
        layout.addStretch()
        return page

    def _items_page(self) -> QWidget:
        page, layout = self._page(
            "Item keresés",
            "Keress név vagy ID alapján, vagy szűkíts a lenyíló kategóriafával, tierrel, enchanttal és qualityvel.",
        )
        self.item_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.item_splitter.setObjectName("masterDetailSplitter")
        self.item_splitter.setChildrenCollapsible(False)
        browser = QWidget()
        browser_layout = QVBoxLayout(browser)
        browser_layout.setContentsMargins(0, 0, 10, 0)
        browser_layout.setSpacing(12)
        self.item_detail = QFrame()
        self.item_detail.setObjectName("detailPanel")
        detail_layout = QVBoxLayout(self.item_detail)
        detail_layout.setContentsMargins(18, 18, 18, 18)
        detail_layout.setSpacing(12)

        controls = QGridLayout()
        controls.setHorizontalSpacing(8)
        controls.setVerticalSpacing(4)
        self.item_search = QLineEdit()
        self.item_search.setPlaceholderText("Például: Leather, Sword, T4_BAG")
        self.item_search.setMinimumWidth(120)
        self.item_search.returnPressed.connect(self.search_items)
        self.item_category = CategoryPopupButton()
        self.item_tier = QSpinBox()
        self.item_tier.setRange(1, 8)
        self.item_tier.setValue(4)
        self.item_tier.setFixedWidth(64)
        self.item_enchantment = QComboBox()
        self.item_enchantment.addItem("Mind", -1)
        for enchantment in range(5):
            self.item_enchantment.addItem(f".{enchantment}", enchantment)
        self.item_enchantment.setFixedWidth(80)
        self.item_quality = QComboBox()
        self.item_quality.addItem("Mind", 0)
        for quality, label in QUALITY_LABELS.items():
            self.item_quality.addItem(f"{quality} · {label}", quality)
        self.item_quality.setFixedWidth(125)
        search = QPushButton("Keresés")
        search.setFixedWidth(85)
        search.clicked.connect(self.search_items)
        for column, label in enumerate(
            ("Keresés", "Kategória", "Tier", "Enchant", "Quality", "")
        ):
            controls.addWidget(QLabel(label), 0, column)
        controls.addWidget(self.item_search, 1, 0)
        controls.addWidget(self.item_category, 1, 1)
        controls.addWidget(self.item_tier, 1, 2)
        controls.addWidget(self.item_enchantment, 1, 3)
        controls.addWidget(self.item_quality, 1, 4)
        controls.addWidget(search, 1, 5)
        controls.setColumnStretch(0, 2)
        controls.setColumnStretch(1, 1)
        browser_layout.addLayout(controls)
        self.item_cities = CitySelector("Megjelenített városok")
        browser_layout.addWidget(self.item_cities)
        self.item_flip_excluded = CitySelector(
            "Flipből kizárt városok (nem kötelező)",
            checked=False,
        )
        browser_layout.addWidget(self.item_flip_excluded)
        results_title = QLabel("Találatok")
        results_title.setObjectName("sectionTitle")
        browser_layout.addWidget(results_title)
        self.item_cards = ItemCardGrid(self.item_icon_loader)
        self.item_cards.item_selected.connect(self.select_item_card)
        self.item_cards.favorite_requested.connect(self.toggle_price_favorite)
        self.item_quality.currentIndexChanged.connect(self.refresh_item_card_quality)
        browser_layout.addWidget(self.item_cards, 1)
        self.load_categories()
        self.item_category.selection_changed.connect(self.search_items)
        actions = QHBoxLayout()
        prices = QPushButton("Piaci árak")
        prices.setFixedWidth(120)
        prices.clicked.connect(self.show_prices)
        flips = QPushButton("Item flipjei")
        flips.setFixedWidth(120)
        flips.clicked.connect(self.show_item_flips)
        history = QPushButton("14 nap history frissítése")
        history.setFixedWidth(210)
        history.clicked.connect(self.refresh_selected_history)
        actions.addWidget(prices)
        actions.addWidget(flips)
        actions.addWidget(history)
        actions.addStretch()
        detail_layout.addLayout(actions)
        prices_title = QLabel("Piaci adatok")
        prices_title.setObjectName("sectionTitle")
        detail_layout.addWidget(prices_title)
        self.item_detail_results = QStackedWidget()
        self.price_cards = MarketCityCardGrid(self.item_icon_loader)
        self.item_table = self._table()
        self.item_detail_results.addWidget(self.price_cards)
        self.item_detail_results.addWidget(self.item_table)
        detail_layout.addWidget(self.item_detail_results, 1)
        self.item_splitter.addWidget(browser)
        self.item_splitter.addWidget(self.item_detail)
        self.item_splitter.setStretchFactor(0, 1)
        self.item_splitter.setStretchFactor(1, 2)
        self.item_splitter.splitterMoved.connect(
            lambda *_: self._schedule_splitter_save("item", self.item_splitter)
        )
        self.item_detail.hide()
        layout.addWidget(self.item_splitter, 1)
        return page

    def _flips_page(self) -> QWidget:
        page, layout = self._page(
            "Flip lehetőségek",
            "Nettó market opportunityk fix játékadóval, kor- és ROI-szűréssel.",
        )
        self.selected_flip_ids: list[str] = []
        self.flip_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.flip_splitter.setObjectName("masterDetailSplitter")
        self.flip_splitter.setChildrenCollapsible(False)
        browser = QWidget()
        browser_layout = QVBoxLayout(browser)
        browser_layout.setContentsMargins(0, 0, 10, 0)
        browser_layout.setSpacing(12)
        self.flip_detail = QFrame()
        self.flip_detail.setObjectName("detailPanel")
        detail_layout = QVBoxLayout(self.flip_detail)
        detail_layout.setContentsMargins(18, 18, 18, 18)
        detail_layout.setSpacing(12)

        search_filters = QGridLayout()
        self.flip_search = QLineEdit()
        self.flip_search.setPlaceholderText("Item név vagy unique name")
        self.flip_search.returnPressed.connect(self.search_flip_items)
        self.flip_category = CategoryPopupButton()
        self.flip_tier = QSpinBox()
        self.flip_tier.setRange(1, 8)
        self.flip_tier.setValue(4)
        self.flip_enchantment = QComboBox()
        self.flip_enchantment.addItem("Mind", -1)
        for enchantment in range(5):
            self.flip_enchantment.addItem(f".{enchantment}", enchantment)
        self.flip_quality = QComboBox()
        self.flip_quality.addItem("Mind", 0)
        for quality, label in QUALITY_LABELS.items():
            self.flip_quality.addItem(f"{quality} · {label}", quality)
        find_item = QPushButton("Keresés")
        find_item.clicked.connect(self.search_flip_items)
        scan_everything = QPushButton("Összes item vizsgálata")
        scan_everything.clicked.connect(self.scan_all_flips)
        for column, label in enumerate(("Keresés", "Kategória", "Tier", "Enchant", "Quality")):
            search_filters.addWidget(QLabel(label), 0, column)
        for column, widget in enumerate((self.flip_search, self.flip_category, self.flip_tier, self.flip_enchantment, self.flip_quality)):
            search_filters.addWidget(widget, 1, column)
        browser_layout.addLayout(search_filters)
        browser_actions = QHBoxLayout()
        browser_actions.addWidget(find_item)
        browser_actions.addWidget(scan_everything)
        browser_actions.addStretch()
        browser_layout.addLayout(browser_actions)
        self.flip_cards = ItemCardGrid(self.item_icon_loader)
        self.flip_cards.item_selected.connect(self.select_flip_card)
        self.flip_cards.favorite_requested.connect(self.toggle_flip_favorite)
        browser_layout.addWidget(self.flip_cards, 1)

        filters = QHBoxLayout()
        self.flip_strategy = QComboBox()
        self.flip_strategy.addItem("Azonnali városközi eladás", MarketStrategy.INSTANT)
        self.flip_strategy.addItem("Városközi újralistázás", MarketStrategy.RELIST)
        self.flip_strategy.addItem("Helyi order spread", MarketStrategy.LOCAL_SPREAD)
        self.flip_min_profit = QSpinBox()
        self.flip_min_profit.setRange(0, 2_000_000_000)
        self.flip_min_profit.setValue(1_000)
        self.flip_min_profit.setSingleStep(1_000)
        self.flip_max_age = QSpinBox()
        self.flip_max_age.setRange(1, 10_080)
        self.flip_max_age.setValue(360)
        self.flip_max_age.setSuffix(" perc")
        self.flip_limit = QSpinBox()
        self.flip_limit.setRange(5, 500)
        self.flip_limit.setValue(50)
        filters.addWidget(QLabel("Stratégia"))
        filters.addWidget(self.flip_strategy)
        filters.addWidget(QLabel("Min. nettó profit"))
        filters.addWidget(self.flip_min_profit)
        filters.addWidget(QLabel("Max. adatkor"))
        filters.addWidget(self.flip_max_age)
        filters.addWidget(QLabel("Találatok"))
        filters.addWidget(self.flip_limit)
        filters.addStretch()
        detail_layout.addLayout(filters)
        self.flip_cities = CitySelector("A scannerben részt vevő városok")
        detail_layout.addWidget(self.flip_cities)
        self.flip_fee_info = QLabel()
        self.flip_fee_info.setObjectName("notice")
        self.flip_fee_info.setWordWrap(True)
        detail_layout.addWidget(self.flip_fee_info)
        load = QPushButton("Lehetőségek újraszámítása")
        load.clicked.connect(self.load_global_flips)
        all_items = QPushButton("Keresés az összes item között")
        all_items.clicked.connect(self.scan_all_flips)
        scan_actions = QHBoxLayout()
        scan_actions.addWidget(load)
        scan_actions.addWidget(all_items)
        scan_actions.addStretch()
        detail_layout.addLayout(scan_actions)
        self.global_table = self._table()
        detail_layout.addWidget(self.global_table, 1)
        self.flip_splitter.addWidget(browser)
        self.flip_splitter.addWidget(self.flip_detail)
        self.flip_splitter.setStretchFactor(0, 1)
        self.flip_splitter.setStretchFactor(1, 2)
        self.flip_splitter.splitterMoved.connect(
            lambda *_: self._schedule_splitter_save("flip", self.flip_splitter)
        )
        self.flip_detail.hide()
        layout.addWidget(self.flip_splitter, 1)
        self.load_categories()
        self.flip_category.selection_changed.connect(self.search_flip_items)
        return page

    def _craft_page(self) -> QWidget:
        page, layout = self._page(
            "Crafting rendszer",
            "Válassz receptet, ellenőrizd a szükséges alapanyagokat, majd számold ki a nettó eredményt a kijelölt városokból.",
        )
        self.craft_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.craft_splitter.setObjectName("masterDetailSplitter")
        self.craft_splitter.setChildrenCollapsible(False)
        self.craft_detail = QFrame()
        self.craft_detail.setObjectName("detailPanel")
        craft_detail_layout = QVBoxLayout(self.craft_detail)
        craft_detail_layout.setContentsMargins(18, 18, 18, 18)
        craft_detail_layout.setSpacing(12)
        setup_group = QGroupBox("1 · Recept és crafting profil")
        setup_layout = QVBoxLayout(setup_group)
        form = QFormLayout()
        self.craft_search = QLineEdit()
        self.craft_search.returnPressed.connect(self.search_recipes)
        self.craft_category = CategoryPopupButton()
        self.craft_search.setPlaceholderText("Például: Leather")
        self.craft_tier = QSpinBox()
        self.craft_tier.setRange(1, 8)
        self.craft_tier.setValue(4)
        self.craft_enchantment = QComboBox()
        self.craft_enchantment.addItem("Mind", -1)
        for enchantment in range(5):
            self.craft_enchantment.addItem(f".{enchantment}", enchantment)
        self.craft_quality = QComboBox()
        self.craft_quality.addItem("Mind", 0)
        for quality, label in QUALITY_LABELS.items():
            self.craft_quality.addItem(f"{quality} · {label}", quality)
        self.craft_source = QComboBox()
        self.craft_recipe_rows: list[tuple] = []
        self.selected_craft_row = -1
        self.craft_source.addItem("Alapanyag vásárlása", "buy")
        self.craft_source.addItem("Saját farmolás", "farm")
        settings = load_craft_settings()
        self.craft_source.setCurrentIndex(1 if settings.material_source == "farm" else 0)
        self.premium = QCheckBox("Prémium")
        self.premium.setChecked(settings.premium)
        self.crafting_price = QSpinBox()
        self.crafting_price.setRange(0, 2_000_000_000)
        self.crafting_price.setSingleStep(100)
        self.crafting_price.setValue(settings.crafting_price)
        self.crafting_price.setSuffix(" silver / craft")
        craft_search_controls = QGridLayout()
        for column, label in enumerate(("Keresés", "Kategória", "Tier", "Enchant", "Quality")):
            craft_search_controls.addWidget(QLabel(label), 0, column)
        for column, widget in enumerate((self.craft_search, self.craft_category, self.craft_tier, self.craft_enchantment, self.craft_quality)):
            craft_search_controls.addWidget(widget, 1, column)
        setup_layout.addLayout(craft_search_controls)
        form.addRow("Forrás", self.craft_source)
        form.addRow("", self.premium)
        form.addRow("Crafting állomásdíj", self.crafting_price)
        setup_layout.addLayout(form)
        self.craft_cities = CitySelector("Alapanyag- és értékesítési városok")
        setup_layout.addWidget(self.craft_cities)
        find = QPushButton("Receptek keresése")
        find.clicked.connect(self.search_recipes)
        setup_layout.addWidget(find, 0, Qt.AlignmentFlag.AlignLeft)
        self.craft_cards = ItemCardGrid(self.item_icon_loader)
        self.craft_cards.setMaximumHeight(300)
        self.craft_cards.item_selected.connect(self.select_craft_card)
        self.craft_cards.favorite_requested.connect(self.toggle_craft_favorite)
        setup_layout.addWidget(self.craft_cards)
        for checkbox in self.craft_cities.checkboxes:
            checkbox.toggled.connect(self.show_recipe_materials)
        self.craft_splitter.addWidget(setup_group)

        materials_group = QGroupBox("2 · Szükséges alapanyagok")
        materials_layout = QVBoxLayout(materials_group)
        self.craft_materials = self._table()
        self.craft_materials.setMinimumHeight(150)
        self.craft_source.currentIndexChanged.connect(self.show_recipe_materials)
        materials_layout.addWidget(self.craft_materials)
        craft_detail_layout.addWidget(materials_group)

        result_group = QGroupBox("3 · Nettó crafting eredmény")
        result_layout = QVBoxLayout(result_group)
        calculate = QPushButton("Költség és profit számítása")
        calculate.clicked.connect(self.calculate_craft)
        result_layout.addWidget(calculate, 0, Qt.AlignmentFlag.AlignLeft)
        self.craft_output = QTextEdit()
        self.craft_output.setReadOnly(True)
        result_layout.addWidget(self.craft_output, 1)
        craft_detail_layout.addWidget(result_group, 1)
        self.craft_splitter.addWidget(self.craft_detail)
        self.craft_splitter.setStretchFactor(0, 1)
        self.craft_splitter.setStretchFactor(1, 2)
        self.craft_splitter.splitterMoved.connect(
            lambda *_: self._schedule_splitter_save("craft", self.craft_splitter)
        )
        self.craft_detail.hide()
        layout.addWidget(self.craft_splitter, 1)
        self.load_categories()
        self.craft_category.selection_changed.connect(self.search_recipes)
        return page

    def _refining_page(self) -> QWidget:
        page, layout = self._page(
            "Refining Calculator",
            "Közvetlen refining receptekből számolható beszerzés, visszatérő alapanyag, díj, adó és ROI. "
            "A kalkulátor nem bontja ki a craft-receptfát.",
        )
        setup = QGroupBox("1 · Recept és útvonal")
        setup_layout = QGridLayout(setup)
        self.refining_search = QLineEdit()
        self.refining_search.setPlaceholderText("Például: Planks, T4_PLANKS")
        self.refining_search.returnPressed.connect(self.search_refining_recipes)
        self.refining_recipe = QComboBox()
        self.refining_recipe.setMinimumWidth(330)
        self.refining_recipe.currentIndexChanged.connect(self._load_refining_profile)
        self.refining_amount = QSpinBox()
        self.refining_amount.setRange(1, 1_000_000)
        self.refining_amount.setValue(1)
        self.refining_amount.setSuffix(" batch")
        search = QPushButton("Receptek keresése")
        search.clicked.connect(self.search_refining_recipes)
        import_button = QPushButton("Recipes importja")
        import_button.clicked.connect(self.import_refining_data)
        setup_layout.addWidget(QLabel("Recept keresése"), 0, 0)
        setup_layout.addWidget(self.refining_search, 1, 0)
        setup_layout.addWidget(search, 1, 1)
        setup_layout.addWidget(import_button, 1, 2)
        setup_layout.addWidget(QLabel("Recept"), 0, 3)
        setup_layout.addWidget(self.refining_recipe, 1, 3)
        setup_layout.addWidget(QLabel("Mennyiség"), 0, 4)
        setup_layout.addWidget(self.refining_amount, 1, 4)
        setup_layout.setColumnStretch(0, 1)
        setup_layout.setColumnStretch(3, 2)
        layout.addWidget(setup)

        route = QGroupBox("2 · Városok és refining profil")
        route_layout = QGridLayout(route)
        self.refining_buy_city = QComboBox()
        self.refining_city = QComboBox()
        self.refining_sell_city = QComboBox()
        self.refining_buy_city.addItems(CITIES)
        self.refining_sell_city.addItems(CITIES)
        with open_db() as conn:
            refining_cities = load_refining_cities(conn)
        self.refining_city.addItems([city.city for city in refining_cities])
        self.refining_city.currentIndexChanged.connect(self._load_refining_profile)
        self.refining_base_rrr = QSpinBox()
        self.refining_base_rrr.setRange(0, 100)
        self.refining_base_rrr.setSuffix(" %")
        self.refining_focus_rrr = QSpinBox()
        self.refining_focus_rrr.setRange(0, 100)
        self.refining_focus_rrr.setSuffix(" %")
        self.refining_station_fee = QSpinBox()
        self.refining_station_fee.setRange(0, 2_000_000_000)
        self.refining_station_fee.setSuffix(" silver / batch")
        self.refining_transport_fee = QSpinBox()
        self.refining_transport_fee.setRange(0, 2_000_000_000)
        self.refining_transport_fee.setSuffix(" silver")
        self.refining_premium = QCheckBox("Prémium market díjak")
        self.refining_premium.setChecked(True)
        self.refining_focus = QCheckBox("Focus használata")
        save_profile = QPushButton("Profil mentése")
        save_profile.clicked.connect(self.save_refining_profile)
        route_layout.addWidget(QLabel("Vétel városa"), 0, 0)
        route_layout.addWidget(self.refining_buy_city, 1, 0)
        route_layout.addWidget(QLabel("Refine város"), 0, 1)
        route_layout.addWidget(self.refining_city, 1, 1)
        route_layout.addWidget(QLabel("Eladás városa"), 0, 2)
        route_layout.addWidget(self.refining_sell_city, 1, 2)
        route_layout.addWidget(QLabel("Alap RRR"), 0, 3)
        route_layout.addWidget(self.refining_base_rrr, 1, 3)
        route_layout.addWidget(QLabel("Focus RRR"), 0, 4)
        route_layout.addWidget(self.refining_focus_rrr, 1, 4)
        route_layout.addWidget(QLabel("Station fee"), 0, 5)
        route_layout.addWidget(self.refining_station_fee, 1, 5)
        route_layout.addWidget(QLabel("Transport"), 0, 6)
        route_layout.addWidget(self.refining_transport_fee, 1, 6)
        route_layout.addWidget(self.refining_premium, 2, 0, 1, 2)
        route_layout.addWidget(self.refining_focus, 2, 2, 1, 2)
        route_layout.addWidget(save_profile, 2, 4, 1, 2)
        layout.addWidget(route)

        result = QGroupBox("3 · Eredmény")
        result_layout = QVBoxLayout(result)
        calculate = QPushButton("Útvonal és ROI számítása")
        calculate.clicked.connect(self.calculate_refining)
        result_layout.addWidget(calculate, 0, Qt.AlignmentFlag.AlignLeft)
        self.refining_output = QTextEdit()
        self.refining_output.setReadOnly(True)
        self.refining_output.setMinimumHeight(240)
        result_layout.addWidget(self.refining_output)
        layout.addWidget(result, 1)
        self._load_refining_profile()
        self.search_refining_recipes()
        return page

    def _favorites_page(self) -> QWidget:
        page, layout = self._page(
            "Kedvencek",
            "Az árfigyeléshez, craftinghoz és flippeléshez elmentett itemek külön gyűjteményben.",
        )
        refresh = QPushButton("Kedvencek frissítése")
        refresh.clicked.connect(self.refresh_favorites)
        layout.addWidget(refresh, 0, Qt.AlignmentFlag.AlignLeft)
        self.favorite_tabs = QTabWidget()
        self.favorite_tables: dict[str, QTableWidget] = {}
        for context, label in (
            ("price", "Item-ár"),
            ("crafting", "Crafting"),
            ("flip", "Market flip"),
        ):
            table = self._table()
            self.favorite_tables[context] = table
            self.favorite_tabs.addTab(table, label)
        layout.addWidget(self.favorite_tabs, 1)
        return page

    def _database_page(self) -> QWidget:
        page, layout = self._page(
            "Adatközpont",
            "Katalógus, receptek és piaci adatok kezelése. A teljes újraépítés destruktív művelet.",
        )
        self.db_status = QLabel("Állapot betöltése...")
        self.db_status.setObjectName("databaseStatus")
        layout.addWidget(self.db_status)
        self.database_cities = CitySelector("API-frissítés városai")
        layout.addWidget(self.database_cities)
        buttons = QHBoxLayout()
        status = QPushButton("Állapot frissítése")
        status.clicked.connect(self.refresh_dashboard)
        one = QPushButton("Egy item API-frissítése")
        one.clicked.connect(self.refresh_one_item)
        all_items = QPushButton("Teljes piaci frissítés")
        all_items.clicked.connect(self.refresh_all_items)
        rebuild = QPushButton("Adatbázis újraépítése")
        rebuild.clicked.connect(self.rebuild_database)
        craft_import = QPushButton("Craft adatok importja")
        craft_import.clicked.connect(self.import_crafting_data)
        category_import = QPushButton("Kategóriafa frissítése")
        category_import.clicked.connect(self.refresh_item_categories)
        buttons.addWidget(status)
        buttons.addWidget(one)
        buttons.addWidget(all_items)
        buttons.addWidget(rebuild)
        buttons.addWidget(craft_import)
        buttons.addWidget(category_import)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.refresh_progress_label = QLabel("Nincs futó adatfrissítés")
        self.refresh_progress_label.setObjectName("progressLabel")
        self.refresh_progress = QProgressBar()
        self.refresh_progress.setRange(0, 100)
        self.refresh_progress.setValue(0)
        self.refresh_progress.setTextVisible(True)
        layout.addWidget(self.refresh_progress_label)
        layout.addWidget(self.refresh_progress)
        warning = QLabel(
            "A katalógus újraépítése nem törli a külön tárolt piaci rekordokat és kedvenceket."
        )
        warning.setObjectName("notice")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        layout.addStretch()
        return page

    @staticmethod
    def _metric_card(label: str, value: str) -> QFrame:
        card = QFrame()
        card.setObjectName("metricCard")
        layout = QVBoxLayout(card)
        caption = QLabel(label)
        caption.setObjectName("metricLabel")
        number = QLabel(value)
        number.setObjectName("metricValue")
        card.value_label = number
        layout.addWidget(caption)
        layout.addWidget(number)
        return card

    @staticmethod
    def _action_card(title: str, description: str, callback: Callable[[], None]) -> QFrame:
        card = QFrame()
        card.setObjectName("actionCard")
        layout = QVBoxLayout(card)
        label = QLabel(title)
        label.setObjectName("actionTitle")
        text = QLabel(description)
        text.setWordWrap(True)
        button = QPushButton("Megnyitás  ›")
        button.clicked.connect(callback)
        layout.addWidget(label)
        layout.addWidget(text)
        layout.addStretch()
        layout.addWidget(button)
        return card

    @staticmethod
    def _table() -> QTableWidget:
        table = QTableWidget()
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setWordWrap(False)
        return table

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QWidget { background: #10161d; color: #e7edf3; font-size: 13px; }
            QLabel { background: transparent; }
            #pageScroll { background: #10161d; border: 0; }
            #pageScroll > QWidget > QWidget { background: #10161d; }
            QMenuBar { background: #10161d; color: #aebdca; }
            QMenuBar::item:selected { background: #1d2b36; }
            #sidebar { background: #0b1117; border-right: 1px solid #253440; }
            #brand { color: #f2b84b; font-size: 22px; font-weight: 800; letter-spacing: 2px; }
            #sidebarSubtitle, #sidebarHint { color: #718392; }
            #sidebarHint { padding: 10px 2px; }
            QPushButton {
                background: #1b2a35; border: 1px solid #2c4351; border-radius: 7px;
                padding: 10px 15px; color: #dce7ed; font-weight: 600;
            }
            QPushButton:hover { background: #263c4a; border-color: #e0a842; }
            QPushButton:pressed, QPushButton:checked { background: #d99a31; color: #10161d; }
            #sidebar QPushButton { text-align: left; border: 0; background: transparent; color: #9badba; }
            #sidebar QPushButton:hover { background: #172630; color: #fff; }
            #sidebar QPushButton:checked { background: #d99a31; color: #10161d; }
            #pageTitle { font-size: 30px; font-weight: 800; color: #f3f6f8; }
            #pageDescription { color: #8fa1ae; font-size: 14px; }
            #sectionTitle {
                color: #dce7ed; font-size: 16px; font-weight: 750;
                padding-top: 4px;
            }
            #itemCardScroll, #itemCardContent { background: transparent; border: 0; }
            #itemCard {
                background: #151f28; border: 1px solid #293d49; border-radius: 11px;
            }
            #itemCard:hover { background: #1a2832; border-color: #8b6a31; }
            #itemCard[selected="true"] {
                background: #292419; border: 2px solid #d99a31;
            }
            #itemIcon {
                background: #0c141a; border: 1px solid #2d414d; border-radius: 9px;
                color: #7f929f; font-weight: 700;
            }
            #itemIcon[failed="true"] { color: #a87777; }
            #itemCardTitle { color: #f3f6f8; font-size: 14px; font-weight: 750; }
            #itemCardMeta { color: #d9ad5d; font-size: 11px; }
            #itemCardId { color: #718592; font-family: Consolas; font-size: 10px; }
            #favoriteButton {
                border: 0; background: transparent; color: #8ea0ac;
                font-size: 22px; padding: 0;
            }
            #favoriteButton:hover, #favoriteButton[favorite="true"] {
                background: transparent; color: #e0a842;
            }
            #detailPanel {
                background: #121d25; border: 1px solid #2c4351; border-radius: 12px;
            }
            #masterDetailSplitter::handle {
                background: #273b48; width: 5px; margin: 8px 1px;
                border-radius: 2px;
            }
            #masterDetailSplitter::handle:hover { background: #d99a31; }
            #marketCityCard {
                background: #151f28; border: 1px solid #2b414e; border-radius: 11px;
            }
            #marketCardIcon {
                background: #0c141a; border: 1px solid #2d414d; border-radius: 9px;
                color: #d9ad5d; font-weight: 700;
            }
            #marketCardTitle { color: #f2b84b; font-size: 16px; font-weight: 750; }
            #marketCardLabel { color: #91a4b1; font-size: 11px; }
            #marketCardPrice { color: #edf3f6; font-weight: 700; }
            #marketCardAge { color: #78909d; font-size: 10px; min-width: 65px; }
            #freshnessBadge { padding: 3px 7px; border-radius: 7px; font-size: 10px; font-weight: 700; }
            #freshnessBadge[freshness="fresh"] { background: #173b2b; color: #6fd39a; }
            #freshnessBadge[freshness="aging"] { background: #49391c; color: #f0c66d; }
            #freshnessBadge[freshness="stale"] { background: #482526; color: #ef8585; }
            #freshnessBadge[freshness="missing"] { background: #26333c; color: #8da0ad; }
            #categoryButton {
                background: #0d141b; border: 1px solid #2c4351; border-radius: 6px;
                padding: 8px 12px; text-align: left; color: #dce7ed;
            }
            #categoryButton:hover { border-color: #d99a31; background: #17232d; }
            #categoryPopup {
                background: #121c24; border: 1px solid #49606d; border-radius: 9px;
            }
            #categoryPopupTitle { color: #f2b84b; font-size: 15px; font-weight: 750; }
            #categoryTree {
                background: transparent; border: 0; color: #b8c7d1;
                outline: 0;
            }
            #categoryTree::item { min-height: 27px; border-radius: 5px; }
            #categoryTree::item:hover { background: #1d2d37; color: #ffffff; }
            #categoryTree::item:selected { background: #735321; color: #ffffff; }
            #metricCard, #actionCard {
                background: #17232d; border: 1px solid #263b49; border-radius: 10px;
            }
            QGroupBox {
                background: #131e27; border: 1px solid #2a3d49; border-radius: 12px;
                margin-top: 13px; padding: 16px 12px 12px 12px; font-weight: 700;
                color: #f2b84b;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 14px; padding: 0 7px;
                background: #10161d;
            }
            #filterPanel {
                background: #121c24; border: 1px solid #263a46; border-radius: 9px;
            }
            #filterTitle { color: #cbd7df; font-weight: 700; }
            #miniButton { padding: 4px 9px; font-size: 11px; min-width: 0; }
            #cityChip {
                background: #172630; border: 1px solid #2b4350; border-radius: 6px;
                padding: 6px 9px; color: #aebfca;
            }
            #cityChip:checked { background: #4b391d; border-color: #d99a31; color: #fff2d2; }
            #metricLabel { color: #8093a1; font-size: 11px; font-weight: 700; }
            #metricValue { color: #f2b84b; font-size: 27px; font-weight: 800; }
            #actionCard { min-width: 220px; min-height: 150px; padding: 4px; }
            #actionTitle { color: #f2b84b; font-size: 17px; font-weight: 700; }
            #databaseStatus { background: #17232d; border: 1px solid #263b49; border-radius: 8px; padding: 16px; font-size: 16px; }
            #notice { background: #2b2418; border: 1px solid #725a2d; border-radius: 8px; padding: 14px; color: #f0cb80; }
            QLineEdit, QSpinBox, QComboBox, QTextEdit {
                background: #0d141b; border: 1px solid #2c4351; border-radius: 6px; padding: 8px;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus {
                border: 1px solid #d99a31;
            }
            QListWidget, QTableWidget {
                background: #0d141b; alternate-background-color: #121d26;
                border: 1px solid #263b49; border-radius: 7px; gridline-color: #20313d;
                color: #dce7ed;
            }
            QTableWidget::item { background: #0d141b; color: #dce7ed; padding: 4px; }
            QTableWidget::item:alternate { background: #121d26; color: #dce7ed; }
            QTableWidget::item:selected { background: #735321; color: #ffffff; }
            QTableCornerButton::section { background: #1b2a35; border: 0; }
            QTableWidget::item:selected, QListWidget::item:selected { background: #735321; color: white; }
            QHeaderView::section { background: #1b2a35; color: #b8c9d4; padding: 8px; border: 0; }
            QProgressBar {
                background: #0d141b; border: 1px solid #2c4351; border-radius: 7px;
                height: 20px; text-align: center; color: #e7edf3;
            }
            QProgressBar::chunk { background: #d99a31; border-radius: 6px; }
            #progressLabel { color: #9eb0bc; font-weight: 600; }
            QScrollBar:vertical { background: #10161d; width: 12px; }
            QScrollBar::handle:vertical { background: #324957; border-radius: 6px; }
            """
        )

    def _splitter_target(self, name: str, total_width: int, default_ratio: int) -> int:
        with open_db() as conn:
            ratio = get_user_int(conn, f"splitter.{name}.right_ratio", default_ratio)
        ratio = min(8000, max(3000, ratio))
        return max(360, int(total_width * ratio / 10_000))

    def _schedule_splitter_save(self, name: str, splitter: QSplitter) -> None:
        timer = self._splitter_save_timers.get(name)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda n=name, s=splitter: self._save_splitter(n, s))
            self._splitter_save_timers[name] = timer
        timer.start(250)

    @staticmethod
    def _save_splitter(name: str, splitter: QSplitter) -> None:
        sizes = splitter.sizes()
        total = sum(sizes)
        if len(sizes) != 2 or total <= 0 or min(sizes) < 50:
            return
        with open_db() as conn:
            set_user_int(conn, f"splitter.{name}.right_ratio", round(sizes[1] * 10_000 / total))

    def _set_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for number, button in enumerate(self.nav_buttons):
            button.setChecked(number == index)

    def show_dashboard(self) -> None:
        self._set_page(0)
        self.refresh_dashboard()

    def show_items(self) -> None:
        self._set_page(1)

    def show_flips(self) -> None:
        self._set_page(2)

    def show_craft(self) -> None:
        self._set_page(3)

    def show_refining(self) -> None:
        self._set_page(4)

    def show_favorites(self) -> None:
        self._set_page(5)
        self.refresh_favorites()

    def show_database(self) -> None:
        self._set_page(6)
        self.refresh_dashboard()

    def search_refining_recipes(self) -> None:
        term = self.refining_search.text().strip()
        parameters: list[object] = []
        predicate = ""
        if term:
            predicate = "WHERE (i.name_en LIKE ? OR r.output_item_uniquename LIKE ?)"
            parameters.extend((f"%{term}%", f"%{term}%"))
        with open_db() as conn:
            rows = conn.execute(
                """SELECT r.output_item_uniquename, r.variant_index, i.name_en,
                          i.tier, r.output_amount, COUNT(m.material_uniquename)
                   FROM refining_recipes r
                   JOIN items i ON i.id=r.output_item_id
                   LEFT JOIN refining_recipe_materials m ON m.recipe_id=r.id
                   """ + predicate +
                """ GROUP BY r.id
                   ORDER BY i.tier, i.name_en, r.output_item_uniquename,
                            r.variant_index LIMIT 200""",
                parameters,
            ).fetchall()
        self.refining_recipe.clear()
        for output, variant, name, tier, amount, material_count in rows:
            label = (
                f"{name or output} · {output} · recept {variant + 1} "
                f"({material_count} alapanyag, {amount} output)"
            )
            self.refining_recipe.addItem(label, (output, variant))
        self.statusBar().showMessage(f"{len(rows)} refining recept")

    def _load_refining_profile(self, _index: int = -1) -> None:
        if not hasattr(self, "refining_city"):
            return
        city_name = self.refining_city.currentText()
        with open_db() as conn:
            profiles = {city.city: city for city in load_refining_cities(conn)}
        profile = profiles.get(city_name)
        if profile is None:
            return
        self.refining_base_rrr.setValue(round(float(profile.base_return_rate) * 100))
        self.refining_focus_rrr.setValue(round(float(profile.focus_return_rate) * 100))
        self.refining_station_fee.setValue(profile.station_fee)

    def save_refining_profile(self) -> None:
        city_name = self.refining_city.currentText()
        with open_db() as conn:
            profiles = {city.city: city for city in load_refining_cities(conn)}
            profile = profiles.get(city_name)
            if profile is None:
                self.statusBar().showMessage("Nincs kiválasztott refining város.")
                return
            save_refining_city(
                conn,
                RefiningCityConfig(
                    city=profile.city,
                    resource_type=profile.resource_type,
                    refined_item=profile.refined_item,
                    local_production_bonus=profile.local_production_bonus,
                    base_return_rate=self.refining_base_rrr.value() / 100,
                    focus_return_rate=self.refining_focus_rrr.value() / 100,
                    station_fee=self.refining_station_fee.value(),
                    refined_item_id=profile.refined_item_id,
                ),
            )
        self.statusBar().showMessage(f"Refining profil mentve: {city_name}")

    def _selected_refining_recipe(self):
        data = self.refining_recipe.currentData()
        if not data:
            return None
        output, variant = data
        with open_db() as conn:
            recipes = list_refining_recipes(conn, output)
        return next(
            (recipe for recipe in recipes if recipe.variant_index == variant),
            None,
        )

    def calculate_refining(self) -> None:
        recipe = self._selected_refining_recipe()
        if recipe is None:
            self.refining_output.setPlainText(
                "Nincs kiválasztott recept. Importáld az items.json refining receptjeit."
            )
            return
        with open_db() as conn:
            profiles = {city.city: city for city in load_refining_cities(conn)}
            city = profiles.get(self.refining_city.currentText())
            if city is None:
                self.refining_output.setPlainText("Nincs érvényes refining profil.")
                return
            # User-editable values are applied to this calculation immediately;
            # saving the profile is optional.
            city = RefiningCityConfig(
                city=city.city,
                resource_type=city.resource_type,
                refined_item=city.refined_item,
                local_production_bonus=city.local_production_bonus,
                base_return_rate=self.refining_base_rrr.value() / 100,
                focus_return_rate=self.refining_focus_rrr.value() / 100,
                station_fee=self.refining_station_fee.value(),
                refined_item_id=city.refined_item_id,
            )
            material_prices, output_price = market_prices_for_route(
                conn,
                recipe,
                self.refining_buy_city.currentText(),
                self.refining_sell_city.currentText(),
            )
        result = calculate_refining_result(
            recipe,
            city,
            material_prices=material_prices,
            output_price=output_price,
            batches=self.refining_amount.value(),
            buy_city=self.refining_buy_city.currentText(),
            sell_city=self.refining_sell_city.currentText(),
            transport_fee=self.refining_transport_fee.value(),
            station_fee=self.refining_station_fee.value(),
            premium=self.refining_premium.isChecked(),
            use_focus=self.refining_focus.isChecked(),
        )
        material_lines = "\n".join(
            f"  {material.item_uniquename}: {material.required_amount:,} db · "
            f"várható vissza {material.returned_amount} · effektív költség "
            f"{material.effective_cost:,}"
            for material in result.materials
        )
        focus_line = (
            f"Focus: {result.focus_used:,} · Silver / Focus: "
            f"{result.silver_per_focus}"
            if result.focus_used
            else "Focus: nem használva"
        )
        self.refining_output.setPlainText(
            f"Recept: {recipe.output_item_uniquename} · {result.output_amount:,} output\n"
            f"Útvonal: {result.buy_city} → {result.refining_city} → {result.sell_city}\n"
            f"RRR: {result.return_rate * 100:.2f}%\n\n"
            f"Alapanyagok:\n{material_lines}\n\n"
            f"Input költség: {result.input_cost:,}\n"
            f"Station fee: {result.station_fee:,}\n"
            f"Transport: {result.transport_fee:,}\n"
            f"Teljes befektetés: {result.total_cost:,}\n"
            f"Bruttó bevétel: {result.gross_revenue:,}\n"
            f"Market tax + listing: {result.market_tax + result.market_listing_fee:,}\n"
            f"Nettó bevétel: {result.net_revenue:,}\n"
            f"Profit: {result.profit:,} silver\n"
            f"ROI: {result.roi_percent}%\n"
            f"{focus_line}"
        )

    def import_refining_data(self) -> None:
        def import_data() -> tuple[int, int, int]:
            return import_refining_recipes()

        def done(result: tuple[int, int, int]) -> None:
            recipes, materials, unresolved = result
            self.search_refining_recipes()
            self.statusBar().showMessage(
                f"Refining import kész: {recipes} recept, {materials} alapanyag, "
                f"{unresolved} feloldatlan hivatkozás"
            )

        self.run_background(import_data, done)

    def refresh_favorites(self) -> None:
        with open_db() as conn:
            rows = list_favorites(conn)
        grouped = {context: [] for context in self.favorite_tables}
        for context, item_id, name, tier, enchantment, created_at in rows:
            grouped[context].append(
                (name or item_id, item_id, f"T{tier}" if tier else "-", f".{enchantment or 0}", created_at)
            )
        for context, table in self.favorite_tables.items():
            self.fill_table(
                table,
                ["Item", "Unique name", "Tier", "Enchant", "Hozzáadva"],
                grouped[context],
            )

    def refresh_dashboard(self) -> None:
        try:
            with open_db() as conn:
                items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
                prices = conn.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0]
                cities = conn.execute("SELECT COUNT(DISTINCT city) FROM market_prices").fetchone()[0]
                recipe_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recipes'"
                ).fetchone()
                recipes = conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] if recipe_table else 0
                favorites = list_favorites(conn)
            grouped = {context: [] for context in self.dashboard_favorite_tables}
            for context, item_id, name, tier, enchantment, _created_at in favorites:
                grouped[context].append((name or item_id, f"T{tier}.{enchantment or 0}"))
            for context, table in self.dashboard_favorite_tables.items():
                self.fill_table(table, ["Item", "Szint"], grouped[context][:12])
            self.db_status.setText(
                f"Items: {items:,}    |    Piaci rekordok: {prices:,}    |    "
                f"Receptek: {recipes:,}    |    Városok: {cities}"
            )
            self.statusBar().showMessage("Adatbázis állapot frissítve")
        except sqlite3.Error as error:
            self.statusBar().showMessage(f"Adatbázis-hiba: {error}")

    def load_categories(self) -> None:
        """Load the imported hierarchy into the compact category popup."""

        counts: dict[tuple[str, ...], int] = {}
        with open_db() as conn:
            category_rows = conn.execute(
                """SELECT path, category_id, parent_path, hidden
                   FROM item_categories
                   ORDER BY level, parent_path, sort_order, category_id"""
            ).fetchall()
            item_paths = conn.execute(
                """SELECT shopcategory, shopsubcategory,
                          shopsubcategory2, shopsubcategory3
                   FROM items"""
            ).fetchall()
        for item_path in item_paths:
            prefix: list[str] = []
            for category_id in item_path:
                if not category_id:
                    break
                prefix.append(category_id)
                key = tuple(prefix)
                counts[key] = counts.get(key, 0) + 1
        self.item_category.set_categories(category_rows, counts)
        if hasattr(self, "craft_category"):
            self.craft_category.set_categories(category_rows, counts)
        if hasattr(self, "flip_category"):
            self.flip_category.set_categories(category_rows, counts)

    def selected_category_path(self) -> tuple[str, ...]:
        return self.item_category.selected_path

    def search_items(self) -> None:
        term = self.item_search.text().strip()
        predicates = ["tier = ?"]
        parameters: list[object] = [self.item_tier.value()]
        if term:
            predicates.append(
                "(name_en LIKE ? COLLATE NOCASE OR uniquename LIKE ? COLLATE NOCASE)"
            )
            parameters.extend((f"%{term}%", f"%{term}%"))
        category_columns = (
            "shopcategory",
            "shopsubcategory",
            "shopsubcategory2",
            "shopsubcategory3",
        )
        for column, category_id in zip(
            category_columns, self.selected_category_path(), strict=False
        ):
            predicates.append(f"{column} = ?")
            parameters.append(category_id)
        enchantment = self.item_enchantment.currentData()
        if enchantment >= 0:
            predicates.append("enchantment = ?")
            parameters.append(enchantment)
        with open_db() as conn:
            rows = conn.execute(
                f"""SELECT uniquename, name_en, tier, shopcategory, enchantment
                    FROM items WHERE {' AND '.join(predicates)}
                    ORDER BY name_en, enchantment, uniquename LIMIT 100""",
                parameters,
            ).fetchall()
        self.item_rows = list(rows)
        self.selected_ids = []
        self.selected_name = ""
        self.item_cards.set_items(
            self.item_rows,
            self.item_quality.currentData(),
            favorite_ids=self._price_favorite_ids(),
        )
        self.statusBar().showMessage(f"{len(rows)} találat")

    def select_item_card(self, selected: tuple) -> None:
        self.selected_name = selected[1] or selected[0]
        self.selected_ids = [selected[0]]
        self._reveal_item_detail()
        self.statusBar().showMessage(f"Kiválasztva: {self.selected_name}")

    def _reveal_item_detail(self) -> None:
        if self.item_detail.isVisible():
            return
        self.item_detail.show()
        total_width = max(700, self.item_splitter.width())
        target = self._splitter_target("item", total_width, 5600)
        self.item_detail_animation = QVariantAnimation(self)
        self.item_detail_animation.setDuration(260)
        self.item_detail_animation.setStartValue(0)
        self.item_detail_animation.setEndValue(target)
        self.item_detail_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.item_detail_animation.valueChanged.connect(
            lambda width: self.item_splitter.setSizes(
                [max(320, total_width - int(width)), int(width)]
            )
        )
        self.item_detail_animation.start()

    def toggle_price_favorite(self, item_id: str) -> None:
        with open_db() as conn:
            enabled = toggle_favorite(conn, "price", item_id)
        self.item_cards.set_favorite_state(item_id, enabled)
        action = "Kedvencekhez adva" if enabled else "Eltávolítva a kedvencekből"
        self.statusBar().showMessage(f"{action}: {item_id}")

    def _price_favorite_ids(self) -> set[str]:
        with open_db() as conn:
            return favorite_ids(conn, "price")

    def refresh_item_card_quality(self, _index: int = -1) -> None:
        if not self.item_rows:
            return
        selected_id = self.selected_ids[0] if self.selected_ids else None
        self.item_cards.set_items(
            self.item_rows,
            self.item_quality.currentData(),
            selected_id,
            self._price_favorite_ids(),
        )

    def show_prices(self) -> None:
        if not self.selected_ids:
            return
        cities = self.item_cities.selected_cities()
        if not cities:
            self.statusBar().showMessage("Jelölj ki legalább egy várost.")
            return
        placeholders = ",".join("?" for _ in self.selected_ids)
        city_placeholders = ",".join("?" for _ in cities)
        quality = self.item_quality.currentData()
        quality_clause = " AND mp.quality = ?" if quality else ""
        parameters: list[object] = [*self.selected_ids, *cities]
        if quality:
            parameters.append(quality)
        with open_db() as conn:
            rows = conn.execute(
                f"""SELECT mp.city, mp.quality, mp.enchantment,
                           mp.sell_price_min, mp.sell_price_min_date,
                           mp.sell_price_max, mp.sell_price_max_date,
                           mp.buy_price_min, mp.buy_price_min_date,
                           mp.buy_price_max, mp.buy_price_max_date
                    FROM market_prices mp WHERE mp.item_uniquename IN ({placeholders})
                    AND mp.city IN ({city_placeholders}) {quality_clause}
                    AND (sell_price_min > 0 OR buy_price_max > 0)
                    ORDER BY mp.city, mp.enchantment, mp.quality""",
                parameters,
            ).fetchall()
        self.price_cards.set_quotes(
            self.selected_ids[0], quality or 1, list(rows)
        )
        self.item_detail_results.setCurrentWidget(self.price_cards)

    def refresh_selected_history(self) -> None:
        if not self.selected_ids:
            self.statusBar().showMessage("Előbb válassz itemet.")
            return
        if self.thread is not None and self.thread.isRunning():
            self.statusBar().showMessage("Már fut egy háttérművelet.")
            return
        item_ids = list(self.selected_ids)
        cities = self.item_cities.selected_cities()
        if not cities:
            self.statusBar().showMessage("Jelölj ki legalább egy várost.")
            return
        quality = self.item_quality.currentData()
        qualities = [quality] if quality else list(QUALITY_LABELS)
        today = datetime.now(timezone.utc).date()
        start_date = (today - timedelta(days=14)).isoformat()
        end_date = today.isoformat()

        def refresh() -> int:
            payload = fetch_history(
                item_ids,
                locations=cities,
                qualities=qualities,
                time_scale=24,
                start_date=start_date,
                end_date=end_date,
            )
            with open_db() as conn:
                return save_history(conn, payload, 24)

        def done(saved: int) -> None:
            self.statusBar().showMessage(f"History frissítve: {saved} pont")
            self.show_prices()

        self.statusBar().showMessage("14 napos AODP history letöltése…")
        self.run_background(refresh, done)

    def show_item_flips(self) -> None:
        if not self.selected_ids:
            return
        displayed_cities = self.item_cities.selected_cities()
        excluded_cities = set(self.item_flip_excluded.selected_cities())
        cities = [city for city in displayed_cities if city not in excluded_cities]
        if not cities:
            self.statusBar().showMessage("A kizárások után nem maradt vizsgálandó város.")
            return
        quality = self.item_quality.currentData()
        qualities = [quality] if quality else None
        with open_db() as conn:
            opportunities = scan_opportunities(
                conn,
                MarketStrategy.INSTANT,
                market_fee_policy(self.premium.isChecked()),
                OpportunityRules(min_profit=1, max_age_minutes=1_440),
                item_ids=self.selected_ids,
                cities=cities,
                qualities=qualities,
                limit=100,
            )
        if not opportunities:
            self.statusBar().showMessage(
                "A kizárt város nélkül nincs pozitív, friss flip ehhez az itemhez. "
                "Próbáld meg frissíteni a piaci adatokat."
            )
        rows = [(
            row.source_city, row.destination_city, row.quality,
            row.buy_price, row.expected_sale_price,
            row.transaction_tax, row.net_profit, f"{row.roi_percent}%",
        ) for row in opportunities]
        self.fill_table(
            self.item_table,
            ["Vétel innen", "Eladás ide", "Q", "Vételár", "Eladási ár", "Adó", "Nettó profit", "ROI"],
            rows,
        )
        self.item_detail_results.setCurrentWidget(self.item_table)

    def search_recipes(self) -> None:
        term = self.craft_search.text().strip()
        predicates = ["i.tier=?"]
        parameters: list[object] = [self.craft_tier.value()]
        if term:
            predicates.append("(i.name_en LIKE ? OR i.uniquename LIKE ?)")
            parameters.extend((f"%{term}%", f"%{term}%"))
        for column, category_id in zip(
            ("i.shopcategory", "i.shopsubcategory", "i.shopsubcategory2", "i.shopsubcategory3"),
            self.craft_category.selected_path,
            strict=False,
        ):
            predicates.append(f"{column}=?")
            parameters.append(category_id)
        enchantment = self.craft_enchantment.currentData()
        if enchantment >= 0:
            predicates.append("i.enchantment=?")
            parameters.append(enchantment)
        with open_db() as conn:
            rows = conn.execute(
                """SELECT r.id, r.item_uniquename, i.name_en, r.variant_index,
                          r.output_amount, i.tier, i.shopcategory, i.enchantment
                   FROM recipes r JOIN items i ON i.id=r.item_id
                   WHERE """ + " AND ".join(predicates) +
                " ORDER BY i.name_en, r.item_uniquename, r.variant_index LIMIT 100",
                parameters,
            ).fetchall()
        self.craft_recipe_rows = list(rows)
        self.selected_craft_row = -1

        self.craft_row_by_item = {}
        card_rows = []
        for index, row in enumerate(rows):
            if row[1] in self.craft_row_by_item:
                continue
            self.craft_row_by_item[row[1]] = index
            card_rows.append((row[1], row[2], row[5], row[6], row[7]))
        with open_db() as conn:
            favorites = favorite_ids(conn, "crafting")
        self.craft_cards.set_items(
            card_rows, self.craft_quality.currentData(), favorite_ids=favorites
        )

    def select_craft_card(self, selected: tuple) -> None:
        self.selected_craft_row = self.craft_row_by_item.get(selected[0], -1)
        self._reveal_craft_detail()
        self.show_recipe_materials()

    def _reveal_craft_detail(self) -> None:
        if self.craft_detail.isVisible():
            return
        self.craft_detail.show()
        total_width = max(700, self.craft_splitter.width())
        target = self._splitter_target("craft", total_width, 5800)
        self.craft_detail_animation = QVariantAnimation(self)
        self.craft_detail_animation.setDuration(260)
        self.craft_detail_animation.setStartValue(0)
        self.craft_detail_animation.setEndValue(target)
        self.craft_detail_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.craft_detail_animation.valueChanged.connect(
            lambda width: self.craft_splitter.setSizes(
                [max(320, total_width - int(width)), int(width)]
            )
        )
        self.craft_detail_animation.start()

    def toggle_craft_favorite(self, item_id: str) -> None:
        with open_db() as conn:
            enabled = toggle_favorite(conn, "crafting", item_id)
        self.craft_cards.set_favorite_state(item_id, enabled)

    def show_recipe_materials(self) -> None:
        rows = self.craft_recipe_rows
        current = self.selected_craft_row
        if not rows or current < 0:
            self.craft_materials.clearContents()
            self.craft_materials.setRowCount(0)
            return
        recipe_id = rows[current][0]
        cities = self.craft_cities.selected_cities()
        if not cities:
            self.statusBar().showMessage("Jelölj ki legalább egy crafting/piaci várost.")
            return
        city_placeholders = ",".join("?" for _ in cities)
        with open_db() as conn:
            materials = conn.execute(
                f"""SELECT rm.material_uniquename,
                          COALESCE(i.name_en, rm.material_uniquename),
                          rm.amount,
                          MIN(CASE WHEN mp.sell_price_min > 0 THEN mp.sell_price_min END),
                          (SELECT mp2.city FROM market_prices mp2
                           WHERE mp2.item_uniquename = rm.material_uniquename
                             AND mp2.city IN ({city_placeholders})
                             AND mp2.sell_price_min > 0
                           ORDER BY mp2.sell_price_min ASC LIMIT 1),
                          rm.returnable
                   FROM recipe_materials rm
                   LEFT JOIN items i ON i.uniquename = rm.material_uniquename
                   LEFT JOIN market_prices mp
                      ON mp.item_uniquename = rm.material_uniquename
                     AND mp.city IN ({city_placeholders})
                   WHERE rm.recipe_id = ?
                   GROUP BY rm.material_uniquename, i.name_en, rm.amount, rm.returnable
                   ORDER BY COALESCE(i.name_en, rm.material_uniquename)""",
                (*cities, *cities, recipe_id),
            ).fetchall()
        source = self.craft_source.currentData()
        display_rows = [
            (
                name,
                unique_name,
                amount,
                "Saját farmolás" if source == "farm" else (
                    f"{price:,}" if price is not None else "N/A"
                ),
                city or "N/A",
                "Igen" if returnable else "Nem",
            )
            for unique_name, name, amount, price, city, returnable in materials
        ]
        self.fill_table(
            self.craft_materials,
            ["Alapanyag", "Uniquename", "Mennyiség", "Egységár", "Legolcsóbb város", "Visszatérhet"],
            display_rows,
        )

    def calculate_craft(self) -> None:
        rows = self.craft_recipe_rows
        if not rows or self.selected_craft_row < 0:
            return
        save_craft_settings(CraftSettings(
            self.premium.isChecked(),
            self.craft_source.currentData(),
            self.crafting_price.value(),
        ))
        recipe_id, output_id, output_name, _variant_index, output_amount = rows[
            self.selected_craft_row
        ][:5]
        cities = self.craft_cities.selected_cities()
        if not cities:
            self.statusBar().showMessage("Jelölj ki legalább egy crafting/piaci várost.")
            return
        city_placeholders = ",".join("?" for _ in cities)
        with open_db() as conn:
            materials = conn.execute(
                "SELECT material_uniquename, amount FROM recipe_materials WHERE recipe_id=?",
                (recipe_id,),
            ).fetchall()
            silver = conn.execute("SELECT silver_cost FROM recipes WHERE id=?", (recipe_id,)).fetchone()[0]
            sale = conn.execute(
                f"""SELECT MAX(buy_price_max) FROM market_prices
                    WHERE item_uniquename=? AND city IN ({city_placeholders})""",
                (output_id, *cities),
            ).fetchone()[0]
            station_cost = self.crafting_price.value()
            cost = silver + station_cost
            missing: list[str] = []
            if self.craft_source.currentData() == "buy":
                for material_id, amount in materials:
                    price = conn.execute(
                        f"""SELECT MIN(sell_price_min) FROM market_prices
                            WHERE item_uniquename=? AND sell_price_min>0
                              AND city IN ({city_placeholders})""",
                        (material_id, *cities),
                    ).fetchone()[0]
                    if price is None:
                        missing.append(material_id)
                    else:
                        cost += price * amount
        self.show_recipe_materials()
        if missing and self.craft_source.currentData() == "buy":
            self.craft_output.setPlainText(
                f"{output_name}\nVásárlási profit nem számítható.\n"
                f"Hiányzó piaci adatok: {', '.join(missing)}\n"
                f"Farmolási költségalap: {silver + station_cost:,} silver "
                f"(recept: {silver:,}, állomás: {station_cost:,})"
            )
            return
        fees = market_fee_policy(self.premium.isChecked())
        gross_revenue = sale * output_amount if sale is not None else None
        market_tax = fees.transaction_tax(gross_revenue) if gross_revenue is not None else 0
        profit = gross_revenue - market_tax - cost if gross_revenue is not None else None
        self.craft_output.setPlainText(
            f"{output_name} · output: {output_amount} db\nRecept silver: {silver:,}\n"
            f"Crafting állomásdíj: {station_cost:,}\nTeljes költség: {cost:,}\n"
            f"Bruttó eladási bevétel: {gross_revenue:,}\n"
            f"Fix piaci adó: {market_tax:,}\nNettó profit: {profit:,}"
            if profit is not None else f"{output_name}\nEladási piaci adat nincs."
        )

    def search_flip_items(self) -> None:
        predicates = ["i.tier=?", "EXISTS (SELECT 1 FROM market_prices mp WHERE mp.item_uniquename=i.uniquename)"]
        parameters: list[object] = [self.flip_tier.value()]
        term = self.flip_search.text().strip()
        if term:
            predicates.append("(i.name_en LIKE ? OR i.uniquename LIKE ?)")
            parameters.extend((f"%{term}%", f"%{term}%"))
        for column, category_id in zip(
            ("i.shopcategory", "i.shopsubcategory", "i.shopsubcategory2", "i.shopsubcategory3"),
            self.flip_category.selected_path,
            strict=False,
        ):
            predicates.append(f"{column}=?")
            parameters.append(category_id)
        enchantment = self.flip_enchantment.currentData()
        if enchantment >= 0:
            predicates.append("i.enchantment=?")
            parameters.append(enchantment)
        quality = self.flip_quality.currentData()
        if quality:
            predicates.append("EXISTS (SELECT 1 FROM market_prices q WHERE q.item_uniquename=i.uniquename AND q.quality=?)")
            parameters.append(quality)
        with open_db() as conn:
            rows = conn.execute(
                "SELECT i.uniquename,i.name_en,i.tier,i.shopcategory,i.enchantment "
                "FROM items i WHERE " + " AND ".join(predicates) +
                " ORDER BY i.name_en,i.enchantment,i.uniquename LIMIT 100",
                parameters,
            ).fetchall()
            favorites = favorite_ids(conn, "flip")
        self.flip_cards.set_items(list(rows), quality, favorite_ids=favorites)
        self.selected_flip_ids = []

    def select_flip_card(self, selected: tuple) -> None:
        self.selected_flip_ids = [selected[0]]
        self._reveal_flip_detail()
        self.load_global_flips()

    def toggle_flip_favorite(self, item_id: str) -> None:
        with open_db() as conn:
            enabled = toggle_favorite(conn, "flip", item_id)
        self.flip_cards.set_favorite_state(item_id, enabled)

    def _reveal_flip_detail(self) -> None:
        if self.flip_detail.isVisible():
            return
        self.flip_detail.show()
        total_width = max(700, self.flip_splitter.width())
        target = self._splitter_target("flip", total_width, 6200)
        self.flip_detail_animation = QVariantAnimation(self)
        self.flip_detail_animation.setDuration(260)
        self.flip_detail_animation.setStartValue(0)
        self.flip_detail_animation.setEndValue(target)
        self.flip_detail_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.flip_detail_animation.valueChanged.connect(
            lambda width: self.flip_splitter.setSizes([max(320, total_width-int(width)), int(width)])
        )
        self.flip_detail_animation.start()

    def scan_all_flips(self) -> None:
        self.selected_flip_ids = []
        self._reveal_flip_detail()
        self.load_global_flips()

    def load_global_flips(self) -> None:
        if self.thread is not None and self.thread.isRunning():
            self.statusBar().showMessage("Már fut egy háttérművelet.")
            return
        settings = load_craft_settings()
        premium = self.premium.isChecked() if hasattr(self, "premium") else settings.premium
        fees = market_fee_policy(premium)
        strategy = self.flip_strategy.currentData()
        cities = self.flip_cities.selected_cities()
        if not cities:
            self.statusBar().showMessage("Jelölj ki legalább egy várost.")
            return
        result_limit = self.flip_limit.value()
        rules = OpportunityRules(
            min_profit=self.flip_min_profit.value(),
            max_age_minutes=self.flip_max_age.value(),
        )
        premium_text = "prémium" if premium else "nem prémium"
        self.flip_fee_info.setText(
            f"Aktív fix díjprofil: {premium_text} · tranzakciós adó "
            f"{fees.transaction_tax_bps / 100:.1f}% · setup fee "
            f"{fees.setup_fee_bps / 100:.1f}%. Az adatkor az AODP megfigyelési ideje."
        )

        def scan():
            with open_db() as conn:
                return scan_opportunities(
                    conn,
                    strategy,
                    fees,
                    rules,
                    item_ids=self.selected_flip_ids or None,
                    cities=cities,
                    qualities=([self.flip_quality.currentData()] if self.flip_quality.currentData() else None),
                    limit=result_limit,
                )

        self.statusBar().showMessage("Market lehetőségek számítása…")
        self.run_background(scan, self._render_global_flips)

    def _render_global_flips(self, opportunities) -> None:
        rows = [
            (
                row.item_id,
                row.source_city,
                row.destination_city,
                row.quality,
                row.buy_price,
                row.expected_sale_price,
                row.transaction_tax + row.setup_fees,
                row.net_profit,
                f"{row.roi_percent}%",
                f"{max(row.source_age_minutes, row.destination_age_minutes):.0f} p",
                f"{row.confidence * 100:.0f}%",
            )
            for row in opportunities
        ]
        self.fill_table(
            self.global_table,
            [
                "Item", "Vétel innen", "Eladás ide", "Q", "Vételár", "Célár",
                "Adó+díj", "Nettó profit", "ROI", "Adatkor", "Frissességpont",
            ],
            rows,
        )
        self.statusBar().showMessage(f"{len(rows)} nettó lehetőség betöltve")

    @staticmethod
    def fill_table(table: QTableWidget, headers: list[str], rows: list[tuple]) -> None:
        table.clear()
        table.setColumnCount(len(headers))
        table.setRowCount(len(rows))
        table.setHorizontalHeaderLabels(headers)
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                text = f"{value:,}" if isinstance(value, int) else str(value)
                table.setItem(row_index, column_index, QTableWidgetItem(text))
        table.resizeColumnsToContents()

    def refresh_one_item(self) -> None:
        from PyQt6.QtWidgets import QInputDialog
        item_id, ok = QInputDialog.getText(self, "API-frissítés", "API item ID:", text="T4_BAG")
        if ok and item_id.strip():
            cities = self.database_cities.selected_cities()
            if not cities:
                self.statusBar().showMessage("Jelölj ki legalább egy API-várost.")
                return
            self.run_background(
                lambda: self._fetch_one(item_id.strip(), cities),
                lambda result: self._done(f"Mentett rekordok: {result}"),
            )

    def _fetch_one(self, item_id: str, cities: list[str]) -> int:
        with open_db() as conn:
            return save_prices(
                conn,
                fetch_prices_for_chunk([item_id], locations=cities),
            )

    def refresh_all_items(self) -> None:
        if QMessageBox.question(self, "Megerősítés", "Az összes item frissítése sok API-hívás lehet. Folytatod?") != QMessageBox.StandardButton.Yes:
            return
        cities = self.database_cities.selected_cities()
        if not cities:
            self.statusBar().showMessage("Jelölj ki legalább egy API-várost.")
            return

        def refresh(progress_callback) -> int:
            with open_db() as conn:
                return do_fetch(conn, locations=cities, progress_callback=progress_callback)

        self.refresh_progress.setValue(0)
        self.refresh_progress_label.setText("Teljes API-frissítés indítása…")
        self.run_background(
            refresh,
            self._finish_market_refresh,
            self._update_market_progress,
        )

    def _update_market_progress(self, current: int, total: int, message: str) -> None:
        self.refresh_progress.setRange(0, max(1, total))
        self.refresh_progress.setValue(current)
        self.refresh_progress.setFormat(f"%v / {total} csomag · %p%")
        self.refresh_progress_label.setText(message)

    def _finish_market_refresh(self, result: int) -> None:
        self.refresh_progress.setValue(self.refresh_progress.maximum())
        self.refresh_progress_label.setText(f"Kész · {result:,} piaci rekord mentve")
        self._done(f"Mentett rekordok: {result}")

    def rebuild_database(self) -> None:
        if QMessageBox.question(
            self,
            "Megerősítés",
            "Az items.json alapján újraépíted az adatbázist? "
            "A piaci rekordok és a kedvencek megmaradnak.",
        ) != QMessageBox.StandardButton.Yes:
            return

        def rebuild() -> str:
            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "data_sorter.py")],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "Az adatbázis újraépítése sikertelen.")
            return "Adatbázis újraépítve."

        self.run_background(rebuild, self._done)

    def import_crafting_data(self) -> None:
        def import_data() -> str:
            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "crafting_importer.py")],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "A craft import sikertelen.")
            return "Craft adatok importálva."

        self.run_background(import_data, self._done)

    def refresh_item_categories(self) -> None:
        """Import category metadata without deleting cached market prices."""

        def import_categories() -> str:
            with open_db() as conn:
                category_count, item_count = sync_item_category_data(
                    conn, PROJECT_ROOT / "items.json"
                )
            return (
                f"Kategóriafa frissítve: {category_count} csomópont, "
                f"{item_count} item. A piaci adatok megmaradtak."
            )

        self.run_background(import_categories, self._done)

    def _done(self, message: str) -> None:
        self.statusBar().showMessage(message)
        self.refresh_dashboard()
        if hasattr(self, "item_category"):
            self.load_categories()

    def run_background(
        self,
        operation: Callable,
        callback: Callable[[object], None],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self.thread = QThread()
        self.worker = Worker(operation, accepts_progress=progress_callback is not None)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(callback)
        self.worker.failed.connect(lambda error: QMessageBox.critical(self, "Hiba", error))
        if progress_callback is not None:
            self.worker.progress.connect(progress_callback)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(lambda: setattr(self, "worker", None))
        self.thread.start()


def run_gui() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    window = AlbionWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    run_gui()
