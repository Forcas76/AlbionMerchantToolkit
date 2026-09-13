from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.core.database import apply_migrations, connect_database


class MigrationTests(unittest.TestCase):
    def test_split_connection_exposes_attached_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.db"
            with closing(sqlite3.connect(catalog)) as setup:
                with setup:
                    setup.execute(
                        "CREATE TABLE items(id INTEGER PRIMARY KEY, uniquename TEXT, market_id TEXT)"
                    )
                    setup.execute("INSERT INTO items VALUES (1, 'T4_TEST', 'T4_TEST@1')")
            with closing(
                connect_database(catalog, root / "market.db", root / "user.db")
            ) as conn:
                conn.execute(
                    """INSERT INTO market_prices(
                           item_id,item_uniquename,city,quality,enchantment
                       ) VALUES(1,'T4_TEST','Lymhurst',1,1)"""
                )
                joined = conn.execute(
                    "SELECT i.uniquename, p.city FROM items i JOIN market_prices p ON p.item_id=i.id"
                ).fetchone()
                self.assertEqual(joined, ("T4_TEST", "Lymhurst"))
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM user.favorites").fetchone()[0], 0
                )

    def test_migrations_are_idempotent(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn, conn:
            self.assertEqual(apply_migrations(conn), [1, 2, 3])
            self.assertEqual(apply_migrations(conn), [])
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            self.assertTrue(
                {"schema_migrations", "market_history", "sync_runs", "item_categories"}
                <= tables
            )

    def test_old_recipe_rows_survive_variant_migration(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE items(id INTEGER PRIMARY KEY, uniquename TEXT);
                INSERT INTO items VALUES (1, 'T4_TEST'), (2, 'T4_WOOD');
                CREATE TABLE recipes(
                    id INTEGER PRIMARY KEY, item_id INTEGER UNIQUE,
                    item_uniquename TEXT UNIQUE, silver_cost INTEGER,
                    craft_time_seconds INTEGER, crafting_focus INTEGER,
                    swap_transaction INTEGER, imported_at TEXT
                );
                INSERT INTO recipes VALUES (7, 1, 'T4_TEST', 10, 2, 3, 0, 'now');
                CREATE TABLE recipe_materials(
                    recipe_id INTEGER, material_item_id INTEGER,
                    material_uniquename TEXT, amount INTEGER,
                    PRIMARY KEY(recipe_id, material_uniquename)
                );
                INSERT INTO recipe_materials VALUES (7, 2, 'T4_WOOD', 8);
                """
            )
            self.assertEqual(apply_migrations(conn), [1, 2, 3])
            self.assertEqual(
                conn.execute(
                    "SELECT item_uniquename, variant_index, output_amount FROM recipes"
                ).fetchone(),
                ("T4_TEST", 0, 1),
            )
            self.assertEqual(
                conn.execute(
                    "SELECT material_uniquename, amount, returnable FROM recipe_materials"
                ).fetchone(),
                ("T4_WOOD", 8, 1),
            )


if __name__ == "__main__":
    unittest.main()
