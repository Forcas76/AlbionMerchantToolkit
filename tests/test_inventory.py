from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.core.database import connect_database
from app.services.inventory import (
    allocate_inventory,
    get_quantity,
    quantity_from_stacks,
    set_quantity,
    split_stacks,
)


class InventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.conn = connect_database(root / "catalog.db", root / "market.db", root / "user.db")

    def tearDown(self) -> None:
        self.conn.close()
        self.temp.cleanup()

    def test_stack_conversion(self) -> None:
        self.assertEqual(quantity_from_stacks(1, 0), 999)
        self.assertEqual(quantity_from_stacks(2, 17), 2015)
        self.assertEqual(split_stacks(2015), (2, 17))
        with self.assertRaises(ValueError):
            quantity_from_stacks(1, 999)

    def test_inventory_persistence_and_removal(self) -> None:
        set_quantity(self.conn, "T4_LEATHER", 1234)
        self.assertEqual(get_quantity(self.conn, "T4_LEATHER"), 1234)
        set_quantity(self.conn, "T4_LEATHER", 50, quality=3)
        self.assertEqual(get_quantity(self.conn, "T4_LEATHER", quality=3), 50)
        self.assertEqual(get_quantity(self.conn, "T4_LEATHER"), 1284)
        set_quantity(self.conn, "T4_LEATHER", 0)
        self.assertEqual(get_quantity(self.conn, "T4_LEATHER"), 50)

    def test_allocation_is_pure(self) -> None:
        allocation = allocate_inventory(1200, 999)
        self.assertEqual((allocation.used, allocation.missing, allocation.remaining), (999, 201, 0))
