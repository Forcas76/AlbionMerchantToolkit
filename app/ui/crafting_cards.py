"""Compact crafting material cards and a visual dependency graph."""

from __future__ import annotations

import weakref
from collections.abc import Iterable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGraphicsLineItem,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.services.crafting import CraftingDependencyNode
from app.ui.item_cards import ItemIconLoader, item_icon_url


def _price_text(price: int | str | None, city: str | None = None) -> str:
    if price is None:
        return "Ár: nincs adat"
    if isinstance(price, str):
        return price
    suffix = f" · {city}" if city else ""
    return f"{price:,} silver/db{suffix}"


class _IconMixin:
    """Small shared helper for the asynchronous item icon loader."""

    def _request_icon(
        self,
        loader: ItemIconLoader,
        item_id: str,
        target: QLabel,
        size: int = 64,
    ) -> None:
        target_ref = weakref.ref(target)

        def apply_icon(pixmap: QPixmap | None) -> None:
            label = target_ref()
            if label is None:
                return
            if pixmap is None:
                label.setText("Nincs kép")
                return
            label.setText("")
            label.setPixmap(
                pixmap.scaled(
                    label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

        loader.request_icon(item_icon_url(item_id, 1, size), apply_icon)


class CraftingMaterialCard(QFrame, _IconMixin):
    """A compact direct-material overview card."""

    def __init__(self, row: tuple, loader: ItemIconLoader) -> None:
        super().__init__()
        name, unique_name, amount, price, city, returnable = row[:6]
        available, used, missing, purchase_cost = (tuple(row[6:10]) + (0, 0, amount, None))[:4]
        self.setObjectName("craftMaterialCard")
        self.setMinimumSize(250, 126)
        self.setMaximumHeight(142)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 7, 8, 7)
        root.setSpacing(8)
        icon = QLabel("IMG")
        icon.setObjectName("craftMaterialIcon")
        icon.setFixedSize(48, 48)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(icon)
        body = QVBoxLayout()
        body.setSpacing(2)
        title = QLabel(str(name or unique_name))
        title.setObjectName("craftMaterialTitle")
        title.setWordWrap(True)
        quantity = QLabel(f"Szükséges: {int(amount):,} db · készleten: {int(available):,} db")
        quantity.setObjectName("craftMaterialMeta")
        allocation = QLabel(
            f"Készletből: {int(used):,} db · beszerzendő: {int(missing):,} db"
        )
        allocation.setObjectName("craftMaterialMeta")
        market = QLabel(_price_text(price, city))
        if purchase_cost is not None:
            market.setText(f"{market.text()} · beszerzés: {int(purchase_cost):,}")
        market.setObjectName("craftMaterialMeta")
        identifier = QLabel(str(unique_name))
        identifier.setObjectName("craftMaterialId")
        identifier.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(title)
        body.addWidget(quantity)
        body.addWidget(allocation)
        body.addWidget(market)
        body.addWidget(QLabel("Visszatérhet" if returnable else "Nem visszatérő"))
        body.addWidget(identifier)
        root.addLayout(body, 1)
        self._request_icon(loader, str(unique_name), icon)


class CraftingMaterialCardGrid(QScrollArea):
    """Responsive grid replacing the wide crafting materials table."""

    def __init__(self, loader: ItemIconLoader) -> None:
        super().__init__()
        self.loader = loader
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("craftMaterialScroll")
        self.content = QWidget()
        self.content.setObjectName("craftMaterialContent")
        self.grid = QGridLayout(self.content)
        self.grid.setContentsMargins(0, 2, 4, 2)
        self.grid.setSpacing(8)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.cards: list[CraftingMaterialCard] = []
        self.setWidget(self.content)

    def set_materials(self, rows: Iterable[tuple]) -> None:
        for card in self.cards:
            card.deleteLater()
        while self.grid.takeAt(0) is not None:
            pass
        self.cards = [CraftingMaterialCard(tuple(row), self.loader) for row in rows]
        self._relayout()

    def _relayout(self) -> None:
        while self.grid.takeAt(0) is not None:
            pass
        columns = max(1, self.viewport().width() // 245)
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self.grid.setColumnStretch(column, 1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()


class CraftingNodeCard(QFrame, _IconMixin):
    """A graph node showing item identity, quantity and known market price."""

    def __init__(self, node: CraftingDependencyNode, loader: ItemIconLoader) -> None:
        super().__init__()
        self.node = node
        self.setObjectName("craftingNodeCard")
        self.setProperty("raw", not node.craftable)
        self.setProperty("cycle", node.is_cycle)
        self.setFixedSize(260, 118)
        root = QHBoxLayout(self)
        root.setContentsMargins(9, 8, 9, 8)
        root.setSpacing(8)
        icon = QLabel("IMG")
        icon.setObjectName("craftingNodeIcon")
        icon.setFixedSize(48, 48)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        body = QVBoxLayout()
        body.setSpacing(2)
        title = QLabel(node.display_name)
        title.setObjectName("craftingNodeTitle")
        title.setWordWrap(True)
        subtitle = QLabel(node.item_uniquename)
        subtitle.setObjectName("craftingNodeId")
        subtitle.setWordWrap(True)
        quantity = QLabel(
            f"Szükséges: {node.required_quantity:,} db · készletből: "
            f"{node.inventory_used:,} db · hiány: {node.missing_quantity:,} db"
        )
        quantity.setObjectName("craftingNodeQuantity")
        if node.is_cycle:
            status = "⚠ Ciklus – bontás leállítva"
        elif node.expansion_stopped:
            status = "⚠ Mélységlimit – bontás leállítva"
        elif node.craftable:
            status = f"Craftolható · {node.batches:,} batch"
        else:
            status = "Nyers / nem craftolható"
        status_label = QLabel(status)
        status_label.setObjectName("craftingNodeStatus")
        price_text = _price_text(node.unit_price, node.price_city)
        if node.total_price is not None and node.required_quantity > 1:
            price_text += f" · össz. {node.total_price:,}"
        price = QLabel(price_text)
        price.setObjectName("craftingNodePrice")
        body.addWidget(title)
        body.addWidget(subtitle)
        body.addWidget(quantity)
        body.addWidget(status_label)
        body.addWidget(price)
        root.addLayout(body, 1)
        self._request_icon(loader, node.item_uniquename, icon)


class CraftingDependencyTreeView(QGraphicsView):
    """A left-to-right card graph with explicit parent/child connector lines."""

    def __init__(self, loader: ItemIconLoader) -> None:
        super().__init__()
        self.loader = loader
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setObjectName("craftingTreeView")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMinimumHeight(350)
        self._proxies: list[QGraphicsProxyWidget] = []
        self._lines: list[QGraphicsLineItem] = []

    def set_tree(self, root: CraftingDependencyNode | None) -> None:
        self._scene.clear()
        self._proxies.clear()
        self._lines.clear()
        if root is None:
            return
        placements: list[tuple[CraftingDependencyNode, int, float]] = []
        next_row = 0

        def place(node: CraftingDependencyNode, depth: int) -> float:
            nonlocal next_row
            if node.children:
                child_rows = [place(child, depth + 1) for child in node.children]
                row = sum(child_rows) / len(child_rows)
            else:
                row = float(next_row)
                next_row += 1
            placements.append((node, depth, row))
            return row

        place(root, 0)
        by_node: dict[int, QGraphicsProxyWidget] = {}
        for node, depth, row in placements:
            card = CraftingNodeCard(node, self.loader)
            proxy = self._scene.addWidget(card)
            x = depth * 310 + 20
            y = row * 145 + 20
            proxy.setPos(x, y)
            self._proxies.append(proxy)
            by_node[id(node)] = proxy

        pen = QPen(QColor("#9a7131"), 2)
        for node, _depth, _row in placements:
            parent_proxy = by_node[id(node)]
            parent_rect = parent_proxy.boundingRect()
            parent_x = parent_proxy.pos().x() + parent_rect.right()
            parent_y = parent_proxy.pos().y() + parent_rect.center().y()
            for child in node.children:
                child_proxy = by_node[id(child)]
                child_rect = child_proxy.boundingRect()
                child_x = child_proxy.pos().x()
                child_y = child_proxy.pos().y() + child_rect.center().y()
                line = self._scene.addLine(parent_x, parent_y, child_x, child_y, pen)
                line.setZValue(-1)
                self._lines.append(line)

        width = max((depth for _node, depth, _row in placements), default=0) * 310 + 310
        height = max((row for _node, _depth, row in placements), default=0) * 145 + 170
        self._scene.setSceneRect(0, 0, width, height)
        self.resetTransform()
        self.centerOn(by_node[id(root)])
