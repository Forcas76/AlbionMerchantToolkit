"""Trade-order journal, inventory settlement and closed-order statistics."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.game_rules import market_fee_policy

ORDER_STATUSES = ("draft", "active", "closed")
LOGGER = logging.getLogger(__name__)


@contextmanager
def _transaction(conn: sqlite3.Connection):
    """Atomic savepoint that does not close ManagedConnection on exit."""

    conn.execute("SAVEPOINT trade_order_transaction")
    try:
        yield
    except Exception:
        conn.execute("ROLLBACK TO trade_order_transaction")
        conn.execute("RELEASE trade_order_transaction")
        LOGGER.debug("Order tranzakció visszavonva", exc_info=True)
        raise
    else:
        conn.execute("RELEASE trade_order_transaction")


@dataclass(frozen=True)
class OrderMaterialInput:
    item_uniquename: str
    quality: int
    purchased_quantity: int
    inventory_quantity: int
    purchased_unit_price: int
    purchase_city: str


@dataclass(frozen=True)
class TradeOrderInput:
    name: str
    output_item_uniquename: str
    output_quality: int
    output_quantity: int
    sold_quantity: int
    planned_unit_price: int
    actual_unit_price: int | None
    sale_city: str
    sale_method: str
    premium: bool
    materials: tuple[OrderMaterialInput, ...] = ()


def _validate_order(value: TradeOrderInput) -> None:
    if not value.name.strip() or not value.output_item_uniquename.strip():
        raise ValueError("Az Order neve és az eladott item kötelező.")
    if value.output_quality not in range(1, 6):
        raise ValueError("Az output quality értéke 1 és 5 közötti lehet.")
    if value.output_quantity <= 0:
        raise ValueError("Az eladási mennyiségnek pozitívnak kell lennie.")
    if not 0 <= value.sold_quantity <= value.output_quantity:
        raise ValueError("Az eladott mennyiség nem lehet nagyobb a teljes mennyiségnél.")
    if value.planned_unit_price < 0 or (value.actual_unit_price or 0) < 0:
        raise ValueError("Az ár nem lehet negatív.")
    if value.sale_method not in ("sell_order", "instant"):
        raise ValueError("Ismeretlen eladási mód.")
    for material in value.materials:
        if not material.item_uniquename.strip() or not material.purchase_city:
            raise ValueError("Minden anyagsorhoz item és beszerzési város kell.")
        if material.quality not in range(1, 6):
            raise ValueError("Az alapanyag quality értéke 1 és 5 közötti lehet.")
        if material.purchased_quantity < 0 or material.inventory_quantity < 0:
            raise ValueError("Az alapanyag-mennyiség nem lehet negatív.")
        if material.purchased_quantity + material.inventory_quantity <= 0:
            raise ValueError("Az üres alapanyagsor nem menthető.")
        if material.purchased_unit_price < 0:
            raise ValueError("A beszerzési ár nem lehet negatív.")


def create_order(conn: sqlite3.Connection, value: TradeOrderInput) -> int:
    _validate_order(value)
    with _transaction(conn):
        cursor = conn.execute(
            """INSERT INTO user.trade_orders(
                   name,status,output_item_uniquename,output_quality,output_quantity,
                   sold_quantity,planned_unit_price,actual_unit_price,sale_city,
                   sale_method,premium
               ) VALUES (?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                value.name.strip(), value.output_item_uniquename,
                value.output_quality, value.output_quantity, value.sold_quantity,
                value.planned_unit_price, value.actual_unit_price, value.sale_city,
                value.sale_method, int(value.premium),
            ),
        )
        order_id = int(cursor.lastrowid)
        _replace_materials(conn, order_id, value.materials)
    LOGGER.info(
        "Order létrehozva | id=%s | item=%s | quantity=%s",
        order_id,
        value.output_item_uniquename,
        value.output_quantity,
    )
    return order_id


