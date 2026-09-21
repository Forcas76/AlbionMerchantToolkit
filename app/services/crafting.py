"""Import recipes and build cost-oriented crafting dependency trees."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import ceil
from typing import Any

from app.paths import CATALOG_DB_FILE, ITEMS_FILE as ITEM_CATALOG_PATH

ITEMS_FILE = str(ITEM_CATALOG_PATH)
DB_FILE = str(CATALOG_DB_FILE)
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CraftingDependencyNode:
    """One item in a recursively expanded crafting dependency tree.

    ``required_quantity`` is the amount needed by the parent recipe (or the
    requested amount for the root).  A node is deliberately kept in the tree
    even when it has no recipe: those raw materials are useful endpoints for
    both the UI and cost calculations.
    """

    item_uniquename: str
    display_name: str
    required_quantity: int
    depth: int
    craftable: bool
    recipe_id: int | None = None
    variant_index: int = 0
    output_amount: int = 1
    batches: int = 0
    silver_cost: int = 0
    unit_price: int | None = None
    price_city: str | None = None
    total_price: int | None = None
    inventory_available: int = 0
    inventory_used: int = 0
    missing_quantity: int = 0
    children: tuple["CraftingDependencyNode", ...] = ()
    is_cycle: bool = False
    expansion_stopped: bool = False

    @property
    def quantity(self) -> int:
        """Short alias used by card renderers and integrations."""

        return self.required_quantity

    @property
    def status(self) -> str:
        if self.is_cycle:
            return "cycle"
        if self.expansion_stopped:
            return "depth-limit"
        return "crafted" if self.craftable else "raw"


CraftingTreeNode = CraftingDependencyNode


def _price_for_item(
    conn: sqlite3.Connection,
    item_uniquename: str,
    cities: Sequence[str] | None,
) -> tuple[int | None, str | None]:
    """Return the cheapest known sell order and its city."""

    if not cities:
        row = conn.execute(
            """SELECT sell_price_min, city
               FROM market_prices
               WHERE item_uniquename=? AND sell_price_min > 0
               ORDER BY sell_price_min, city
               LIMIT 1""",
            (item_uniquename,),
        ).fetchone()
    else:
        placeholders = ",".join("?" for _ in cities)
        row = conn.execute(
            f"""SELECT sell_price_min, city
                FROM market_prices
                WHERE item_uniquename=? AND city IN ({placeholders})
                  AND sell_price_min > 0
                ORDER BY sell_price_min, city
                LIMIT 1""",
            (item_uniquename, *cities),
        ).fetchone()
    return (int(row[0]), str(row[1])) if row else (None, None)


def build_crafting_dependency_tree(
    conn: sqlite3.Connection,
    item_uniquename: str,
    quantity: int = 1,
    *,
    recipe_id: int | None = None,
    cities: Sequence[str] | None = None,
    inventory: Mapping[str, int] | None = None,
    max_depth: int = 20,
) -> CraftingDependencyNode:
    """Expand recipes into a cycle-safe dependency tree.

    The first recipe variant is used for nested materials.  ``recipe_id`` can
    pin the selected root variant.  Ancestor-path tracking prevents a bad
    catalogue cycle from recursing forever, while ``max_depth`` is an
    additional guard for unusually deep imported data.
    """

    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    requested = max(1, _as_int(quantity, 1))
    price_cache: dict[str, tuple[int | None, str | None]] = {}
    recipe_cache: dict[str, tuple[Any, ...] | None] = {}
    name_cache: dict[str, str] = {}
    remaining_inventory = {
        str(item_id): max(0, _as_int(amount))
        for item_id, amount in (inventory or {}).items()
    }

    def item_name(unique_name: str) -> str:
        if unique_name not in name_cache:
            row = conn.execute(
                "SELECT name_en FROM items WHERE uniquename=? LIMIT 1",
                (unique_name,),
            ).fetchone()
            name_cache[unique_name] = str(row[0]) if row and row[0] else unique_name
        return name_cache[unique_name]

    def recipe_for(unique_name: str, pinned_id: int | None = None) -> tuple[Any, ...] | None:
        if pinned_id is not None:
            row = conn.execute(
                """SELECT id, variant_index, output_amount, silver_cost
                   FROM recipes WHERE id=? AND item_uniquename=?""",
                (pinned_id, unique_name),
            ).fetchone()
            if row:
                return tuple(row)
        if unique_name not in recipe_cache:
            row = conn.execute(
                """SELECT id, variant_index, output_amount, silver_cost
                   FROM recipes
                   WHERE item_uniquename=?
                   ORDER BY variant_index, id
                   LIMIT 1""",
                (unique_name,),
            ).fetchone()
            recipe_cache[unique_name] = tuple(row) if row else None
        return recipe_cache[unique_name]

    def price_for(unique_name: str) -> tuple[int | None, str | None]:
        if unique_name not in price_cache:
            price_cache[unique_name] = _price_for_item(conn, unique_name, cities)
        return price_cache[unique_name]

    def expand(
        unique_name: str,
        required: int,
        depth: int,
        ancestors: frozenset[str],
        pinned_id: int | None = None,
        use_inventory: bool = True,
    ) -> CraftingDependencyNode:
        required = max(1, required)
        available = remaining_inventory.get(unique_name, 0) if use_inventory else 0
        inventory_used = min(required, available)
        missing = required - inventory_used
        if inventory_used:
            remaining_inventory[unique_name] = available - inventory_used
        unit_price, price_city = price_for(unique_name)
        total_price = unit_price * missing if unit_price is not None else None
        recipe = recipe_for(unique_name, pinned_id)
        if unique_name in ancestors:
            return CraftingDependencyNode(
                unique_name,
                item_name(unique_name),
                required,
                depth,
                bool(recipe),
                recipe_id=recipe[0] if recipe else None,
                variant_index=recipe[1] if recipe else 0,
                output_amount=recipe[2] if recipe else 1,
                unit_price=unit_price,
                price_city=price_city,
                total_price=total_price,
                inventory_available=available,
                inventory_used=inventory_used,
                missing_quantity=missing,
                is_cycle=True,
            )
        if recipe is None:
            return CraftingDependencyNode(
                unique_name,
                item_name(unique_name),
                required,
                depth,
                False,
                unit_price=unit_price,
                price_city=price_city,
                total_price=total_price,
                inventory_available=available,
                inventory_used=inventory_used,
                missing_quantity=missing,
            )

        recipe_key, variant, output_amount, silver_cost = recipe
        output_amount = max(1, _as_int(output_amount, 1))
        batches = ceil(missing / output_amount) if missing else 0
        if depth >= max_depth:
            return CraftingDependencyNode(
                unique_name,
                item_name(unique_name),
                required,
                depth,
                True,
                recipe_id=recipe_key,
                variant_index=_as_int(variant),
                output_amount=output_amount,
                batches=batches,
                silver_cost=_as_int(silver_cost),
                unit_price=unit_price,
                price_city=price_city,
                total_price=total_price,
                inventory_available=available,
                inventory_used=inventory_used,
                missing_quantity=missing,
                expansion_stopped=True,
            )
        materials = conn.execute(
            """SELECT material_uniquename, amount
               FROM recipe_materials
               WHERE recipe_id=?
               ORDER BY material_uniquename""",
            (recipe_key,),
        ).fetchall()
        children = tuple(
            expand(
                str(material_name),
                max(1, _as_int(amount)) * batches,
                depth + 1,
                ancestors | {unique_name},
            )
            for material_name, amount in materials
            if material_name and _as_int(amount) > 0 and batches > 0
        )
        return CraftingDependencyNode(
            unique_name,
            item_name(unique_name),
            required,
            depth,
            True,
            recipe_id=recipe_key,
            variant_index=_as_int(variant),
            output_amount=output_amount,
            batches=batches,
            silver_cost=_as_int(silver_cost),
            unit_price=unit_price,
            price_city=price_city,
            total_price=total_price,
            inventory_available=available,
            inventory_used=inventory_used,
            missing_quantity=missing,
            children=children,
        )

    # Owning some of the selected output does not reduce the requested craft.
    # Inventory is allocated only to its recipe dependencies.
    return expand(item_uniquename, requested, 0, frozenset(), recipe_id, False)


build_dependency_tree = build_crafting_dependency_tree


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
    LOGGER.info("Crafting import indul")
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
    LOGGER.info(
        "Crafting import kész | receptek=%s | anyagok=%s | feloldatlan=%s",
        recipes,
        materials,
        unresolved,
    )
    return recipes, materials, unresolved


if __name__ == "__main__":
    recipe_count, material_count, unresolved_count = import_crafting_data()
    print(
        f"Recipes: {recipe_count}, materials: {material_count}, "
        f"unresolved items: {unresolved_count}"
    )
