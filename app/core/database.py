"""SQLite connection and forward-only schema migration helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from app.paths import CATALOG_DB_FILE, MARKET_DB_FILE, USER_DB_FILE

Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]


class ManagedConnection(sqlite3.Connection):
    """SQLite connection that also closes when used as a context manager."""

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


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


def _migration_004_market_identity(conn: sqlite3.Connection) -> None:
    item_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='items'"
    ).fetchone()
    if item_table is None:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    if "market_id" not in columns:
        conn.execute("ALTER TABLE items ADD COLUMN market_id TEXT")
    conn.execute("UPDATE items SET market_id = uniquename WHERE market_id IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_market_id ON items(market_id)")


def _migration_005_refining_schema(conn: sqlite3.Connection) -> None:
    """Add the configurable refining catalogue and imported direct recipes.

    Refining is intentionally kept separate from the general crafting recipe
    graph.  A refining recipe contains its direct materials only; the service
    never recursively expands a material into another recipe.
    """

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS refining_cities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT NOT NULL UNIQUE,
            resource_type TEXT NOT NULL,
            refined_item TEXT NOT NULL,
            refined_item_id INTEGER REFERENCES items(id),
            local_production_bonus REAL NOT NULL DEFAULT 0,
            production_bonus REAL NOT NULL DEFAULT 0,
            base_return_rate REAL NOT NULL DEFAULT 0.15,
            focus_return_rate REAL NOT NULL DEFAULT 0,
            station_fee INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS refining_recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            input_item_id INTEGER REFERENCES items(id),
            input_item_uniquename TEXT,
            output_item_id INTEGER REFERENCES items(id),
            output_item_uniquename TEXT NOT NULL,
            variant_index INTEGER NOT NULL DEFAULT 0,
            input_amount INTEGER NOT NULL DEFAULT 0,
            output_amount INTEGER NOT NULL DEFAULT 1,
            silver_cost INTEGER NOT NULL DEFAULT 0,
            craft_time_seconds REAL NOT NULL DEFAULT 0,
            crafting_focus INTEGER NOT NULL DEFAULT 0,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(output_item_uniquename, variant_index)
        );
        CREATE TABLE IF NOT EXISTS refining_recipe_materials (
            recipe_id INTEGER NOT NULL REFERENCES refining_recipes(id)
                ON DELETE CASCADE,
            material_item_id INTEGER REFERENCES items(id),
            material_uniquename TEXT NOT NULL,
            amount INTEGER NOT NULL,
            returnable INTEGER NOT NULL DEFAULT 1,
            max_return_amount INTEGER,
            PRIMARY KEY(recipe_id, material_uniquename)
        );
        CREATE INDEX IF NOT EXISTS idx_refining_recipes_output
            ON refining_recipes(output_item_uniquename, variant_index);
        CREATE INDEX IF NOT EXISTS idx_refining_recipe_materials_item
            ON refining_recipe_materials(material_uniquename);
        """
    )
    # These are defaults, not hard-coded calculation rules.  Users may update
    # any column (including the rates) through the refining service.
    conn.executemany(
        """INSERT OR IGNORE INTO refining_cities(
               city, resource_type, refined_item, local_production_bonus,
               production_bonus, base_return_rate, focus_return_rate
           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            ("Fort Sterling", "wood", "planks", 0.35, 0.35, 0.15, 0.10),
            ("Lymhurst", "fiber", "cloth", 0.35, 0.35, 0.15, 0.10),
            ("Martlock", "hide", "leather", 0.35, 0.35, 0.15, 0.10),
            ("Thetford", "ore", "metalbars", 0.35, 0.35, 0.15, 0.10),
            ("Bridgewatch", "rock", "stoneblocks", 0.35, 0.35, 0.15, 0.10),
        ),
    )


CATALOG_MIGRATIONS: tuple[Migration, ...] = (
    (1, "recipe variants and returnable materials", _migration_002_recipe_variants),
    (2, "item category hierarchy", _migration_003_item_category_tree),
    (3, "separate market item identity", _migration_004_market_identity),
    (4, "configurable refining cities and direct recipes", _migration_005_refining_schema),
)


def _migration_001_market_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            item_uniquename TEXT NOT NULL,
            city TEXT NOT NULL,
            quality INTEGER NOT NULL,
            enchantment INTEGER NOT NULL DEFAULT 0,
            sell_price_min INTEGER,
            sell_price_min_date TEXT,
            sell_price_max INTEGER,
            sell_price_max_date TEXT,
            buy_price_min INTEGER,
            buy_price_min_date TEXT,
            buy_price_max INTEGER,
            buy_price_max_date TEXT,
            fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(item_id, city, quality, enchantment)
        );
        CREATE INDEX IF NOT EXISTS idx_market_item ON market_prices(item_id);
        CREATE INDEX IF NOT EXISTS idx_market_lookup
            ON market_prices(item_uniquename, enchantment, quality, city);
        CREATE INDEX IF NOT EXISTS idx_market_sell ON market_prices(sell_price_min);
        CREATE INDEX IF NOT EXISTS idx_market_buy ON market_prices(buy_price_max);
        """
    )
    _migration_001_market_history(conn)


