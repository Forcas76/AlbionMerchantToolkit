from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.core.database import connect_database
from app.services.inventory import get_quantity, set_quantity
from app.services.trade_orders import (
    OrderMaterialInput,
    TradeOrderInput,
    activate_order,
    close_order,
    closed_order_statistics,
    aggregate_order_profit,
    aggregate_order_timeline,
    create_order,
    get_order,
    update_order,
)


class TradeOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.conn = connect_database(root / "catalog.db", root / "market.db", root / "user.db")
        self.conn.execute(
            "INSERT INTO items(uniquename,name_en,tier,enchantment) VALUES('T4_BAG','Bag',4,0)"
        )
        self.conn.execute(
            "INSERT INTO items(uniquename,name_en,tier,enchantment) VALUES('T4_LEATHER','Leather',4,0)"
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    @staticmethod
    def value(*, sold: int = 0, actual: int | None = None, inventory: int = 20) -> TradeOrderInput:
        return TradeOrderInput(
            "T4 Bag kör", "T4_BAG", 1, 10, sold, 2_000, actual,
            "Lymhurst", "sell_order", True,
            (OrderMaterialInput("T4_LEATHER", 1, 5, inventory, 100, "Martlock"),),
        )

    def test_close_is_atomic_deducts_inventory_and_feeds_statistics(self) -> None:
        set_quantity(self.conn, "T4_LEATHER", 30)
        order_id = create_order(self.conn, self.value())
        activate_order(self.conn, order_id)
        update_order(self.conn, order_id, self.value(sold=10, actual=2_100))
        close_order(self.conn, order_id)
        order, _materials = get_order(self.conn, order_id)
        self.assertEqual(order[2], "closed")
        self.assertEqual(get_quantity(self.conn, "T4_LEATHER", 1), 10)
        stats = closed_order_statistics(self.conn)
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["gross"], 21_000)
        self.assertEqual(stats[0]["purchase_cost"], 500)
        self.assertEqual(stats[0]["net_profit"], 19_135)

    def test_insufficient_inventory_keeps_order_active_and_stock_untouched(self) -> None:
        set_quantity(self.conn, "T4_LEATHER", 5)
        order_id = create_order(self.conn, self.value(sold=10, actual=2_100, inventory=20))
        activate_order(self.conn, order_id)
        with self.assertRaises(ValueError):
            close_order(self.conn, order_id)
        order, _materials = get_order(self.conn, order_id)
        self.assertEqual(order[2], "active")
        self.assertEqual(get_quantity(self.conn, "T4_LEATHER", 1), 5)

    def test_chart_aggregations_group_financials_and_keep_quality(self) -> None:
        rows = [
            {
                "item_id": "T4_BAG", "quality": 1, "city": "Lymhurst",
                "closed_at": "2026-09-02 10:00:00", "gross": 10_000,
                "purchase_cost": 2_000, "transaction_tax": 400,
                "setup_fee": 250, "net_profit": 7_350,
            },
            {
                "item_id": "T4_BAG", "quality": 2, "city": "Lymhurst",
                "closed_at": "2026-09-03 10:00:00", "gross": 12_000,
                "purchase_cost": 3_000, "transaction_tax": 480,
                "setup_fee": 300, "net_profit": 8_220,
            },
        ]
        timeline = aggregate_order_timeline(rows, "month")
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]["gross"], 22_000)
        self.assertEqual(timeline[0]["cost"], 6_430)
        self.assertEqual(timeline[0]["orders"], 2)
        self.assertEqual(
            aggregate_order_profit(rows, "item"),
            [("T4_BAG · Q2", 8_220), ("T4_BAG · Q1", 7_350)],
        )


if __name__ == "__main__":
    unittest.main()
