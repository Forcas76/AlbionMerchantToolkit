"""Modern PyQt6 interface for Albion Prize Shower."""

from __future__ import annotations

import logging
import sqlite3
import sys
import weakref
import runpy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QEasingCurve, QObject, QStandardPaths, QThread, QTimer, Qt, QUrl, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
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
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
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
from app.services.crafting import (
    build_crafting_dependency_tree,
    import_crafting_data as import_crafting_catalog,
)
from app.core.app_logging import (
    LOG_DIR,
    configure_logging,
    export_diagnostic_bundle,
    install_qt_message_logging,
)
from app.paths import ITEMS_FILE as ITEM_CATALOG_FILE, LOCALIZATION_FILE, PROJECT_ROOT
from app.services.inventory import (
    allocate_inventory,
    get_quantity as get_inventory_quantity,
    inventory_quantities,
    list_inventory,
    remove_item as remove_inventory_item,
    set_quantity as set_inventory_quantity,
)
from app.services.trade_orders import (
    OrderMaterialInput,
    TradeOrderInput,
    activate_order,
    aggregate_order_profit,
    aggregate_order_timeline,
    available_months,
    close_order,
    closed_order_statistics,
    create_order,
    delete_order,
    get_order,
    list_orders,
    update_order,
)
from app.services.craft_settings import CraftSettings, load_craft_settings, save_craft_settings
from app.ui.item_cards import ItemCardGrid, ItemIconLoader, item_icon_url
from app.ui.market_cards import MarketCityCardGrid
from app.ui.crafting_cards import CraftingDependencyTreeView, CraftingMaterialCardGrid
from app.ui.inventory_cards import InventoryCardGrid, InventoryPickerDialog
from app.ui.inventory_price_cards import InventoryValuationGrid
from app.ui.refining_cards import RefiningRecipeCardGrid
from app.ui.trade_order_cards import (
    CompactInventoryList,
    OrderMaterialEditor,
    StatisticsCardGrid,
    TradeOrderCardList,
)
from app.ui.statistics_charts import (
    ComparisonBarChart,
    HorizontalBarChart,
    TimelineChart,
)
from app.ui.safe_inputs import SafeComboBox as QComboBox, SafeSpinBox as QSpinBox
from app.ui.category_picker import CategoryPopupButton

QUALITY_LABELS = {
    1: "Normal",
    2: "Good",
    3: "Outstanding",
    4: "Excellent",
    5: "Masterpiece",
}
LOGGER = logging.getLogger(__name__)


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
        operation_name = getattr(self.operation, "__qualname__", repr(self.operation))
        LOGGER.info("Háttérfeladat indul: %s", operation_name)
        try:
            result = (
                self.operation(self.progress.emit)
                if self.accepts_progress
                else self.operation()
            )
            self.finished.emit(result)
            LOGGER.info("Háttérfeladat befejezve: %s", operation_name)
        except Exception as error:
            LOGGER.exception("Háttérfeladat hibával leállt: %s", operation_name)
            self.failed.emit(str(error))