def update_order(conn: sqlite3.Connection, order_id: int, value: TradeOrderInput) -> None:
    _validate_order(value)
    row = conn.execute(
        "SELECT status FROM user.trade_orders WHERE id=?", (order_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Az Order nem található.")
    if row[0] == "closed":
        raise ValueError("Lezárt Order nem szerkeszthető.")
    with _transaction(conn):
        conn.execute(
            """UPDATE user.trade_orders SET
                   name=?, output_item_uniquename=?, output_quality=?,
                   output_quantity=?, sold_quantity=?, planned_unit_price=?,
                   actual_unit_price=?, sale_city=?, sale_method=?, premium=?,
                   updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (
                value.name.strip(), value.output_item_uniquename,
                value.output_quality, value.output_quantity, value.sold_quantity,
                value.planned_unit_price, value.actual_unit_price, value.sale_city,
                value.sale_method, int(value.premium), order_id,
            ),
        )
        _replace_materials(conn, order_id, value.materials)
    LOGGER.info("Order módosítva | id=%s | item=%s", order_id, value.output_item_uniquename)


def _replace_materials(
    conn: sqlite3.Connection, order_id: int, materials: tuple[OrderMaterialInput, ...]
) -> None:
    conn.execute("DELETE FROM user.trade_order_materials WHERE order_id=?", (order_id,))
    conn.executemany(
        """INSERT INTO user.trade_order_materials(
               order_id,item_uniquename,quality,purchased_quantity,
               inventory_quantity,purchased_unit_price,purchase_city
           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                order_id, material.item_uniquename, material.quality,
                material.purchased_quantity, material.inventory_quantity,
                material.purchased_unit_price, material.purchase_city,
            )
            for material in materials
        ],
    )


def list_orders(conn: sqlite3.Connection, status: str | None = None) -> list[tuple]:
    parameters: list[object] = []
    where = ""
    if status in ORDER_STATUSES:
        where = "WHERE o.status=?"
        parameters.append(status)
    return conn.execute(
        f"""SELECT o.id,o.name,o.status,o.output_item_uniquename,
                   COALESCE(i.name_en,o.output_item_uniquename),o.output_quality,
                   o.output_quantity,o.sold_quantity,o.planned_unit_price,
                   o.actual_unit_price,o.sale_city,o.sale_method,o.premium,
                   o.created_at,o.activated_at,o.closed_at
            FROM user.trade_orders o
            LEFT JOIN items i ON i.uniquename=o.output_item_uniquename
            {where}
            ORDER BY CASE o.status WHEN 'active' THEN 0 WHEN 'draft' THEN 1 ELSE 2 END,
                     COALESCE(o.closed_at,o.updated_at) DESC, o.id DESC""",
        parameters,
    ).fetchall()


def get_order(conn: sqlite3.Connection, order_id: int) -> tuple[tuple, list[tuple]]:
    order = conn.execute(
        """SELECT id,name,status,output_item_uniquename,output_quality,
                  output_quantity,sold_quantity,planned_unit_price,actual_unit_price,
                  sale_city,sale_method,premium,created_at,activated_at,closed_at
           FROM user.trade_orders WHERE id=?""",
        (order_id,),
    ).fetchone()
    if order is None:
        raise ValueError("Az Order nem található.")
    materials = conn.execute(
        """SELECT m.id,m.item_uniquename,COALESCE(i.name_en,m.item_uniquename),
                  m.quality,m.purchased_quantity,m.inventory_quantity,
                  m.purchased_unit_price,m.purchase_city
           FROM user.trade_order_materials m
           LEFT JOIN items i ON i.uniquename=m.item_uniquename
           WHERE m.order_id=? ORDER BY m.id""",
        (order_id,),
    ).fetchall()
    return tuple(order), list(materials)


def activate_order(conn: sqlite3.Connection, order_id: int) -> None:
    with _transaction(conn):
        cursor = conn.execute(
            """UPDATE user.trade_orders
               SET status='active',activated_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='draft'""",
            (order_id,),
        )
        if cursor.rowcount != 1:
            raise ValueError("Csak tervezett Order aktiválható.")
    LOGGER.info("Order aktiválva | id=%s", order_id)


def close_order(conn: sqlite3.Connection, order_id: int) -> None:
    order, materials = get_order(conn, order_id)
    if order[2] != "active":
        raise ValueError("Csak aktív Order zárható le.")
    if int(order[6]) != int(order[5]):
        raise ValueError("Lezáráshoz az összes darabot eladottként kell rögzíteni.")
    if order[8] is None:
        raise ValueError("Lezáráshoz add meg a tényleges eladási darabárat.")
    required: dict[tuple[str, int], int] = {}
    for material in materials:
        key = (str(material[1]), int(material[3]))
        required[key] = required.get(key, 0) + int(material[5])
    with _transaction(conn):
        for (item_id, quality), quantity in required.items():
            available_row = conn.execute(
                """SELECT quantity FROM user.inventory
                   WHERE item_uniquename=? AND quality=?""",
                (item_id, quality),
            ).fetchone()
            available = int(available_row[0]) if available_row else 0
            if available < quantity:
                raise ValueError(
                    f"Nincs elég inventory: {item_id} Q{quality} "
                    f"({available:,}/{quantity:,} db)."
                )
        for (item_id, quality), quantity in required.items():
            conn.execute(
                """UPDATE user.inventory SET quantity=quantity-?,updated_at=CURRENT_TIMESTAMP
                   WHERE item_uniquename=? AND quality=?""",
                (quantity, item_id, quality),
            )
            conn.execute(
                "DELETE FROM user.inventory WHERE item_uniquename=? AND quality=? AND quantity=0",
                (item_id, quality),
            )
        conn.execute(
            """UPDATE user.trade_orders
               SET status='closed',closed_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND status='active'""",
            (order_id,),
        )
    LOGGER.info(
        "Order lezárva | id=%s | inventory_tételek=%s", order_id, len(required)
    )


def delete_order(conn: sqlite3.Connection, order_id: int) -> None:
    with _transaction(conn):
        cursor = conn.execute(
            "DELETE FROM user.trade_orders WHERE id=? AND status!='closed'", (order_id,)
        )
        if cursor.rowcount != 1:
            raise ValueError("Lezárt Order nem törölhető.")
    LOGGER.info("Order törölve | id=%s", order_id)


def order_financials(order: tuple, materials: list[tuple]) -> dict[str, int]:
    quantity = int(order[5])
    actual_price = int(order[8] or 0)
    gross = actual_price * quantity
    fees = market_fee_policy(bool(order[11]))
    tax = fees.transaction_tax(gross)
    setup = fees.setup_fee(gross) if order[10] == "sell_order" else 0
    purchase_cost = sum(int(row[4]) * int(row[6]) for row in materials)
    return {
        "gross": gross,
        "transaction_tax": tax,
        "setup_fee": setup,
        "purchase_cost": purchase_cost,
        "net_profit": gross - tax - setup - purchase_cost,
    }


def available_months(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row[0]) for row in conn.execute(
            """SELECT DISTINCT substr(closed_at,1,7) month
               FROM user.trade_orders WHERE status='closed' AND closed_at IS NOT NULL
               ORDER BY month DESC"""
        )
    ]