MARKET_MIGRATIONS: tuple[Migration, ...] = (
    (1, "market prices, history and sync audit", _migration_001_market_schema),
)


def _migration_002_stable_market_key(conn: sqlite3.Connection) -> None:
    columns = [row[1] for row in conn.execute("PRAGMA table_info(market_prices)")]
    if not columns:
        return
    conn.executescript(
        """
        CREATE TABLE market_prices_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            item_uniquename TEXT NOT NULL,
            city TEXT NOT NULL,
            quality INTEGER NOT NULL,
            enchantment INTEGER NOT NULL DEFAULT 0,
            sell_price_min INTEGER, sell_price_min_date TEXT,
            sell_price_max INTEGER, sell_price_max_date TEXT,
            buy_price_min INTEGER, buy_price_min_date TEXT,
            buy_price_max INTEGER, buy_price_max_date TEXT,
            fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(item_uniquename, city, quality, enchantment)
        );
        INSERT OR REPLACE INTO market_prices_new
        SELECT * FROM market_prices;
        DROP TABLE market_prices;
        ALTER TABLE market_prices_new RENAME TO market_prices;
        CREATE INDEX idx_market_item ON market_prices(item_uniquename);
        CREATE INDEX idx_market_lookup
            ON market_prices(item_uniquename, enchantment, quality, city);
        CREATE INDEX idx_market_sell ON market_prices(sell_price_min);
        CREATE INDEX idx_market_buy ON market_prices(buy_price_max);
        """
    )


MARKET_MIGRATIONS = MARKET_MIGRATIONS + (
    (2, "stable cross-database item key", _migration_002_stable_market_key),
)


def _migration_001_user_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            context TEXT NOT NULL CHECK(context IN ('price', 'crafting', 'flip')),
            item_uniquename TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(context, item_uniquename)
        );
        CREATE INDEX IF NOT EXISTS idx_favorites_context
            ON favorites(context, created_at);
        CREATE TABLE IF NOT EXISTS user_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


USER_MIGRATIONS: tuple[Migration, ...] = (
    (1, "favorites and user interface settings", _migration_001_user_schema),
)


def _apply_migration_set(
    conn: sqlite3.Connection,
    migrations: tuple[Migration, ...],
    table_name: str,
) -> list[int]:
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {table_name} (
               version INTEGER PRIMARY KEY,
               description TEXT NOT NULL,
               applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    applied = {row[0] for row in conn.execute(f"SELECT version FROM {table_name}")}
    completed: list[int] = []
    for version, description, migration in migrations:
        if version in applied:
            continue
        with conn:
            migration(conn)
            conn.execute(
                f"INSERT INTO {table_name}(version, description) VALUES (?, ?)",
                (version, description),
            )
        completed.append(version)
    return completed


def apply_catalog_migrations(conn: sqlite3.Connection) -> list[int]:
    return _apply_migration_set(conn, CATALOG_MIGRATIONS, "catalog_schema_migrations")


def apply_market_migrations(conn: sqlite3.Connection) -> list[int]:
    return _apply_migration_set(conn, MARKET_MIGRATIONS, "market_schema_migrations")


def apply_user_migrations(conn: sqlite3.Connection) -> list[int]:
    return _apply_migration_set(conn, USER_MIGRATIONS, "user_schema_migrations")


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


def connect_database(
    path: str | Path = CATALOG_DB_FILE,
    market_path: str | Path = MARKET_DB_FILE,
    user_path: str | Path = USER_DB_FILE,
) -> sqlite3.Connection:
    """Open the catalogue and attach volatile market and private user stores."""

    catalog = Path(path)
    market = Path(market_path)
    user = Path(user_path)
    for db_path in (catalog, market, user):
        db_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(str(catalog))) as schema_conn:
        with schema_conn:
            schema_conn.execute("PRAGMA foreign_keys = ON")
            apply_catalog_migrations(schema_conn)
    with closing(sqlite3.connect(str(market))) as schema_conn:
        with schema_conn:
            apply_market_migrations(schema_conn)
    with closing(sqlite3.connect(str(user))) as schema_conn:
        with schema_conn:
            apply_user_migrations(schema_conn)

    conn = sqlite3.connect(str(catalog), factory=ManagedConnection)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("ATTACH DATABASE ? AS market", (str(market),))
    conn.execute("ATTACH DATABASE ? AS user", (str(user),))
    return conn
