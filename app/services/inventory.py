"""Personal inventory storage and crafting allocation helpers."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

STACK_SIZE = 999
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class InventoryAllocation:
    required: int
    available: int
    used: int
    missing: int
    remaining: int


def quantity_from_stacks(stacks: int, loose: int = 0) -> int:
    if stacks < 0:
        raise ValueError("A stackek száma nem lehet negatív.")
    if not 0 <= loose < STACK_SIZE:
        raise ValueError(f"A maradék darabszám 0 és {STACK_SIZE - 1} közötti lehet.")
    return stacks * STACK_SIZE + loose


def split_stacks(quantity: int) -> tuple[int, int]:
    if quantity < 0:
        raise ValueError("A készlet nem lehet negatív.")
    return divmod(quantity, STACK_SIZE)


def allocate_inventory(required: int, available: int) -> InventoryAllocation:
    if required < 0 or available < 0:
        raise ValueError("A mennyiségek nem lehetnek negatívak.")
    used = min(required, available)
    return InventoryAllocation(required, available, used, required - used, available - used)


def set_quantity(
    conn: sqlite3.Connection,
    item_uniquename: str,
    quantity: int,
    quality: int = 1,
) -> None:
    item_uniquename = item_uniquename.strip()
    if not item_uniquename:
        raise ValueError("Az item azonosítója kötelező.")
    if quantity < 0:
        raise ValueError("A készlet nem lehet negatív.")
    if quality not in range(1, 6):
        raise ValueError("A quality értéke 1 és 5 közötti lehet.")
    if quantity == 0:
        conn.execute(
            "DELETE FROM user.inventory WHERE item_uniquename=? AND quality=?",
            (item_uniquename, quality),
        )
    else:
        conn.execute(
            """INSERT INTO user.inventory(item_uniquename, quality, quantity)
               VALUES (?, ?, ?)
               ON CONFLICT(item_uniquename, quality) DO UPDATE SET
                   quantity=excluded.quantity,
                   updated_at=CURRENT_TIMESTAMP""",
            (item_uniquename, quality, quantity),
        )
    conn.commit()
    LOGGER.info(
        "Inventory módosítva | item=%s | quality=%s | quantity=%s",
        item_uniquename,
        quality,
        quantity,
    )


def remove_item(conn: sqlite3.Connection, item_uniquename: str, quality: int = 1) -> None:
    set_quantity(conn, item_uniquename, 0, quality)


def get_quantity(
    conn: sqlite3.Connection, item_uniquename: str, quality: int | None = None
) -> int:
    if quality is None:
        row = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM user.inventory WHERE item_uniquename=?",
            (item_uniquename,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT quantity FROM user.inventory WHERE item_uniquename=? AND quality=?",
            (item_uniquename, quality),
        ).fetchone()
    return int(row[0]) if row else 0


def inventory_quantities(
    conn: sqlite3.Connection, item_uniquenames: list[str] | tuple[str, ...]
) -> dict[str, int]:
    unique_names = list(dict.fromkeys(item_uniquenames))
    if not unique_names:
        return {}
    placeholders = ",".join("?" for _ in unique_names)
    rows = conn.execute(
        f"SELECT item_uniquename, SUM(quantity) FROM user.inventory "
        f"WHERE item_uniquename IN ({placeholders}) GROUP BY item_uniquename",
        unique_names,
    ).fetchall()
    return {str(item_id): int(quantity) for item_id, quantity in rows}


def list_inventory(conn: sqlite3.Connection, search: str = "") -> list[tuple]:
    parameters: list[object] = []
    predicate = ""
    if search.strip():
        predicate = "WHERE inv.item_uniquename LIKE ? OR i.name_en LIKE ?"
        term = f"%{search.strip()}%"
        parameters.extend((term, term))
    return conn.execute(
        f"""SELECT inv.item_uniquename,
                   COALESCE(i.name_en, inv.item_uniquename),
                   inv.quantity,
                   inv.updated_at,
                   COALESCE(i.tier, 0),
                   COALESCE(i.shopcategory, ''),
                   COALESCE(i.shopsubcategory, ''),
                   COALESCE(i.shopsubcategory2, ''),
                   COALESCE(i.shopsubcategory3, ''),
                   COALESCE(i.enchantment, 0),
                   inv.quality
            FROM user.inventory inv
            LEFT JOIN items i ON i.uniquename=inv.item_uniquename
            {predicate}
            ORDER BY COALESCE(i.name_en, inv.item_uniquename),
                     inv.item_uniquename, inv.quality""",
        parameters,
    ).fetchall()
