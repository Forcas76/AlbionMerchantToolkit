"""SQLite connection and forward-only schema migration helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from app.services.market_api import DB_FILE

Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]


def _migration_001_market_history(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_history (
            item_uniquename TEXT NOT NULL,
            city TEXT NOT NULL,
            quality INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            avg_price INTEGER NOT NULL DEFAULT 0,
            time_scale_hours INTEGER NOT NULL,
            fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (
                item_uniquename, city, quality, timestamp, time_scale_hours
            )
        );
        CREATE INDEX IF NOT EXISTS idx_market_history_lookup
            ON market_history(item_uniquename, city, quality, timestamp);

        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            records_received INTEGER NOT NULL DEFAULT 0,
            records_saved INTEGER NOT NULL DEFAULT 0,
            error_message TEXT
        );
        """
    )


def _migration_002_recipe_variants(conn: sqlite3.Connection) -> None:
    """Allow multiple and enchanted recipes without discarding existing data."""

    recipe_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recipes'"
    ).fetchone()
    if recipe_table is None:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(recipes)")}
    if "variant_index" in columns:
        return
    conn.executescript(
        """
        CREATE TABLE recipes_new (
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
        INSERT INTO recipes_new (
            id, item_id, item_uniquename, variant_index, output_amount,
            silver_cost, craft_time_seconds, crafting_focus,
            swap_transaction, imported_at
        )
        SELECT id, item_id, item_uniquename, 0, 1, silver_cost,
               craft_time_seconds, crafting_focus, swap_transaction, imported_at
        FROM recipes;

        CREATE TABLE recipe_materials_new (
            recipe_id INTEGER NOT NULL REFERENCES recipes_new(id) ON DELETE CASCADE,
            material_item_id INTEGER REFERENCES items(id),
            material_uniquename TEXT NOT NULL,
            amount INTEGER NOT NULL,
            returnable INTEGER NOT NULL DEFAULT 1,
            max_return_amount INTEGER,
            PRIMARY KEY (recipe_id, material_uniquename)
        );
        INSERT INTO recipe_materials_new (
            recipe_id, material_item_id, material_uniquename, amount,
            returnable, max_return_amount
        )
        SELECT recipe_id, material_item_id, material_uniquename, amount, 1, NULL
        FROM recipe_materials;

        DROP TABLE recipe_materials;
        DROP TABLE recipes;
        ALTER TABLE recipes_new RENAME TO recipes;
        ALTER TABLE recipe_materials_new RENAME TO recipe_materials;
        CREATE INDEX idx_recipes_item ON recipes(item_id);
        CREATE INDEX idx_recipe_materials_item ON recipe_materials(material_item_id);
        """
    )


def _migration_003_item_category_tree(conn: sqlite3.Connection) -> None:
    """Store every shop-category level and its path-safe hierarchy."""

    item_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'"
    ).fetchone()
    if item_table is not None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
        for column in (
            "shopcategory",
            "shopsubcategory",
            "shopsubcategory2",
            "shopsubcategory3",
        ):
            if column not in columns:
                conn.execute(f"ALTER TABLE items ADD COLUMN {column} TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_category_path "
            "ON items(shopcategory, shopsubcategory, shopsubcategory2, shopsubcategory3)"
        )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS item_categories (
            path TEXT PRIMARY KEY,
            category_id TEXT NOT NULL,
            level INTEGER NOT NULL,
            parent_path TEXT REFERENCES item_categories(path),
            sort_order INTEGER NOT NULL DEFAULT 0,
            hidden INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_item_categories_parent
            ON item_categories(parent_path, sort_order);
        """
    )


MIGRATIONS: tuple[Migration, ...] = (
    (1, "market history and sync audit", _migration_001_market_history),
    (2, "recipe variants and returnable materials", _migration_002_recipe_variants),
    (3, "item category hierarchy", _migration_003_item_category_tree),
)


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version INTEGER PRIMARY KEY,
               description TEXT NOT NULL,
               applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    completed: list[int] = []
    for version, description, migration in MIGRATIONS:
        if version in applied:
            continue
        with conn:
            migration(conn)
            conn.execute(
                "INSERT INTO schema_migrations(version, description) VALUES (?, ?)",
                (version, description),
            )
        completed.append(version)
    return completed


def connect_database(path: str | Path = DB_FILE) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    apply_migrations(conn)
    return conn