def closed_order_statistics(
    conn: sqlite3.Connection, month: str | None = None
) -> list[dict[str, object]]:
    rows = list_orders(conn, "closed")
    result: list[dict[str, object]] = []
    for row in rows:
        if month and str(row[15] or "")[:7] != month:
            continue
        order, materials = get_order(conn, int(row[0]))
        result.append({
            "id": int(order[0]), "name": str(order[1]),
            "item_id": str(order[3]), "quality": int(order[4]),
            "quantity": int(order[5]), "city": str(order[9]),
            "closed_at": str(order[14]), **order_financials(order, materials),
        })
    return result


def aggregate_order_timeline(
    rows: list[dict[str, object]], interval: str = "month"
) -> list[dict[str, object]]:
    """Group closed-order financials into chronological chart buckets."""

    if interval not in {"day", "week", "month"}:
        raise ValueError("Az idővonal bontása csak nap, hét vagy hónap lehet.")
    grouped: dict[str, dict[str, object]] = {}
    for row in rows:
        try:
            moment = datetime.fromisoformat(str(row.get("closed_at") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if interval == "day":
            key = moment.strftime("%Y-%m-%d")
            label = moment.strftime("%m.%d")
        elif interval == "week":
            monday = (moment - timedelta(days=moment.weekday())).date()
            key = monday.isoformat()
            label = f"{monday.month:02d}.{monday.day:02d}. hét"
        else:
            key = moment.strftime("%Y-%m")
            label = moment.strftime("%Y.%m")
        target = grouped.setdefault(key, {
            "key": key, "label": label, "gross": 0, "cost": 0,
            "profit": 0, "orders": 0,
        })
        target["gross"] = int(target["gross"]) + int(row["gross"])
        target["cost"] = int(target["cost"]) + (
            int(row["purchase_cost"]) + int(row["transaction_tax"]) + int(row["setup_fee"])
        )
        target["profit"] = int(target["profit"]) + int(row["net_profit"])
        target["orders"] = int(target["orders"]) + 1
    return [grouped[key] for key in sorted(grouped)]


def aggregate_order_profit(
    rows: list[dict[str, object]], field: str
) -> list[tuple[str, int]]:
    """Rank net profit by item (quality-aware) or sale city."""

    if field not in {"item", "city"}:
        raise ValueError("A profit csak item vagy város szerint csoportosítható.")
    totals: dict[str, int] = {}
    for row in rows:
        label = (
            f"{row['item_id']} · Q{row['quality']}"
            if field == "item"
            else str(row["city"])
        )
        totals[label] = totals.get(label, 0) + int(row["net_profit"])
    return sorted(totals.items(), key=lambda pair: pair[1], reverse=True)