class SidebarButton(QPushButton):
    def __init__(self, text: str, icon: str) -> None:
        super().__init__(f"  {icon}   {text}")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class OpportunityCardGrid(QScrollArea):
    """Compact card view for item/route opportunities instead of table rows."""

    def __init__(self, icon_loader: ItemIconLoader) -> None:
        super().__init__()
        self.icon_loader = icon_loader
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("opportunityScroll")
        self.content = QWidget()
        self.content.setObjectName("opportunityContent")
        self.layout = QVBoxLayout(self.content)
        self.layout.setContentsMargins(0, 2, 4, 2)
        self.layout.setSpacing(8)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(self.content)

    def set_opportunities(
        self,
        rows: list[tuple],
        include_item: bool = False,
        item_id: str | None = None,
    ) -> None:
        while self.layout.count():
            item = self.layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for row in rows:
            offset = 0
            card = QFrame()
            card.setObjectName("opportunityCard")
            card_layout = QGridLayout(card)
            card_layout.setContentsMargins(12, 8, 12, 8)
            card_layout.setHorizontalSpacing(18)
            icon_id = str(row[0]) if include_item else item_id
            icon = QLabel("IMG")
            icon.setObjectName("opportunityIcon")
            icon.setFixedSize(52, 52)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_layout.addWidget(icon, 0, 0, 2, 1)
            if icon_id:
                icon_ref = weakref.ref(icon)
                self.icon_loader.request_icon(
                    item_icon_url(icon_id, 1, 64),
                    lambda pixmap, target=icon_ref: self._set_icon_ref(target, pixmap),
                )
            if include_item:
                item_id = str(row[0])
                title = QLabel(item_id)
                title.setObjectName("opportunityItem")
                card_layout.addWidget(title, 0, 1, 2, 1)
                offset = 1
            source, destination, quality, buy, sale, fees, profit, roi = row[offset:offset + 8]
            display_column = 2 if include_item else 1
            route = QLabel(f"{source}  ->  {destination}   |   Q{quality}")
            route.setObjectName("opportunityRoute")
            values = QLabel(
                f"Vétel: {buy:,}    Célár: {sale:,}    Díjak: {fees:,}    "
                f"Profit: {profit:,}    ROI: {roi}"
            )
            values.setObjectName("opportunityValues")
            extra = row[offset + 8:]
            if extra:
                values.setText(
                    f"{values.text()}    Adatkor: {extra[0]}"
                    + (f"    Bizalom: {extra[1]}" if len(extra) > 1 else "")
                )
            card_layout.addWidget(route, 0, display_column)
            card_layout.addWidget(values, 1, display_column)
            self.layout.addWidget(card)

    @staticmethod
    def _set_icon(label: QLabel, pixmap) -> None:
        if pixmap is not None:
            label.setText("")
            label.setPixmap(pixmap.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
        else:
            label.setText("Nincs kép")

    @staticmethod
    def _set_icon_ref(label_ref, pixmap) -> None:
        label = label_ref()
        if label is not None:
            OpportunityCardGrid._set_icon(label, pixmap)


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


class ItemPriceWindow(QDialog):
    """Independent city-price window opened from an inventory card."""

    def __init__(
        self,
        item_id: str,
        item_name: str,
        quality: int,
        inventory_quantity: int,
        premium: bool,
        load_quotes: Callable[[str, int, list[str]], list[tuple]],
        parent=None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.item_id = item_id
        self.quality = quality
        self.load_quotes = load_quotes
        self.setWindowTitle(f"Piaci árak · {item_name}")
        self.resize(980, 720)
        self.setMinimumSize(720, 520)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(12)
        title = QLabel(f"{item_name} · Q{quality}")
        title.setObjectName("pageTitle")
        identifier = QLabel(item_id)
        identifier.setObjectName("itemCardId")
        identifier.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(title)
        root.addWidget(identifier)
        scenario_controls = QHBoxLayout()
        self.inventory_caption = QLabel(f"Készleten: {inventory_quantity:,} db")
        self.inventory_caption.setObjectName("inventoryValueStock")
        self.quantity = QSpinBox()
        self.quantity.setRange(0, 2_000_000_000)
        self.quantity.setValue(inventory_quantity)
        self.quantity.setSuffix(" db számolva")
        self.premium = QCheckBox("Prémium adóprofil")
        self.premium.setChecked(premium)
        scenario_controls.addWidget(self.inventory_caption)
        scenario_controls.addStretch()
        scenario_controls.addWidget(QLabel("Kísérleti mennyiség"))
        scenario_controls.addWidget(self.quantity)
        scenario_controls.addWidget(self.premium)
        root.addLayout(scenario_controls)
        self.fee_caption = QLabel()
        self.fee_caption.setObjectName("pageDescription")
        root.addWidget(self.fee_caption)
        self.cities = CitySelector("Megjelenített városok")
        root.addWidget(self.cities)
        self.status = QLabel()
        self.status.setObjectName("pageDescription")
        root.addWidget(self.status)
        self.cards = InventoryValuationGrid()
        root.addWidget(self.cards, 1)
        close = QPushButton("Ablak bezárása")
        close.clicked.connect(self.close)
        root.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(80)
        self._refresh_timer.timeout.connect(self.refresh_quotes)
        for checkbox in self.cities.checkboxes:
            checkbox.toggled.connect(lambda _checked: self._refresh_timer.start())
        self.quantity.valueChanged.connect(self.cards.set_quantity)
        self.premium.toggled.connect(self._premium_changed)
        self._premium_changed(self.premium.isChecked())
        self.refresh_quotes()

    def _premium_changed(self, checked: bool) -> None:
        fees = market_fee_policy(checked)
        self.cards.set_fees(fees)
        self.fee_caption.setText(
            f"Fix adóprofil: tranzakciós adó {fees.transaction_tax_bps / 100:.1f}% · "
            f"sell order setup fee {fees.setup_fee_bps / 100:.1f}%"
        )

    def refresh_quotes(self) -> None:
        cities = self.cities.selected_cities()
        rows = self.load_quotes(self.item_id, self.quality, cities) if cities else []
        self.cards.set_quotes(
            rows,
            self.quantity.value(),
            market_fee_policy(self.premium.isChecked()),
        )
        if not cities:
            self.status.setText("Nincs kijelölt város.")
        elif rows:
            self.status.setText(f"{len(rows)} városi árkártya · helyi market.db adatok")
        else:
            self.status.setText("A kijelölt városokhoz nincs eltárolt piaci adat.")


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
        self.inventory_price_windows: set[ItemPriceWindow] = set()
        self.current_order_id: int | None = None
        self.order_material_editors: list[OrderMaterialEditor] = []
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
        LOGGER.info("Főablak felépítve")

    def closeEvent(self, event) -> None:
        LOGGER.info("Alkalmazásablak bezárása")
        super().closeEvent(event)

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
            ("Inventory", "▦", self.show_inventory),
            ("Orderek", "▣", self.show_orders),
            ("Statisztika", "▥", self.show_statistics),
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
        self.pages.addWidget(self._inventory_page())
        self.pages.addWidget(self._orders_page())
        self.pages.addWidget(self._statistics_page())
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
            "A lezárt Orderek legfontosabb üzleti mutatói és trendjei egyetlen nézetben.",
        )
        controls = QHBoxLayout()
        dashboard_label = QLabel("Idővonal bontása")
        self.dashboard_interval = QComboBox()
        self.dashboard_interval.addItem("Havonta", "month")
        self.dashboard_interval.addItem("Hetente", "week")
        self.dashboard_interval.addItem("Naponta", "day")
        self.dashboard_interval.currentIndexChanged.connect(self.refresh_dashboard)
        controls.addWidget(dashboard_label)
        controls.addWidget(self.dashboard_interval)
        controls.addStretch()
        details = QPushButton("Részletes statisztika  ›")
        details.clicked.connect(self.show_statistics)
        controls.addWidget(details)
        layout.addLayout(controls)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.dashboard_revenue = self._metric_card("Összbevétel", "0 silver")
        self.dashboard_cost = self._metric_card("Teljes költség", "0 silver")
        self.dashboard_profit = self._metric_card("Nettó profit", "0 silver")
        self.dashboard_closed = self._metric_card("Lezárt Orderek", "0")
        for card in (
            self.dashboard_revenue, self.dashboard_cost,
            self.dashboard_profit, self.dashboard_closed,
        ):
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)

        primary_charts = QHBoxLayout()
        primary_charts.setSpacing(12)
        self.dashboard_timeline = TimelineChart()
        self.dashboard_comparison = ComparisonBarChart()
        primary_charts.addWidget(self.dashboard_timeline, 2)
        primary_charts.addWidget(self.dashboard_comparison, 1)
        layout.addLayout(primary_charts)

        rankings = QHBoxLayout()
        rankings.setSpacing(12)
        self.dashboard_items = HorizontalBarChart(
            "Legjobb itemek", "Nettó profit quality szerint"
        )
        self.dashboard_cities = HorizontalBarChart(
            "Legerősebb városok", "Nettó profit eladási város szerint"
        )
        rankings.addWidget(self.dashboard_items, 1)
        rankings.addWidget(self.dashboard_cities, 1)
        layout.addLayout(rankings)

        recent_group = QGroupBox("Legutóbbi lezárt Orderek")
        recent_layout = QVBoxLayout(recent_group)
        self.dashboard_order_cards = StatisticsCardGrid()
        self.dashboard_order_cards.setMinimumHeight(220)
        recent_layout.addWidget(self.dashboard_order_cards)
        layout.addWidget(recent_group)
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
        actions.setSpacing(8)
        actions.addWidget(
            self._detail_action_card(
                "Piaci árak",
                "Városonkénti vételi és eladási árak",
                self.show_prices,
            )
        )
        actions.addWidget(
            self._detail_action_card(
                "Item flipjei",
                "Legjobb városközi lehetőségek",
                self.show_item_flips,
            )
        )
        actions.addWidget(
            self._detail_action_card(
                "14 napos előzmény",
                "Ártrendek frissítése",
                self.refresh_selected_history,
            )
        )
        actions.addStretch()
        detail_layout.addLayout(actions)
        prices_title = QLabel("Piaci adatok")
        prices_title.setObjectName("sectionTitle")
        detail_layout.addWidget(prices_title)
        self.item_detail_results = QStackedWidget()
        self.price_cards = MarketCityCardGrid(self.item_icon_loader)
        self.item_table = OpportunityCardGrid(self.item_icon_loader)
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

    @staticmethod
    def _detail_action_card(
        title: str,
        description: str,
        callback: Callable[[], None],
    ) -> QToolButton:
        card = QToolButton()
        card.setObjectName("detailActionCard")
        card.setText(f"{title}\n{description}")
        card.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card.setMinimumHeight(58)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.clicked.connect(callback)
        return card

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
        self.global_table = OpportunityCardGrid(self.item_icon_loader)
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
        self.craft_batches = QSpinBox()
        self.craft_batches.setRange(1, 1_000_000)
        self.craft_batches.setValue(1)
        self.craft_batches.setSuffix(" craft kör")
        craft_search_controls = QGridLayout()
        for column, label in enumerate(("Keresés", "Kategória", "Tier", "Enchant", "Quality")):
            craft_search_controls.addWidget(QLabel(label), 0, column)
        for column, widget in enumerate((self.craft_search, self.craft_category, self.craft_tier, self.craft_enchantment, self.craft_quality)):
            craft_search_controls.addWidget(widget, 1, column)
        setup_layout.addLayout(craft_search_controls)
        form.addRow("Forrás", self.craft_source)
        form.addRow("", self.premium)
        form.addRow("Crafting állomásdíj", self.crafting_price)
        form.addRow("Mennyiség", self.craft_batches)
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
        self.craft_materials = CraftingMaterialCardGrid(self.item_icon_loader)
        self.craft_materials.setMinimumHeight(150)
        self.craft_source.currentIndexChanged.connect(self.show_recipe_materials)
        self.craft_batches.valueChanged.connect(self.show_recipe_materials)
        materials_layout.addWidget(self.craft_materials)
        craft_detail_layout.addWidget(materials_group)

        tree_group = QGroupBox("3 · Rekurzív crafting függőségek")
        tree_layout = QVBoxLayout(tree_group)
        tree_hint = QLabel(
            "A vonalak a receptből a szükséges összetevők felé vezetnek. "
            "A nyers vagy nem craftolható levelek is megjelennek; ciklus esetén a bontás leáll."
        )
        tree_hint.setObjectName("notice")
        tree_hint.setWordWrap(True)
        tree_layout.addWidget(tree_hint)
        self.craft_dependency_tree = CraftingDependencyTreeView(self.item_icon_loader)
        tree_layout.addWidget(self.craft_dependency_tree, 1)
        craft_detail_layout.addWidget(tree_group, 2)

        result_group = QGroupBox("4 · Nettó crafting eredmény")
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
        self.refining_category = CategoryPopupButton()
        self.refining_tier = QSpinBox()
        self.refining_tier.setRange(1, 8)
        self.refining_tier.setValue(4)
        self.refining_enchantment = QComboBox()
        self.refining_enchantment.addItem("Mind", -1)
        for enchantment in range(5):
            self.refining_enchantment.addItem(f".{enchantment}", enchantment)
        self.refining_quality = QComboBox()
        self.refining_quality.addItem("Mind", 0)
        for quality, label in QUALITY_LABELS.items():
            self.refining_quality.addItem(f"{quality} · {label}", quality)
        self.refining_recipe = QComboBox()
        self.refining_recipe.setMinimumWidth(330)
        self.refining_recipe.currentIndexChanged.connect(self._load_refining_profile)
        self.refining_recipe_cards = RefiningRecipeCardGrid(self.item_icon_loader)
        self.refining_recipe_cards.selected.connect(self._select_refining_recipe_card)
        self.refining_amount = QSpinBox()
        self.refining_amount.setRange(1, 1_000_000)
        self.refining_amount.setValue(1)
        self.refining_amount.setSuffix(" batch")
        search = QPushButton("Receptek keresése")
        search.clicked.connect(self.search_refining_recipes)
        import_button = QPushButton("Recipes importja")
        import_button.clicked.connect(self.import_refining_data)
        for column, label in enumerate(("Item keresése", "Kategória", "Tier", "Enchant", "Quality")):
            setup_layout.addWidget(QLabel(label), 0, column)
        setup_layout.addWidget(self.refining_search, 1, 0)
        setup_layout.addWidget(self.refining_category, 1, 1)
        setup_layout.addWidget(self.refining_tier, 1, 2)
        setup_layout.addWidget(self.refining_enchantment, 1, 3)
        setup_layout.addWidget(self.refining_quality, 1, 4)
        amount_row = QHBoxLayout()
        amount_row.addWidget(QLabel("Számolt mennyiség"))
        amount_row.addWidget(self.refining_amount)
        amount_row.addStretch()
        amount_row.addWidget(search)
        amount_row.addWidget(import_button)
        setup_layout.addLayout(amount_row, 2, 0, 1, 5)
        setup_layout.setColumnStretch(0, 2)
        setup_layout.setColumnStretch(1, 1)
        layout.addWidget(setup)
        recipe_heading = QLabel("Válassz refining receptet")
        recipe_heading.setObjectName("sectionTitle")
        layout.addWidget(recipe_heading)
        layout.addWidget(self.refining_recipe_cards)
        self.refining_recipe.hide()

        route = QGroupBox("2 · Városok és refining profil")
        route_layout = QGridLayout(route)
        route_layout.setSpacing(12)
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

        cities_card = QFrame()
        cities_card.setObjectName("refiningProfileCard")
        cities_form = QFormLayout(cities_card)
        cities_form.addRow(self._profile_card_title("Útvonal"))
        cities_form.addRow("Vétel városa", self.refining_buy_city)
        cities_form.addRow("Refine város", self.refining_city)
        cities_form.addRow("Eladás városa", self.refining_sell_city)

        returns_card = QFrame()
        returns_card.setObjectName("refiningProfileCard")
        returns_form = QFormLayout(returns_card)
        returns_form.addRow(self._profile_card_title("Visszatérítés és focus"))
        returns_form.addRow("Alap RRR", self.refining_base_rrr)
        returns_form.addRow("Focus RRR", self.refining_focus_rrr)
        returns_form.addRow("", self.refining_focus)
        returns_form.addRow("", self.refining_premium)

        costs_card = QFrame()
        costs_card.setObjectName("refiningProfileCard")
        costs_form = QFormLayout(costs_card)
        costs_form.addRow(self._profile_card_title("Költségprofil"))
        costs_form.addRow("Station fee", self.refining_station_fee)
        costs_form.addRow("Transport", self.refining_transport_fee)
        costs_form.addRow("", save_profile)

        route_layout.addWidget(cities_card, 0, 0)
        route_layout.addWidget(returns_card, 0, 1)
        route_layout.addWidget(costs_card, 1, 0, 1, 2)
        route_layout.setColumnStretch(0, 1)
        route_layout.setColumnStretch(1, 1)
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
        self.load_categories()
        self.refining_category.selection_changed.connect(self.search_refining_recipes)
        self.search_refining_recipes()
        return page

    def _inventory_page(self) -> QWidget:
        page, layout = self._page(
            "Inventory",
            "Személyes készlet darabban és 999-es stackekben. A crafting kalkulátor ebből fedezi először az alapanyagokat.",
        )
        toolbar = QHBoxLayout()
        add_item = QPushButton("＋ Item hozzáadása")
        add_item.clicked.connect(self.open_inventory_picker)
        self.inventory_filter = QLineEdit()
        self.inventory_filter.setPlaceholderText("Szűrés a saját készletben…")
        self.inventory_filter.textChanged.connect(self.refresh_inventory)
        toolbar.addWidget(add_item)
        toolbar.addWidget(self.inventory_filter, 1)
        layout.addLayout(toolbar)
        hint = QLabel(
            "A mennyiséget közvetlenül a kártyán módosíthatod. A változás a Mentés gombbal kerül az adatbázisba."
        )
        hint.setObjectName("notice")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.inventory_cards = InventoryCardGrid(self.item_icon_loader)
        self.inventory_cards.save_requested.connect(self.save_inventory_card)
        self.inventory_cards.remove_requested.connect(self.delete_inventory_card)
        self.inventory_cards.price_requested.connect(self.open_inventory_price_window)
        layout.addWidget(self.inventory_cards, 1)
        self.refresh_inventory()
        return page

    def _orders_page(self) -> QWidget:
        page, layout = self._page(
            "Orderek",
            "Névre mentett kereskedelmi projektek. A készlet csak az aktív Order lezárásakor csökken.",
        )
        toolbar = QHBoxLayout()
        new_button = QPushButton("＋ Új Order")
        new_button.clicked.connect(self.new_order)
        self.order_status_filter = QComboBox()
        self.order_status_filter.addItem("Minden állapot", None)
        self.order_status_filter.addItem("Tervezett", "draft")
        self.order_status_filter.addItem("Aktív", "active")
        self.order_status_filter.addItem("Lezárt", "closed")
        self.order_status_filter.currentIndexChanged.connect(self.refresh_orders)
        toolbar.addWidget(new_button)
        toolbar.addStretch()
        toolbar.addWidget(QLabel("Szűrés"))
        toolbar.addWidget(self.order_status_filter)
        layout.addLayout(toolbar)

        self.order_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.order_splitter.setObjectName("masterDetailSplitter")
        self.order_splitter.setChildrenCollapsible(False)
        self.order_cards = TradeOrderCardList(self.item_icon_loader)
        self.order_cards.selected.connect(self.load_order)
        self.order_cards.setMinimumWidth(330)
        self.order_splitter.addWidget(self.order_cards)

        self.order_editor = QFrame()
        self.order_editor.setObjectName("detailPanel")
        editor = QVBoxLayout(self.order_editor)
        editor.setContentsMargins(18, 16, 18, 16)
        editor.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("Order szerkesztése")
        title.setObjectName("sectionTitle")
        self.order_state_label = QLabel("Új · tervezett")
        self.order_state_label.setObjectName("orderEditorStatus")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.order_state_label)
        editor.addLayout(header)

        identity = QGroupBox("Projekt és eladás")
        form = QGridLayout(identity)
        self.order_name = QLineEdit()
        self.order_name.setPlaceholderText("Például: T4 táska – Caerleon")
        self.order_output_label = QLabel("Nincs kiválasztott eladási item")
        self.order_output_label.setObjectName("orderOutputSelection")
        choose_output = QPushButton("Kiválasztás")
        choose_output.setToolTip("Eladási item kiválasztása")
        choose_output.clicked.connect(lambda: self.open_order_item_picker("output"))
        self.order_choose_output = choose_output
        self.order_output_id = ""
        self.order_output_quality = 1
        self.order_output_quantity = QSpinBox()
        self.order_output_quantity.setRange(1, 2_000_000_000)
        self.order_output_quantity.setValue(1)
        self.order_output_quantity.setSuffix(" db")
        self.order_sold_quantity = QSpinBox()
        self.order_sold_quantity.setRange(0, 2_000_000_000)
        self.order_sold_quantity.setSuffix(" db eladva")
        self.order_planned_price = QSpinBox()
        self.order_planned_price.setRange(0, 2_000_000_000)
        self.order_planned_price.setSuffix(" silver/db")
        self.order_actual_price = QSpinBox()
        self.order_actual_price.setRange(0, 2_000_000_000)
        self.order_actual_price.setSpecialValueText("Még nincs")
        self.order_actual_price.setSuffix(" silver/db")
        self.order_sale_city = QComboBox()
        self.order_sale_city.addItems(CITIES)
        self.order_sale_method = QComboBox()
        self.order_sale_method.addItem("Sell order", "sell_order")
        self.order_sale_method.addItem("Azonnali eladás", "instant")
        self.order_premium = QCheckBox("Prémium díjszabás")
        self.order_premium.setChecked(True)
        form.addWidget(QLabel("Order neve"), 0, 0)
        form.addWidget(self.order_name, 1, 0, 1, 4)
        form.addWidget(QLabel("Eladási item"), 2, 0)
        form.addWidget(self.order_output_label, 3, 0, 1, 3)
        form.addWidget(choose_output, 3, 3)
        form.addWidget(QLabel("Teljes mennyiség"), 4, 0)
        form.addWidget(self.order_output_quantity, 5, 0, 1, 2)
        form.addWidget(QLabel("Eladott mennyiség"), 4, 2)
        form.addWidget(self.order_sold_quantity, 5, 2, 1, 2)
        form.addWidget(QLabel("Tervezett darabár"), 6, 0)
        form.addWidget(self.order_planned_price, 7, 0, 1, 2)
        form.addWidget(QLabel("Tényleges darabár"), 6, 2)
        form.addWidget(self.order_actual_price, 7, 2, 1, 2)
        form.addWidget(QLabel("Eladás városa"), 8, 0)
        form.addWidget(self.order_sale_city, 9, 0, 1, 2)
        form.addWidget(QLabel("Eladási mód"), 8, 2)
        form.addWidget(self.order_sale_method, 9, 2, 1, 2)
        form.addWidget(self.order_premium, 10, 0, 1, 4)
        for column in range(4):
            form.setColumnStretch(column, 1)
        editor.addWidget(identity)

        material_header = QHBoxLayout()
        material_title = QLabel("Alapanyagok")
        material_title.setObjectName("sectionTitle")
        add_material = QPushButton("＋ Alapanyag")
        add_material.clicked.connect(lambda: self.open_order_item_picker("material"))
        self.order_add_material = add_material
        material_header.addWidget(material_title)
        material_header.addStretch()
        material_header.addWidget(add_material)
        editor.addLayout(material_header)
        material_scroll = QScrollArea()
        material_scroll.setWidgetResizable(True)
        material_scroll.setFrameShape(QFrame.Shape.NoFrame)
        material_scroll.setMinimumHeight(190)
        self.order_material_content = QWidget()
        self.order_material_layout = QVBoxLayout(self.order_material_content)
        self.order_material_layout.setContentsMargins(0, 0, 4, 0)
        self.order_material_layout.setSpacing(7)
        self.order_material_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        material_scroll.setWidget(self.order_material_content)
        editor.addWidget(material_scroll, 1)

        actions = QHBoxLayout()
        self.order_save_button = QPushButton("Mentés")
        self.order_save_button.clicked.connect(lambda: self.save_order())
        self.order_activate_button = QPushButton("Aktiválás")
        self.order_activate_button.clicked.connect(self.activate_current_order)
        self.order_close_button = QPushButton("Order lezárása")
        self.order_close_button.setToolTip("Lezárás és inventory-készlet levonása")
        self.order_close_button.clicked.connect(self.close_current_order)
        self.order_delete_button = QPushButton("Törlés")
        self.order_delete_button.setObjectName("dangerButton")
        self.order_delete_button.clicked.connect(self.delete_current_order)
        actions.addWidget(self.order_save_button)
        actions.addWidget(self.order_activate_button)
        actions.addWidget(self.order_close_button)
        actions.addStretch()
        actions.addWidget(self.order_delete_button)
        editor.addLayout(actions)
        self.order_splitter.addWidget(self.order_editor)
        self.order_splitter.setSizes([390, 780])
        self.order_splitter.setStretchFactor(0, 1)
        self.order_splitter.setStretchFactor(1, 2)
        self.order_splitter.splitterMoved.connect(
            lambda *_: self._schedule_splitter_save("orders", self.order_splitter)
        )
        layout.addWidget(self.order_splitter, 1)
        self.new_order()
        self.refresh_orders()
        return page

    def _statistics_page(self) -> QWidget:
        page, layout = self._page(
            "Statisztika",
            "A lezárt Orderek tényleges bevétele, új készpénzes költsége és nettó profitja.",
        )
        filters = QHBoxLayout()
        self.statistics_month = QComboBox()
        self.statistics_month.currentIndexChanged.connect(self.refresh_statistics)
        self.statistics_group = QComboBox()
        self.statistics_group.addItem("Orderenként", "order")
        self.statistics_group.addItem("Itemenként", "item")
        self.statistics_group.addItem("Városonként", "city")
        self.statistics_group.currentIndexChanged.connect(self.refresh_statistics)
        self.statistics_interval = QComboBox()
        self.statistics_interval.addItem("Havi grafikon", "month")
        self.statistics_interval.addItem("Heti grafikon", "week")
        self.statistics_interval.addItem("Napi grafikon", "day")
        self.statistics_interval.currentIndexChanged.connect(self.refresh_statistics)
        filters.addWidget(QLabel("Hónap"))
        filters.addWidget(self.statistics_month)
        filters.addWidget(QLabel("Csoportosítás"))
        filters.addWidget(self.statistics_group)
        filters.addWidget(QLabel("Idővonal"))
        filters.addWidget(self.statistics_interval)
        filters.addStretch()
        layout.addLayout(filters)
        metrics = QHBoxLayout()
        self.statistics_revenue = self._metric_card("Bruttó bevétel", "0")
        self.statistics_cost = self._metric_card("Teljes költség", "0")
        self.statistics_profit = self._metric_card("Nettó profit", "0")
        metrics.addWidget(self.statistics_revenue)
        metrics.addWidget(self.statistics_cost)
        metrics.addWidget(self.statistics_profit)
        layout.addLayout(metrics)
        chart_row = QHBoxLayout()
        chart_row.setSpacing(12)
        self.statistics_timeline = TimelineChart()
        self.statistics_comparison = ComparisonBarChart()
        chart_row.addWidget(self.statistics_timeline, 2)
        chart_row.addWidget(self.statistics_comparison, 1)
        layout.addLayout(chart_row)
        ranking_row = QHBoxLayout()
        ranking_row.setSpacing(12)
        self.statistics_items = HorizontalBarChart(
            "Itemek profitja", "A kiválasztott időszak legerősebb itemei"
        )
        self.statistics_cities = HorizontalBarChart(
            "Városok profitja", "A kiválasztott időszak eladási városai"
        )
        ranking_row.addWidget(self.statistics_items, 1)
        ranking_row.addWidget(self.statistics_cities, 1)
        layout.addLayout(ranking_row)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        inventory_box = QGroupBox("Aktuális inventory")
        inventory_layout = QVBoxLayout(inventory_box)
        self.statistics_inventory = CompactInventoryList()
        inventory_layout.addWidget(self.statistics_inventory)
        results_box = QGroupBox("Lezárt Orderek")
        results_layout = QVBoxLayout(results_box)
        self.statistics_cards = StatisticsCardGrid()
        results_layout.addWidget(self.statistics_cards)
        splitter.addWidget(inventory_box)
        splitter.addWidget(results_box)
        splitter.setSizes([390, 780])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self.refresh_statistics(reload_months=True)
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
        self.favorite_tables: dict[str, ItemCardGrid] = {}
        for context, label in (
            ("price", "Item-ár"),
            ("crafting", "Crafting"),
            ("flip", "Market flip"),
        ):
            table = ItemCardGrid(self.item_icon_loader, compact=True)
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
        has_items = ITEM_CATALOG_FILE.exists()
        has_full_catalog_source = has_items and LOCALIZATION_FILE.exists()
        rebuild.setEnabled(has_full_catalog_source)
        craft_import.setEnabled(has_items)
        category_import.setEnabled(has_items)
        if not has_full_catalog_source:
            rebuild.setToolTip(
                "A telepítőben nincs items.json/localization.json; a beépített katalógus használható."
            )
        if not has_items:
            craft_import.setToolTip("Az importhoz items.json szükséges.")
            category_import.setToolTip("A frissítéshez items.json szükséges.")
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
        diagnostics_group = QGroupBox("Hibajelentés és naplók")
        diagnostics_layout = QVBoxLayout(diagnostics_group)
        diagnostics_help = QLabel(
            "Ha hibát tapasztalsz, ments egy diagnosztikai ZIP-et, és küldd el a "
            "fejlesztőnek. A csomag naplókat és műszaki adatokat tartalmaz, "
            "személyes adatbázisokat nem."
        )
        diagnostics_help.setWordWrap(True)
        diagnostics_layout.addWidget(diagnostics_help)
        diagnostics_buttons = QHBoxLayout()
        log_folder = QPushButton("Log mappa megnyitása")
        log_folder.clicked.connect(self.open_log_folder)
        diagnostics = QPushButton("Diagnosztikai ZIP mentése")
        diagnostics.clicked.connect(self.export_diagnostics)
        diagnostics_buttons.addWidget(log_folder)
        diagnostics_buttons.addWidget(diagnostics)
        diagnostics_buttons.addStretch()
        diagnostics_layout.addLayout(diagnostics_buttons)
        layout.addWidget(diagnostics_group)
        layout.addStretch()
        return page

    @staticmethod
    def _metric_card(label: str, value: str) -> QFrame:
        card = QFrame()
        card.setObjectName("metricCard")
        layout = QVBoxLayout(card)
        caption = QLabel(label)
        caption.setObjectName("metricLabel")
        caption.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        number = QLabel(value)
        number.setObjectName("metricValue")
        number.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        card.value_label = number
        layout.addWidget(caption)
        layout.addWidget(number)
        return card

    @staticmethod
    def _profile_card_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("profileCardTitle")
        return label

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
            #detailActionCard {
                background: #16232d; border: 1px solid #2d4655; border-radius: 8px;
                color: #dce7ed; padding: 8px 12px; text-align: left;
                font-weight: 650;
            }
            #detailActionCard:hover {
                background: #223743; border-color: #d99a31; color: #ffffff;
            }
            #detailActionCard:pressed { background: #735321; color: #ffffff; }
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
            #itemCard[compact="true"] {
                border-radius: 8px; min-height: 76px; max-height: 86px;
            }
            #itemCard[compact="true"] #itemCardTitle { font-size: 12px; }
            #itemCard[compact="true"] #itemCardMeta { font-size: 10px; }
            #itemCard[compact="true"] #itemCardId { font-size: 9px; }
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
            #inventoryScroll, #inventoryContent { background: transparent; border: 0; }
            #inventoryCard {
                background: #151f28; border: 1px solid #293d49; border-radius: 11px;
            }
            #inventoryCard:hover { background: #192731; border-color: #7d6235; }
            #inventoryIcon {
                background: #0c141a; border: 1px solid #2d414d; border-radius: 9px;
                color: #7f929f; font-weight: 700;
            }
            #inventoryTitle { color: #f3f6f8; font-size: 14px; font-weight: 750; }
            #inventoryMeta, #inventoryTotal { color: #d9ad5d; font-weight: 650; }
            #inventoryId, #inventoryUpdated {
                color: #718592; font-family: Consolas; font-size: 10px;
            }
            #inventoryRemove {
                background: #342126; border: 1px solid #68404a;
                border-radius: 14px; padding: 0; font-size: 13px; font-weight: 800;
            }
            #inventoryRemove:hover { background: #4a2931; border-color: #b85e70; }
            #inventorySelection { color: #d9ad5d; font-weight: 650; }
            #inventoryCategorySection, #inventoryCategoryBody { background: transparent; border: 0; }
            #inventoryCategoryToggle {
                background: #17242e; border: 1px solid #2b414e; border-radius: 8px;
                color: #e7edf3; padding: 9px 12px; text-align: left; font-weight: 750;
            }
            #inventoryCategoryToggle:hover { background: #20333f; border-color: #8b6a31; }
            #inventoryEmpty { color: #8194a1; padding: 50px; font-size: 14px; }
            #inventoryValueScroll, #inventoryValueContent { background: transparent; border: 0; }
            #inventoryValueCard {
                background: #151f28; border: 1px solid #2c4351; border-radius: 10px;
            }
            #inventoryValueCity { color: #f3f6f8; font-size: 15px; font-weight: 800; }
            #inventoryValueStock {
                color: #f2b84b; background: #2b2418; border: 1px solid #725a2d;
                border-radius: 10px; padding: 4px 9px; font-weight: 750;
            }
            #inventoryValueMarketPrice { color: #d9ad5d; font-weight: 700; }
            #inventoryValueInstant, #inventoryValueScenario {
                background: #101920; border: 1px solid #273b47; border-radius: 7px;
            }
            #inventoryValueCaption { color: #8fa1ae; font-weight: 650; }
            #inventoryValueResult { color: #70d3a2; font-size: 14px; font-weight: 800; }
            #opportunityScroll, #opportunityContent { background: transparent; border: 0; }
            #opportunityCard {
                background: #151f28; border: 1px solid #293d49; border-radius: 9px;
            }
            #opportunityCard:hover { background: #1a2832; border-color: #8b6a31; }
            #opportunityItem { color: #f2b84b; font-weight: 750; min-width: 180px; }
            #opportunityRoute { color: #dce7ed; font-weight: 650; }
            #opportunityValues { color: #9fb0ba; }
            #recipeCardScroll, #recipeCardContent { background: transparent; border: 0; }
            #recipeCard {
                background: #151f28; border: 1px solid #293d49; border-radius: 8px;
                color: #dce7ed; padding: 12px; text-align: left;
                font-size: 12px; font-weight: 650;
            }
            #recipeCard:hover { background: #1a2832; border-color: #8b6a31; }
            #recipeCard:checked { background: #292419; border: 2px solid #d99a31; color: #ffffff; }
            #refiningRecipeScroll, #refiningRecipeContent { background: transparent; border: 0; }
            #refiningRecipeCard {
                background: #151f28; border: 1px solid #293d49; border-radius: 10px;
            }
            #refiningRecipeCard:hover { background: #1a2832; border-color: #8b6a31; }
            #refiningRecipeCard[selected="true"] {
                background: #292419; border: 2px solid #d99a31;
            }
            #refiningRecipeIcon {
                background: #0c141a; border: 1px solid #2d414d; border-radius: 9px;
                color: #7f929f; font-weight: 750;
            }
            #refiningRecipeTitle { color: #f3f6f8; font-size: 14px; font-weight: 800; }
            #refiningRecipeMeta { color: #d9ad5d; font-size: 11px; font-weight: 650; }
            #refiningRecipeId { color: #718592; font-family: Consolas; font-size: 9px; }
            #refiningRecipeBadge {
                color: #f2b84b; background: #2b2418; border: 1px solid #725a2d;
                border-radius: 9px; padding: 3px 7px; font-size: 10px; font-weight: 750;
            }
            #refiningProfileCard {
                background: #151f28; border: 1px solid #293d49;
                border-radius: 10px; padding: 10px;
            }
            #profileCardTitle {
                color: #f2b84b; font-size: 15px; font-weight: 800;
                padding-bottom: 6px;
            }
            #craftMaterialScroll, #craftMaterialContent {
                background: transparent; border: 0;
            }
            #craftMaterialCard {
                background: #151f28; border: 1px solid #293d49; border-radius: 8px;
            }
            #craftMaterialCard:hover {
                background: #1a2832; border-color: #8b6a31;
            }
            #craftMaterialIcon, #craftingNodeIcon {
                background: #0c141a; border: 1px solid #2d414d; border-radius: 7px;
                color: #7f929f; font-size: 10px; font-weight: 700;
            }
            #craftMaterialTitle, #craftingNodeTitle {
                color: #f2b84b; font-weight: 750;
            }
            #craftMaterialMeta, #craftingNodeQuantity {
                color: #dce7ed; font-size: 11px; font-weight: 650;
            }
            #craftMaterialId, #craftingNodeId {
                color: #718592; font-family: Consolas; font-size: 9px;
            }
            #craftMaterialCard QLabel {
                color: #9fb0ba; font-size: 10px;
            }
            #craftingTreeView {
                background: #0d141b; border: 1px solid #263b49; border-radius: 8px;
            }
            #craftingNodeCard {
                background: #151f28; border: 1px solid #2b414e; border-radius: 10px;
            }
            #craftingNodeCard[raw="true"] {
                background: #17292a; border-color: #35615a;
            }
            #craftingNodeCard[cycle="true"] {
                background: #342224; border-color: #934e4e;
            }
            #craftingNodeStatus, #craftingNodePrice {
                color: #9fb0ba; font-size: 10px;
            }
            #craftingNodeCard[raw="true"] #craftingNodeStatus { color: #76c4a1; }
            #craftingNodeCard[cycle="true"] #craftingNodeStatus { color: #ef8585; }
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
            #tradeOrderScroll { background: transparent; border: 0; }
            #tradeOrderCard, #orderMaterialCard, #statisticsOrderCard,
            #statisticsInventoryCard {
                background: #151f28; border: 1px solid #2b414e; border-radius: 10px;
            }
            #tradeOrderCard:hover { background: #1b2a34; border-color: #8b6a31; }
            #tradeOrderCard[selected="true"] {
                background: #292419; border: 2px solid #d99a31;
            }
            #tradeOrderIcon {
                background: #0c141a; border: 1px solid #2d414d; border-radius: 8px;
                color: #718592; font-weight: 700;
            }
            #tradeOrderTitle, #orderMaterialTitle, #statisticsCardTitle,
            #statisticsInventoryTitle { color: #f3f6f8; font-weight: 800; }
            #tradeOrderMeta, #orderMaterialId { color: #8093a1; font-size: 10px; }
            #tradeOrderAmount, #statisticsInventoryAmount {
                color: #d9ad5d; font-weight: 750;
            }
            #tradeOrderStatus, #orderEditorStatus {
                background: #243541; color: #b9c9d3; border-radius: 8px;
                padding: 3px 8px; font-size: 10px; font-weight: 750;
            }
            #tradeOrderStatus[status="active"] { background: #173b2b; color: #70d3a2; }
            #tradeOrderStatus[status="closed"] { background: #2b2418; color: #f2b84b; }
            #orderOutputSelection { color: #d9ad5d; font-weight: 700; }
            #orderMaterialRemove {
                background: #342126; border: 1px solid #68404a; border-radius: 15px; padding: 4px;
            }
            #orderMaterialRemove:hover, #dangerButton:hover {
                background: #4a2931; border-color: #b85e70;
            }
            #dangerButton { background: #342126; border-color: #68404a; }
            #statisticsOrderCard { padding: 5px; }
            #statisticsProfit { color: #70d3a2; font-size: 15px; font-weight: 800; }
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
        LOGGER.debug("Oldalváltás: index=%s", index)

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

    def show_inventory(self) -> None:
        self._set_page(5)
        self.refresh_inventory()

    def show_orders(self) -> None:
        self._set_page(6)
        total_width = self.order_splitter.width()
        if total_width > 0:
            target = self._splitter_target("orders", total_width, 6700)
            self.order_splitter.setSizes([max(300, total_width - target), target])
        self.refresh_orders()

    def show_statistics(self) -> None:
        self._set_page(7)
        self.refresh_statistics(reload_months=True)

    def show_favorites(self) -> None:
        self._set_page(8)
        self.refresh_favorites()

    def show_database(self) -> None:
        self._set_page(9)
        self.refresh_dashboard()

    def new_order(self) -> None:
        self.current_order_id = None
        self.order_name.clear()
        self.order_output_id = ""
        self.order_output_quality = 1
        self.order_output_label.setText("Nincs kiválasztott eladási item")
        self.order_output_quantity.setValue(1)
        self.order_sold_quantity.setValue(0)
        self.order_planned_price.setValue(0)
        self.order_actual_price.setValue(0)
        self.order_sale_city.setCurrentIndex(0)
        self.order_sale_method.setCurrentIndex(0)
        self.order_premium.setChecked(True)
        self._clear_order_materials()
        self._set_order_editor_state("draft", is_new=True)

    def _clear_order_materials(self) -> None:
        for material in self.order_material_editors:
            self.order_material_layout.removeWidget(material)
            material.deleteLater()
        self.order_material_editors.clear()

    def _add_order_material(
        self,
        item_id: str,
        name: str,
        quality: int,
        purchased: int = 0,
        inventory: int = 0,
        unit_price: int = 0,
        city: str = "Caerleon",
    ) -> None:
        material = OrderMaterialEditor(
            item_id, name, quality, purchased, inventory, unit_price, city
        )
        material.remove_requested.connect(self._remove_order_material)
        self.order_material_editors.append(material)
        self.order_material_layout.addWidget(material)

    def _remove_order_material(self, material: OrderMaterialEditor) -> None:
        if material not in self.order_material_editors:
            return
        self.order_material_editors.remove(material)
        self.order_material_layout.removeWidget(material)
        material.deleteLater()

    def open_order_item_picker(self, target: str) -> None:
        category_rows, counts = self._category_data()
        dialog = InventoryPickerDialog(
            self.item_icon_loader,
            self._inventory_picker_items,
            category_rows,
            counts,
            self,
            show_quantity=False,
            action_label="Kiválasztás",
            window_title=(
                "Eladási item kiválasztása" if target == "output"
                else "Alapanyag kiválasztása"
            ),
        )
        if not dialog.exec() or dialog.selected_row is None:
            return
        item_id = str(dialog.selected_row[0])
        name = str(dialog.selected_row[1] or item_id)
        quality = dialog.selected_quality()
        if target == "output":
            self.order_output_id = item_id
            self.order_output_quality = quality
            self.order_output_label.setText(f"{name} · {item_id} · Q{quality}")
        else:
            self._add_order_material(item_id, name, quality)

    def _order_input(self) -> TradeOrderInput:
        materials = tuple(
            OrderMaterialInput(
                item_uniquename=widget.item_id,
                quality=widget.quality,
                purchased_quantity=widget.purchased.value(),
                inventory_quantity=widget.inventory.value(),
                purchased_unit_price=widget.unit_price.value(),
                purchase_city=widget.city.currentText(),
            )
            for widget in self.order_material_editors
        )
        actual = self.order_actual_price.value()
        return TradeOrderInput(
            name=self.order_name.text().strip(),
            output_item_uniquename=self.order_output_id,
            output_quality=self.order_output_quality,
            output_quantity=self.order_output_quantity.value(),
            sold_quantity=self.order_sold_quantity.value(),
            planned_unit_price=self.order_planned_price.value(),
            actual_unit_price=actual if actual > 0 else None,
            sale_city=self.order_sale_city.currentText(),
            sale_method=str(self.order_sale_method.currentData()),
            premium=self.order_premium.isChecked(),
            materials=materials,
        )

    def save_order(self, *, quiet: bool = False) -> bool:
        try:
            value = self._order_input()
            with open_db() as conn:
                if self.current_order_id is None:
                    self.current_order_id = create_order(conn, value)
                else:
                    update_order(conn, self.current_order_id, value)
            self.refresh_orders()
            self.load_order(self.current_order_id)
            if not quiet:
                self.statusBar().showMessage("Order mentve.")
            return True
        except (ValueError, sqlite3.Error) as error:
            LOGGER.warning("Order nem menthető", exc_info=True)
            QMessageBox.warning(self, "Order nem menthető", str(error))
            return False

    def refresh_orders(self, _value=0) -> None:
        if not hasattr(self, "order_cards"):
            return
        status = self.order_status_filter.currentData()
        with open_db() as conn:
            rows = list_orders(conn, status)
        self.order_cards.set_orders(list(rows), self.current_order_id)

    def load_order(self, order_id: int) -> None:
        try:
            with open_db() as conn:
                order, materials = get_order(conn, order_id)
        except (ValueError, sqlite3.Error) as error:
            LOGGER.warning("Order nem tölthető be | id=%s", order_id, exc_info=True)
            QMessageBox.warning(self, "Order betöltési hiba", str(error))
            return
        self.current_order_id = int(order[0])
        self.order_name.setText(str(order[1]))
        self.order_output_id = str(order[3])
        self.order_output_quality = int(order[4])
        with open_db() as conn:
            name_row = conn.execute(
                "SELECT COALESCE(name_en,uniquename) FROM items WHERE uniquename=?",
                (self.order_output_id,),
            ).fetchone()
        output_name = str(name_row[0]) if name_row else self.order_output_id
        self.order_output_label.setText(
            f"{output_name} · {self.order_output_id} · Q{self.order_output_quality}"
        )
        self.order_output_quantity.setValue(int(order[5]))
        self.order_sold_quantity.setValue(int(order[6]))
        self.order_planned_price.setValue(int(order[7]))
        self.order_actual_price.setValue(int(order[8] or 0))
        self.order_sale_city.setCurrentText(str(order[9]))
        method_index = self.order_sale_method.findData(str(order[10]))
        self.order_sale_method.setCurrentIndex(max(0, method_index))
        self.order_premium.setChecked(bool(order[11]))
        self._clear_order_materials()
        for _id, item_id, name, quality, purchased, inventory, price, city in materials:
            self._add_order_material(
                str(item_id), str(name), int(quality), int(purchased),
                int(inventory), int(price), str(city),
            )
        self._set_order_editor_state(str(order[2]))
        self.refresh_orders()

    def _set_order_editor_state(self, status: str, *, is_new: bool = False) -> None:
        labels = {"draft": "Tervezett", "active": "Aktív", "closed": "Lezárt"}
        self.order_state_label.setText(
            ("Új · " if is_new else "") + labels.get(status, status)
        )
        closed = status == "closed"
        for widget in (
            self.order_name, self.order_output_quantity, self.order_sold_quantity,
            self.order_planned_price, self.order_actual_price, self.order_sale_city,
            self.order_sale_method, self.order_premium, self.order_choose_output,
            self.order_add_material,
        ):
            widget.setEnabled(not closed)
        for material in self.order_material_editors:
            material.set_read_only(closed)
        self.order_save_button.setEnabled(not closed)
        self.order_activate_button.setEnabled(status == "draft" and not is_new)
        self.order_close_button.setEnabled(status == "active")
        self.order_delete_button.setEnabled(not closed and not is_new)

    def activate_current_order(self) -> None:
        if self.current_order_id is None or not self.save_order(quiet=True):
            return
        try:
            with open_db() as conn:
                activate_order(conn, self.current_order_id)
            self.load_order(self.current_order_id)
            self.statusBar().showMessage("Order aktiválva.")
        except (ValueError, sqlite3.Error) as error:
            LOGGER.warning("Order aktiválása sikertelen", exc_info=True)
            QMessageBox.warning(self, "Aktiválás sikertelen", str(error))

    def close_current_order(self) -> None:
        if self.current_order_id is None or not self.save_order(quiet=True):
            return
        if QMessageBox.question(
            self,
            "Order lezárása",
            "Lezárod az Ordert és levonod a megadott inventory-mennyiségeket?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            with open_db() as conn:
                close_order(conn, self.current_order_id)
            self.load_order(self.current_order_id)
            self.refresh_inventory()
            self.refresh_statistics(reload_months=True)
            self.refresh_dashboard()
            self.statusBar().showMessage("Order lezárva, a készlet levonva.")
        except (ValueError, sqlite3.Error) as error:
            LOGGER.warning("Order lezárása sikertelen", exc_info=True)
            QMessageBox.warning(self, "Lezárás sikertelen", str(error))

    def delete_current_order(self) -> None:
        if self.current_order_id is None:
            return
        if QMessageBox.question(
            self, "Order törlése", "Biztosan törlöd ezt a még le nem zárt Ordert?"
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            with open_db() as conn:
                delete_order(conn, self.current_order_id)
            self.new_order()
            self.refresh_orders()
            self.statusBar().showMessage("Order törölve.")
        except (ValueError, sqlite3.Error) as error:
            LOGGER.warning("Order törlése sikertelen", exc_info=True)
            QMessageBox.warning(self, "Törlés sikertelen", str(error))

    def refresh_statistics(self, _value=0, *, reload_months: bool = False) -> None:
        if not hasattr(self, "statistics_cards"):
            return
        with open_db() as conn:
            months = available_months(conn)
            inventory = list_inventory(conn)
        if reload_months:
            selected = self.statistics_month.currentData()
            self.statistics_month.blockSignals(True)
            self.statistics_month.clear()
            self.statistics_month.addItem("Összes hónap", None)
            for month in months:
                self.statistics_month.addItem(month, month)
            index = self.statistics_month.findData(selected)
            self.statistics_month.setCurrentIndex(max(0, index))
            self.statistics_month.blockSignals(False)
        with open_db() as conn:
            orders = closed_order_statistics(conn, self.statistics_month.currentData())
        grouping = str(self.statistics_group.currentData())
        grouped: dict[object, dict[str, object]] = {}
        for row in orders:
            if grouping == "item":
                key = (row["item_id"], row["quality"])
                label = f"{row['item_id']} · Q{row['quality']}"
            elif grouping == "city":
                key = row["city"]
                label = str(row["city"])
            else:
                key = row["id"]
                label = f"{row['name']} · {row['item_id']} · {row['city']}"
            target = grouped.setdefault(key, {
                "label": label, "quantity": 0, "gross": 0,
                "purchase_cost": 0, "fees": 0, "net_profit": 0,
            })
            row["fees"] = int(row["transaction_tax"]) + int(row["setup_fee"])
            for field in ("quantity", "gross", "purchase_cost", "fees", "net_profit"):
                target[field] = int(target[field]) + int(row[field])
        result_rows = sorted(
            grouped.values(), key=lambda row: int(row["net_profit"]), reverse=True
        )
        self.statistics_inventory.set_rows(list(inventory))
        self.statistics_cards.set_rows(result_rows)
        gross = sum(int(row["gross"]) for row in orders)
        cost = sum(
            int(row["purchase_cost"]) + int(row["transaction_tax"]) + int(row["setup_fee"])
            for row in orders
        )
        profit = sum(int(row["net_profit"]) for row in orders)
        self.statistics_revenue.value_label.setText(f"{gross:,} silver")
        self.statistics_cost.value_label.setText(f"{cost:,} silver")
        self.statistics_profit.value_label.setText(f"{profit:,} silver")
        self.statistics_timeline.set_data(
            aggregate_order_timeline(orders, str(self.statistics_interval.currentData()))
        )
        self.statistics_comparison.set_totals(gross, cost, profit)
        self.statistics_items.set_data(aggregate_order_profit(orders, "item"))
        self.statistics_cities.set_data(aggregate_order_profit(orders, "city"))

    def _inventory_picker_items(
        self,
        term: str,
        tier: int,
        enchantment: int,
        category_path: tuple[str, ...],
    ) -> list[tuple]:
        predicates = ["1=1"]
        parameters: list[object] = []
        if term:
            predicates.append("(name_en LIKE ? COLLATE NOCASE OR uniquename LIKE ? COLLATE NOCASE)")
            parameters.extend((f"%{term}%", f"%{term}%"))
        if tier:
            predicates.append("tier=?")
            parameters.append(tier)
        for column, category_id in zip(
            ("shopcategory", "shopsubcategory", "shopsubcategory2", "shopsubcategory3"),
            category_path,
            strict=False,
        ):
            predicates.append(f"{column}=?")
            parameters.append(category_id)
        if enchantment >= 0:
            predicates.append("enchantment=?")
            parameters.append(enchantment)
        with open_db() as conn:
            return conn.execute(
                """SELECT uniquename, COALESCE(name_en, uniquename), tier,
                          shopcategory, enchantment
                   FROM items WHERE """ + " AND ".join(predicates) +
                " ORDER BY name_en, enchantment, uniquename LIMIT 150",
                parameters,
            ).fetchall()

    def open_inventory_picker(self) -> None:
        category_rows, counts = self._category_data()
        dialog = InventoryPickerDialog(
            self.item_icon_loader,
            self._inventory_picker_items,
            category_rows,
            counts,
            self,
        )
        if not dialog.exec() or dialog.selected_row is None:
            return
        item_id = str(dialog.selected_row[0])
        quantity = dialog.quantity()
        quality = dialog.selected_quality()
        with open_db() as conn:
            set_inventory_quantity(conn, item_id, quantity, quality)
        self.refresh_inventory()
        self.show_recipe_materials()
        self.statusBar().showMessage(
            f"Inventoryhoz adva: {item_id} · Q{quality} · {quantity:,} db"
        )

    def refresh_inventory(self) -> None:
        if not hasattr(self, "inventory_cards"):
            return
        with open_db() as conn:
            rows = list_inventory(conn, self.inventory_filter.text())
        self.inventory_cards.set_inventory(list(rows))

    def save_inventory_card(self, item_id: str, quality: int, quantity: int) -> None:
        with open_db() as conn:
            set_inventory_quantity(conn, item_id, quantity, quality)
        self.refresh_inventory()
        self.show_recipe_materials()
        self.statusBar().showMessage(
            f"Készlet mentve: {item_id} · Q{quality} · {quantity:,} db"
        )

    @staticmethod
    def _inventory_price_quotes(
        item_id: str, quality: int, cities: list[str]
    ) -> list[tuple]:
        if not cities:
            return []
        placeholders = ",".join("?" for _ in cities)
        with open_db() as conn:
            return conn.execute(
                f"""SELECT city, quality, enchantment,
                           sell_price_min, sell_price_min_date,
                           sell_price_max, sell_price_max_date,
                           buy_price_min, buy_price_min_date,
                           buy_price_max, buy_price_max_date
                    FROM market_prices
                    WHERE item_uniquename=? AND quality=?
                      AND city IN ({placeholders})
                      AND (sell_price_min > 0 OR buy_price_max > 0)
                    ORDER BY city, enchantment""",
                (item_id, quality, *cities),
            ).fetchall()

    def open_inventory_price_window(self, item_id: str, quality: int) -> None:
        with open_db() as conn:
            row = conn.execute(
                "SELECT COALESCE(name_en, uniquename) FROM items WHERE uniquename=?",
                (item_id,),
            ).fetchone()
            inventory_quantity = get_inventory_quantity(conn, item_id, quality)
        item_name = str(row[0]) if row else item_id
        window = ItemPriceWindow(
            item_id,
            item_name,
            quality,
            inventory_quantity,
            self.premium.isChecked() if hasattr(self, "premium") else True,
            self._inventory_price_quotes,
            self,
        )
        self.inventory_price_windows.add(window)
        window.finished.connect(
            lambda _result, target=window: self.inventory_price_windows.discard(target)
        )
        window.show()
        window.raise_()
        window.activateWindow()

    def delete_inventory_card(self, item_id: str, quality: int) -> None:
        if QMessageBox.question(
            self,
            "Inventory elem törlése",
            f"Biztosan törlöd az inventoryból?\n{item_id} · Q{quality}",
        ) != QMessageBox.StandardButton.Yes:
            return
        with open_db() as conn:
            remove_inventory_item(conn, item_id, quality)
        self.refresh_inventory()
        self.show_recipe_materials()
        self.statusBar().showMessage(f"Készletből törölve: {item_id}")

    def search_refining_recipes(self) -> None:
        term = self.refining_search.text().strip()
        predicates, parameters = self._catalog_item_predicates(
            "i",
            term,
            self.refining_tier.value(),
            self.refining_enchantment.currentData(),
            self.refining_category.selected_path,
        )
        predicates.append(
            "EXISTS (SELECT 1 FROM refining_recipes rr "
            "WHERE rr.output_item_id = i.id)"
        )
        quality = self.refining_quality.currentData()
        if quality:
            predicates.append(
                "EXISTS (SELECT 1 FROM market_prices mp "
                "WHERE mp.item_uniquename = i.uniquename AND mp.quality = ?)"
            )
            parameters.append(quality)
        with open_db() as conn:
            rows = conn.execute(
                """SELECT r.output_item_uniquename, r.variant_index, i.name_en,
                          i.tier, r.output_amount, COUNT(m.material_uniquename)
                  FROM refining_recipes r
                  JOIN items i ON i.id=r.output_item_id
                  LEFT JOIN refining_recipe_materials m ON m.recipe_id=r.id
                  WHERE """ + " AND ".join(predicates) +
                """ GROUP BY r.id
                   ORDER BY i.tier, i.name_en, r.output_item_uniquename,
                            r.variant_index LIMIT 200""",
                parameters,
            ).fetchall()
        self.refining_recipe.clear()
        recipe_rows: list[tuple] = []
        for output, variant, name, tier, amount, material_count in rows:
            label = (
                f"{name or output} · {output} · recept {variant + 1} "
                f"({material_count} alapanyag, {amount} output)"
            )
            self.refining_recipe.addItem(label, (output, variant))
            recipe_rows.append((output, name or output, f"T{tier}", f"{material_count} alapanyag", f"{amount} output", variant))
        self.refining_recipe_cards.set_recipes(recipe_rows)
        if recipe_rows:
            self.refining_recipe_cards._select(0)
        self.statusBar().showMessage(f"{len(rows)} refining recept")

    def _select_refining_recipe_card(self, index: int) -> None:
        if 0 <= index < self.refining_recipe.count():
            self.refining_recipe.setCurrentIndex(index)

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
                (item_id, name or item_id, tier or 0, "", enchantment or 0)
            )
        for context, cards in self.favorite_tables.items():
            cards.set_items(grouped[context], 0, favorite_ids={row[0] for row in grouped[context]})

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
                order_rows = closed_order_statistics(conn)
            recent_orders = order_rows[:5]
            gross = sum(int(row["gross"]) for row in order_rows)
            cost = sum(
                int(row["purchase_cost"]) + int(row["transaction_tax"]) + int(row["setup_fee"])
                for row in order_rows
            )
            profit = sum(int(row["net_profit"]) for row in order_rows)
            self.dashboard_revenue.value_label.setText(f"{gross:,} silver")
            self.dashboard_cost.value_label.setText(f"{cost:,} silver")
            self.dashboard_profit.value_label.setText(f"{profit:,} silver")
            self.dashboard_closed.value_label.setText(f"{len(order_rows):,}")
            self.dashboard_timeline.set_data(aggregate_order_timeline(
                order_rows, str(self.dashboard_interval.currentData())
            ))
            self.dashboard_comparison.set_totals(gross, cost, profit)
            self.dashboard_items.set_data(aggregate_order_profit(order_rows, "item"))
            self.dashboard_cities.set_data(aggregate_order_profit(order_rows, "city"))
            self.dashboard_order_cards.set_rows([
                {
                    "label": f"{row['name']} · {row['item_id']} · {row['city']}",
                    "quantity": row["quantity"],
                    "gross": row["gross"],
                    "purchase_cost": row["purchase_cost"],
                    "fees": int(row["transaction_tax"]) + int(row["setup_fee"]),
                    "net_profit": row["net_profit"],
                }
                for row in recent_orders
            ])
            self.db_status.setText(
                f"Items: {items:,}    |    Piaci rekordok: {prices:,}    |    "
                f"Receptek: {recipes:,}    |    Városok: {cities}"
            )
            self.statusBar().showMessage("Adatbázis állapot frissítve")
        except sqlite3.Error as error:
            LOGGER.exception("Az adatbázis-állapot nem tölthető be")
            self.statusBar().showMessage(f"Adatbázis-hiba: {error}")

    def load_categories(self) -> None:
        """Load the imported hierarchy into the compact category popup."""

        category_rows, counts = self._category_data()
        self.item_category.set_categories(category_rows, counts)
        if hasattr(self, "craft_category"):
            self.craft_category.set_categories(category_rows, counts)
        if hasattr(self, "flip_category"):
            self.flip_category.set_categories(category_rows, counts)
        if hasattr(self, "refining_category"):
            self.refining_category.set_categories(category_rows, counts)

    @staticmethod
    def _category_data() -> tuple[list[tuple], dict[tuple[str, ...], int]]:
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
        return list(category_rows), counts

    def selected_category_path(self) -> tuple[str, ...]:
        return self.item_category.selected_path

    @staticmethod
    def _catalog_item_predicates(
        alias: str,
        term: str,
        tier: int,
        enchantment: int,
        category_path: tuple[str, ...],
    ) -> tuple[list[str], list[object]]:
        predicates = [f"{alias}.tier = ?"]
        parameters: list[object] = [tier]
        if term:
            predicates.append(
                f"({alias}.name_en LIKE ? COLLATE NOCASE OR "
                f"{alias}.uniquename LIKE ? COLLATE NOCASE)"
            )
            parameters.extend((f"%{term}%", f"%{term}%"))
        for column, category_id in zip(
            ("shopcategory", "shopsubcategory", "shopsubcategory2", "shopsubcategory3"),
            category_path,
            strict=False,
        ):
            predicates.append(f"{alias}.{column} = ?")
            parameters.append(category_id)
        if enchantment >= 0:
            predicates.append(f"{alias}.enchantment = ?")
            parameters.append(enchantment)
        return predicates, parameters

    def search_items(self) -> None:
        term = self.item_search.text().strip()
        enchantment = self.item_enchantment.currentData()
        predicates, parameters = self._catalog_item_predicates(
            "i", term, self.item_tier.value(), enchantment, self.selected_category_path()
        )
        with open_db() as conn:
            rows = conn.execute(
                f"""SELECT i.uniquename, i.name_en, i.tier, i.shopcategory, i.enchantment
                    FROM items i WHERE {' AND '.join(predicates)}
                    ORDER BY i.name_en, i.enchantment, i.uniquename LIMIT 100""",
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
        self.item_table.set_opportunities(rows, item_id=self.selected_ids[0])
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
            self.craft_materials.set_materials([])
            self.craft_dependency_tree.set_tree(None)
            return
        recipe_id = rows[current][0]
        output_id = rows[current][1]
        output_amount = max(1, int(rows[current][4] or 1))
        batches = self.craft_batches.value()
        cities = self.craft_cities.selected_cities()
        if not cities:
            self.statusBar().showMessage("Jelölj ki legalább egy crafting/piaci várost.")
            self.craft_materials.set_materials([])
            self.craft_dependency_tree.set_tree(None)
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
            inventory = inventory_quantities(
                conn, tuple(str(row[0]) for row in materials)
            )
            all_inventory = {
                str(item_id): int(quantity)
                for item_id, quantity in conn.execute(
                    """SELECT item_uniquename, SUM(quantity)
                       FROM user.inventory GROUP BY item_uniquename"""
                )
            }
        source = self.craft_source.currentData()
        display_rows = []
        for unique_name, name, amount, price, city, returnable in materials:
            required = int(amount) * batches
            allocation = allocate_inventory(required, inventory.get(str(unique_name), 0))
            purchase_cost = (
                int(price) * allocation.missing
                if source == "buy" and price is not None
                else 0 if source == "farm" else None
            )
            display_rows.append((
                name, unique_name, required,
                "Saját farmolás" if source == "farm" else price,
                city or "N/A", bool(returnable), allocation.available,
                allocation.used, allocation.missing, purchase_cost,
            ))
        self.craft_materials.set_materials(display_rows)
        with open_db() as conn:
            tree = build_crafting_dependency_tree(
                conn,
                output_id,
                output_amount * batches,
                recipe_id=recipe_id,
                cities=cities,
                inventory=all_inventory,
            )
        self.craft_dependency_tree.set_tree(tree)

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
        batches = self.craft_batches.value()
        output_total = max(1, int(output_amount or 1)) * batches
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
            station_cost = self.crafting_price.value() * batches
            recipe_cost = int(silver or 0) * batches
            cost = recipe_cost + station_cost
            missing: list[str] = []
            inventory_used = 0
            purchase_cost = 0
            inventory = inventory_quantities(
                conn, tuple(str(material_id) for material_id, _amount in materials)
            )
            if self.craft_source.currentData() == "buy":
                for material_id, amount in materials:
                    required = int(amount) * batches
                    allocation = allocate_inventory(required, inventory.get(str(material_id), 0))
                    inventory_used += allocation.used
                    if allocation.missing == 0:
                        continue
                    price = conn.execute(
                        f"""SELECT MIN(sell_price_min) FROM market_prices
                            WHERE item_uniquename=? AND sell_price_min>0
                              AND city IN ({city_placeholders})""",
                        (material_id, *cities),
                    ).fetchone()[0]
                    if price is None:
                        missing.append(material_id)
                    else:
                        material_cost = int(price) * allocation.missing
                        purchase_cost += material_cost
                        cost += material_cost
            else:
                for material_id, amount in materials:
                    required = int(amount) * batches
                    inventory_used += allocate_inventory(
                        required, inventory.get(str(material_id), 0)
                    ).used
        self.show_recipe_materials()
        if missing and self.craft_source.currentData() == "buy":
            self.craft_output.setPlainText(
                f"{output_name}\nVásárlási profit nem számítható.\n"
                f"Hiányzó piaci adatok: {', '.join(missing)}\n"
                f"Készletből felhasználható: {inventory_used:,} db\n"
                f"Farmolási költségalap: {recipe_cost + station_cost:,} silver "
                f"(recept: {recipe_cost:,}, állomás: {station_cost:,})"
            )
            return
        fees = market_fee_policy(self.premium.isChecked())
        gross_revenue = sale * output_total if sale is not None else None
        market_tax = fees.transaction_tax(gross_revenue) if gross_revenue is not None else 0
        profit = gross_revenue - market_tax - cost if gross_revenue is not None else None
        self.craft_output.setPlainText(
            f"{output_name} · {batches:,} craft kör · output: {output_total:,} db\n"
            f"Készletből fedezve: {inventory_used:,} alapanyag db\n"
            f"Piacról beszerzendő anyagok: {purchase_cost:,} silver\n"
            f"Recept silver: {recipe_cost:,}\nCrafting állomásdíj: {station_cost:,}\n"
            f"Teljes készpénzköltség: {cost:,}\n"
            f"Bruttó eladási bevétel: {gross_revenue:,}\n"
            f"Fix piaci adó: {market_tax:,}\nNettó profit: {profit:,}"
            if profit is not None else f"{output_name}\nEladási piaci adat nincs."
        )

    def search_flip_items(self) -> None:
        term = self.flip_search.text().strip()
        predicates, parameters = self._catalog_item_predicates(
            "i",
            term,
            self.flip_tier.value(),
            self.flip_enchantment.currentData(),
            self.flip_category.selected_path,
        )
        predicates.append(
            "EXISTS (SELECT 1 FROM market_prices mp "
            "WHERE mp.item_uniquename=i.uniquename)"
        )
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
        self.global_table.set_opportunities(rows, include_item=True)
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
            script = PROJECT_ROOT / "scripts" / "data_sorter.py"
            if not script.exists() or not ITEM_CATALOG_FILE.exists() or not LOCALIZATION_FILE.exists():
                raise RuntimeError(
                    "A katalógus újraépítéséhez items.json és localization.json szükséges."
                )
            runpy.run_path(str(script), run_name="__main__")
            return "Adatbázis újraépítve."

        self.run_background(rebuild, self._done)

    def import_crafting_data(self) -> None:
        def import_data() -> str:
            if not ITEM_CATALOG_FILE.exists():
                raise RuntimeError("A craft importhoz items.json szükséges.")
            recipes, materials, unresolved = import_crafting_catalog(
                str(DB_FILE), str(ITEM_CATALOG_FILE)
            )
            return (
                f"Craft adatok importálva: {recipes} recept, {materials} alapanyag, "
                f"{unresolved} feloldatlan hivatkozás."
            )

        self.run_background(import_data, self._done)

    def refresh_item_categories(self) -> None:
        """Import category metadata without deleting cached market prices."""

        def import_categories() -> str:
            with open_db() as conn:
                category_count, item_count = sync_item_category_data(
                    conn, ITEM_CATALOG_FILE
                )
            return (
                f"Kategóriafa frissítve: {category_count} csomópont, "
                f"{item_count} item. A piaci adatok megmaradtak."
            )

        self.run_background(import_categories, self._done)

    def open_log_folder(self) -> None:
        configure_logging()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        LOGGER.info("Log mappa megnyitása")
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR))):
            QMessageBox.warning(
                self, "Log mappa", f"A mappa nem nyitható meg automatikusan:\n{LOG_DIR}"
            )

    def export_diagnostics(self) -> None:
        documents = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        destination = Path(documents) if documents else LOG_DIR.parent
        suggested = destination / (
            f"AlbionPrizeShower-diagnosztika-{datetime.now():%Y%m%d-%H%M%S}.zip"
        )
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "Diagnosztikai csomag mentése",
            str(suggested),
            "ZIP archívum (*.zip)",
        )
        if not selected:
            return
        try:
            bundle = export_diagnostic_bundle(selected)
            LOGGER.info("Diagnosztikai csomag elkészült: %s", bundle.name)
            QMessageBox.information(
                self,
                "Diagnosztikai csomag elkészült",
                f"Ezt a fájlt küldd el a fejlesztőnek:\n{bundle}\n\n"
                "Személyes adatbázisok nem kerültek a ZIP-be.",
            )
        except (OSError, ValueError) as error:
            LOGGER.exception("A diagnosztikai csomag nem menthető")
            QMessageBox.critical(self, "Mentési hiba", str(error))

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
    configure_logging()
    app = QApplication.instance() or QApplication(sys.argv)
    install_qt_message_logging()
    LOGGER.info("PyQt6 felület indítása")
    app.setFont(QFont("Segoe UI", 10))
    window = AlbionWindow()
    window.show()
    exit_code = app.exec()
    LOGGER.info("Alkalmazás leállt | exit_code=%s", exit_code)


if __name__ == "__main__":
    run_gui()
