"""Import the item category hierarchy and item category paths from items.json."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

CATEGORY_KEYS = (
    "shopcategory",
    "shopsubcategory",
    "shopsubcategory2",
    "shopsubcategory3",
)
ITEM_COLUMNS = (
    "shopcategory",
    "shopsubcategory",
    "shopsubcategory2",
    "shopsubcategory3",
)


def _as_list(value: Any) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    return []


def iter_category_rows(items_data: dict) -> Iterator[tuple]:
    """Yield path-safe rows from the nested shop category definition."""

    root = items_data.get("items", {}).get("shopcategories", {})

    def walk(container: dict, level: int, parent_path: str | None) -> Iterator[tuple]:
        if level >= len(CATEGORY_KEYS):
            return
        key = CATEGORY_KEYS[level]
        for position, node in enumerate(_as_list(container.get(key))):
            category_id = str(node.get("@id", "")).strip()
            if not category_id:
                continue
            path = f"{parent_path}/{category_id}" if parent_path else category_id
            hidden = str(node.get("@hideindropdown", "false")).lower() == "true"
            yield (
                path,
                category_id,
                level,
                parent_path,
                position,
                int(hidden),
            )
            yield from walk(node, level + 1, path)

    yield from walk(root, 0, None)


def _walk_item_categories(node: Any) -> Iterator[tuple[str, str | None, str | None, str | None, str | None]]:
    if isinstance(node, list):
        for entry in node:
            yield from _walk_item_categories(entry)
        return
    if not isinstance(node, dict):
        return
    unique_name = node.get("@uniquename")
    if unique_name:
        yield (
            unique_name,
            node.get("@shopcategory"),
            node.get("@shopsubcategory1"),
            node.get("@shopsubcategory2"),
            node.get("@shopsubcategory3"),
        )
        return
    for key, value in node.items():
        if not key.startswith("@") and key != "shopcategories":
            yield from _walk_item_categories(value)


def sync_item_categories(conn: sqlite3.Connection, items_data: dict) -> tuple[int, int]:
    """Refresh the hierarchy and item paths without touching market data."""

    category_rows = list(iter_category_rows(items_data))
    base_paths = {
        unique_name: (category1, category2, category3, category4)
        for unique_name, category1, category2, category3, category4
        in _walk_item_categories(items_data.get("items", {}))
    }
    item_updates = []
    for (unique_name,) in conn.execute("SELECT uniquename FROM items"):
        base_name = unique_name.split("@", 1)[0]
        path = base_paths.get(base_name)
        if path is not None:
            item_updates.append((*path, unique_name))

    with conn:
        conn.execute("DELETE FROM item_categories")
        conn.executemany(
            """INSERT INTO item_categories(
                   path, category_id, level, parent_path, sort_order, hidden
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            category_rows,
        )
        conn.executemany(
            """UPDATE items SET shopcategory = ?, shopsubcategory = ?,
                   shopsubcategory2 = ?, shopsubcategory3 = ?
               WHERE uniquename = ?""",
            item_updates,
        )
    return len(category_rows), len(item_updates)


def import_item_categories(conn: sqlite3.Connection, items_file: str | Path) -> tuple[int, int]:
    with Path(items_file).open(encoding="utf-8") as handle:
        return sync_item_categories(conn, json.load(handle))
