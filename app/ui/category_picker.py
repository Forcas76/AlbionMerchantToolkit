"""Compact category button with a tree rendered in an overlay popup."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)


class CategoryPopupButton(QPushButton):
    """A combobox-like control whose dropdown can contain a real tree."""

    selection_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__("Összes kategória  ▾", parent)
        self.setObjectName("categoryButton")
        self.setFixedWidth(180)
        self.setToolTip("Kategóriafa megnyitása")
        self._selected_path: tuple[str, ...] = ()
        self.clicked.connect(self.show_popup)

        self.popup = QFrame(self, Qt.WindowType.Popup)
        self.popup.setObjectName("categoryPopup")
        popup_layout = QVBoxLayout(self.popup)
        popup_layout.setContentsMargins(10, 10, 10, 10)
        popup_layout.setSpacing(8)
        title = QLabel("Item kategória")
        title.setObjectName("categoryPopupTitle")
        popup_layout.addWidget(title)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Kategória keresése…")
        self.filter_input.setClearButtonEnabled(True)
        popup_layout.addWidget(self.filter_input)
        self.tree = QTreeWidget()
        self.tree.setObjectName("categoryTree")
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        popup_layout.addWidget(self.tree, 1)
        self.filter_input.textChanged.connect(self._filter_tree)
        self.tree.itemClicked.connect(self._choose_item)

    @property
    def selected_path(self) -> tuple[str, ...]:
        return self._selected_path

    def set_categories(self, rows: list[tuple], counts: dict[tuple[str, ...], int]) -> None:
        self.tree.clear()
        root = QTreeWidgetItem(["Összes kategória"])
        root.setData(0, Qt.ItemDataRole.UserRole, ())
        self.tree.addTopLevelItem(root)
        nodes: dict[str, QTreeWidgetItem] = {"": root}
        for path, category_id, parent_path, hidden in rows:
            parent = nodes.get(parent_path or "", root)
            category_path = tuple(path.split("/"))
            label = category_id.replace("_", " ").title()
            item = QTreeWidgetItem([f"{label}  ({counts.get(category_path, 0)})"])
            item.setData(0, Qt.ItemDataRole.UserRole, category_path)
            if hidden:
                item.setForeground(0, Qt.GlobalColor.gray)
                item.setToolTip(0, "Az Albion kategóriafájában rejtett kategória")
            parent.addChild(item)
            nodes[path] = item
        root.setExpanded(True)

    def show_popup(self) -> None:
        popup_width = max(380, self.width())
        popup_height = 460
        position = self.mapToGlobal(QPoint(0, self.height() + 4))
        screen = QGuiApplication.screenAt(position)
        if screen is not None:
            available = screen.availableGeometry()
            position.setX(min(max(position.x(), available.left()), available.right() - popup_width))
            if position.y() + popup_height > available.bottom():
                position.setY(self.mapToGlobal(QPoint(0, 0)).y() - popup_height - 4)
        self.popup.resize(popup_width, popup_height)
        self.popup.move(position)
        self.popup.show()
        self.popup.raise_()
        self.filter_input.setFocus()

    def _choose_item(self, item: QTreeWidgetItem) -> None:
        self._selected_path = tuple(item.data(0, Qt.ItemDataRole.UserRole) or ())
        if self._selected_path:
            label = " › ".join(part.replace("_", " ").title() for part in self._selected_path)
        else:
            label = "Összes kategória"
        self.setText(f"{label}  ▾")
        self.setToolTip(label)
        self.popup.hide()
        self.selection_changed.emit()

    def _filter_tree(self, text: str) -> None:
        term = text.strip().casefold()

        def visit(item: QTreeWidgetItem) -> bool:
            child_match = False
            for index in range(item.childCount()):
                child_match = visit(item.child(index)) or child_match
            own_match = not term or term in item.text(0).casefold()
            visible = own_match or child_match
            item.setHidden(not visible)
            if term and child_match:
                item.setExpanded(True)
            return visible

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))
