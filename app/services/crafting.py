"""Import crafting requirements from items.json into albion.db."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.paths import CATALOG_DB_FILE, ITEMS_FILE as ITEM_CATALOG_PATH

ITEMS_FILE = str(ITEM_CATALOG_PATH)
DB_FILE = str(CATALOG_DB_FILE)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _walk_items(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if "@uniquename" in node and (
            "craftingrequirements" in node or "enchantments" in node
        ):
            found.append(node)
        for value in node.values():
            found.extend(_walk_items(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk_items(value))
    return found


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(id),
            item_uniquename TEXT NOT NULL,
            variant_index INTEGER NOT NULL DEFAULT 0,
            output_amount INTEGER NOT NULL DEFAULT 1,
            silver_cost INTEGER NOT NULL DEFAULT 0,
            craft_time_seconds REAL NOT NULL DEFAULT 0,
            crafting_focus INTEGER NOT NULL DEFAULT 0,
            swap_transaction INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(item_id, variant_index)
        );
        CREATE TABLE IF NOT EXISTS recipe_materials (
            recipe_id INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
            material_item_id INTEGER REFERENCES items(id),
            material_uniquename TEXT NOT NULL,
            amount INTEGER NOT NULL,
            returnable INTEGER NOT NULL DEFAULT 1,
            max_return_amount INTEGER,
            PRIMARY KEY (recipe_id, material_uniquename)
        );
        CREATE INDEX IF NOT EXISTS idx_recipe_materials_item
            ON recipe_materials(material_item_id);
        """
    )


def _requirements(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    return []


def _recipe_entries(item: dict[str, Any]) -> list[tuple[str, int, dict[str, Any]]]:
    """Return (output id, variant index, requirements) for base and enchant recipes."""

    base_name = item["@uniquename"]
    entries = [
        (base_name, index, requirement)
        for index, requirement in enumerate(_requirements(item.get("craftingrequirements")))
    ]
    enchantments = item.get("enchantments")
    if not isinstance(enchantments, dict):
        return entries
    levels = enchantments.get("enchantment", [])
    if isinstance(levels, dict):
        levels = [levels]
    for level in levels:
        if not isinstance(level, dict):
            continue
        enchantment = _as_int(level.get("@enchantmentlevel"), -1)
        if enchantment < 0:
            continue
        output_name = f"{base_name}@{enchantment}"
        entries.extend(
            (output_name, index, requirement)
            for index, requirement in enumerate(
                _requirements(level.get("craftingrequirements"))
            )
        )
    return entries


def import_crafting_data(
    db_file: str = DB_FILE,
    items_file: str = ITEMS_FILE,
) -> tuple[int, int, int]:
    """Import recipes and materials; return (recipes, materials, unresolved)."""
    with open(items_file, encoding="utf-8") as source:
        raw_items = json.load(source)

    with sqlite3.connect(db_file) as conn:
        _create_tables(conn)
        # Local import avoids a module-level dependency cycle during startup.
        from app.core.database import apply_migrations

        apply_migrations(conn)
        item_ids = {
            row[1]: row[0]
            for row in conn.execute("SELECT id, uniquename FROM items")
        }
        recipes = materials = unresolved = 0
        seen_items: set[str] = set()
        for item in _walk_items(raw_items):
            base_name = item["@uniquename"]
            if base_name in seen_items:
                continue
            seen_items.add(base_name)
            for unique_name, variant_index, requirement in _recipe_entries(item):
                item_id = item_ids.get(unique_name)
                if item_id is None:
                    unresolved += 1
                    continue
                conn.execute(
                    """INSERT INTO recipes (
                           item_id, item_uniquename, variant_index, output_amount, silver_cost,
                           craft_time_seconds, crafting_focus, swap_transaction
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(item_id, variant_index) DO UPDATE SET
                           item_uniquename=excluded.item_uniquename,
                           output_amount=excluded.output_amount,
                           silver_cost=excluded.silver_cost,
                           craft_time_seconds=excluded.craft_time_seconds,
                           crafting_focus=excluded.crafting_focus,
                           swap_transaction=excluded.swap_transaction,
                           imported_at=CURRENT_TIMESTAMP""",
                    (
                        item_id,
                        unique_name,
                        variant_index,
                        max(1, _as_int(requirement.get("@amountcrafted"), 1)),
                        _as_int(requirement.get("@silver")),
                        _as_float(requirement.get("@time")),
                        _as_int(requirement.get("@craftingfocus")),
                        1 if requirement.get("@swaptransaction") == "true" else 0,
                    ),
                )
                recipe_id = conn.execute(
                    "SELECT id FROM recipes WHERE item_id = ? AND variant_index = ?",
                    (item_id, variant_index),
                ).fetchone()[0]
                conn.execute("DELETE FROM recipe_materials WHERE recipe_id = ?", (recipe_id,))
                raw_materials = requirement.get("craftresource", [])
                if isinstance(raw_materials, dict):
                    raw_materials = [raw_materials]
                for material in raw_materials:
                    if not isinstance(material, dict) or "@uniquename" not in material:
                        continue
                    material_name = material["@uniquename"]
                    max_return = material.get("@maxreturnamount")
                    returnable = max_return not in ("0", 0)
                    conn.execute(
                        """INSERT INTO recipe_materials (
                               recipe_id, material_item_id, material_uniquename, amount,
                               returnable, max_return_amount
                           ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            recipe_id,
                            item_ids.get(material_name),
                            material_name,
                            _as_int(material.get("@count")),
                            1 if returnable else 0,
                            _as_int(max_return) if max_return is not None else None,
                        ),
                    )
                    materials += 1
                recipes += 1
        conn.commit()
    return recipes, materials, unresolved


if __name__ == "__main__":
    recipe_count, material_count, unresolved_count = import_crafting_data()
    print(
        f"Recipes: {recipe_count}, materials: {material_count}, "
        f"unresolved items: {unresolved_count}"
    )
