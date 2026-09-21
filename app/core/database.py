"""SQLite connection and forward-only schema migration helpers."""

from __future__ import annotations

import logging
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from app.paths import (
    BUNDLED_CATALOG_DB_FILE,
    CATALOG_DB_FILE,
    MARKET_DB_FILE,
    USER_DB_FILE,
    migrate_legacy_data_directory,
)

LOGGER = logging.getLogger(__name__)

Migration = tuple[int, str, Callable[[sqlite3.Connection], None]]


class ManagedConnection(sqlite3.Connection):
    """SQLite connection that also closes when used as a context manager."""

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _ensure_catalog_base_schema(conn: sqlite3.Connection) -> None:
    """Make a brand-new catalogue database safe to open before its first import."""

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uniquename TEXT NOT NULL UNIQUE,
            market_id TEXT,
            name_en TEXT,
            item_type TEXT,
            tier INTEGER,
            enchantment INTEGER DEFAULT 0,
            shopcategory TEXT,
            shopsubcategory TEXT,
            shopsubcategory2 TEXT,
            shopsubcategory3 TEXT,
            slottype TEXT,
            weight REAL,
            maxstacksize INTEGER,
            itemvalue INTEGER,
            uisprite TEXT,
            craftable INTEGER DEFAULT 0
        )
        """
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    optional_columns = {
        "market_id": "TEXT",
        "name_en": "TEXT",
        "item_type": "TEXT",
        "tier": "INTEGER",
        "enchantment": "INTEGER DEFAULT 0",
        "shopcategory": "TEXT",
        "shopsubcategory": "TEXT",
        "shopsubcategory2": "TEXT",
        "shopsubcategory3": "TEXT",
        "slottype": "TEXT",
        "weight": "REAL",
        "maxstacksize": "INTEGER",
        "itemvalue": "INTEGER",
        "uisprite": "TEXT",
        "craftable": "INTEGER DEFAULT 0",
    }
    for column, definition in optional_columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE items ADD COLUMN {column} {definition}")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_items_uniquename ON items(uniquename);
        CREATE INDEX IF NOT EXISTS idx_items_market_id ON items(market_id);
        CREATE INDEX IF NOT EXISTS idx_items_type ON items(item_type);
        CREATE INDEX IF NOT EXISTS idx_items_tier ON items(tier);
        CREATE INDEX IF NOT EXISTS idx_items_category ON items(shopcategory);
        """
    )


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


def _migration_002_inventory(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS inventory (
            item_uniquename TEXT PRIMARY KEY,
            quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_inventory_updated
            ON inventory(updated_at DESC);
        """
    )


USER_MIGRATIONS = USER_MIGRATIONS + (
    (2, "personal inventory", _migration_002_inventory),
)


def _migration_003_inventory_quality(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(inventory)")}
    if not columns or "quality" in columns:
        return
    conn.executescript(
        """
        ALTER TABLE inventory RENAME TO inventory_legacy;
        CREATE TABLE inventory (
            item_uniquename TEXT NOT NULL,
            quality INTEGER NOT NULL DEFAULT 1 CHECK(quality BETWEEN 1 AND 5),
            quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(item_uniquename, quality)
        );
        INSERT INTO inventory(item_uniquename, quality, quantity, updated_at)
        SELECT item_uniquename, 1, quantity, updated_at FROM inventory_legacy;
        DROP TABLE inventory_legacy;
        CREATE INDEX idx_inventory_updated ON inventory(updated_at DESC);
        """
    )


USER_MIGRATIONS = USER_MIGRATIONS + (
    (3, "quality-aware personal inventory", _migration_003_inventory_quality),
)


def _migration_004_trade_orders(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trade_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft', 'active', 'closed')),
            output_item_uniquename TEXT NOT NULL,
            output_quality INTEGER NOT NULL DEFAULT 1 CHECK(output_quality BETWEEN 1 AND 5),
            output_quantity INTEGER NOT NULL CHECK(output_quantity > 0),
            sold_quantity INTEGER NOT NULL DEFAULT 0 CHECK(sold_quantity >= 0),
            planned_unit_price INTEGER NOT NULL DEFAULT 0 CHECK(planned_unit_price >= 0),
            actual_unit_price INTEGER CHECK(actual_unit_price >= 0),
            sale_city TEXT NOT NULL,
            sale_method TEXT NOT NULL DEFAULT 'sell_order'
                CHECK(sale_method IN ('sell_order', 'instant')),
            premium INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            activated_at TEXT,
            closed_at TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_trade_orders_status_date
            ON trade_orders(status, closed_at DESC, created_at DESC);

        CREATE TABLE IF NOT EXISTS trade_order_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES trade_orders(id) ON DELETE CASCADE,
            item_uniquename TEXT NOT NULL,
            quality INTEGER NOT NULL DEFAULT 1 CHECK(quality BETWEEN 1 AND 5),
            purchased_quantity INTEGER NOT NULL DEFAULT 0 CHECK(purchased_quantity >= 0),
            inventory_quantity INTEGER NOT NULL DEFAULT 0 CHECK(inventory_quantity >= 0),
            purchased_unit_price INTEGER NOT NULL DEFAULT 0 CHECK(purchased_unit_price >= 0),
            purchase_city TEXT NOT NULL,
            CHECK(purchased_quantity > 0 OR inventory_quantity > 0)
        );
        CREATE INDEX IF NOT EXISTS idx_trade_order_materials_order
            ON trade_order_materials(order_id);
        """
    )


USER_MIGRATIONS = USER_MIGRATIONS + (
    (4, "trade order journal", _migration_004_trade_orders),
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
    if catalog.resolve() == Path(CATALOG_DB_FILE).resolve():
        migrate_legacy_data_directory()
    market = Path(market_path)
    user = Path(user_path)
    for db_path in (catalog, market, user):
        db_path.parent.mkdir(parents=True, exist_ok=True)

    # Installed builds keep the shipped catalogue read-only beside the
    # executable and copy it into the current user's writable AppData on the
    # first launch. Market and personal data are always created there.
    bundled_catalog = BUNDLED_CATALOG_DB_FILE
    is_default_catalog = catalog.resolve() == Path(CATALOG_DB_FILE).resolve()
    is_separate_bundle = (
        bundled_catalog.exists() and bundled_catalog.resolve() != catalog.resolve()
    )
    bundle_is_newer = is_separate_bundle and (
        not catalog.exists()
        or bundled_catalog.stat().st_mtime_ns > catalog.stat().st_mtime_ns
    )
    if is_default_catalog and is_separate_bundle and bundle_is_newer:
        shutil.copy2(bundled_catalog, catalog)
        LOGGER.info("Beépített katalógus telepítve/frissítve: %s", catalog.name)

    with closing(sqlite3.connect(str(catalog))) as schema_conn:
        with schema_conn:
            schema_conn.execute("PRAGMA foreign_keys = ON")
            _ensure_catalog_base_schema(schema_conn)
            catalog_migrations = apply_catalog_migrations(schema_conn)
    with closing(sqlite3.connect(str(market))) as schema_conn:
        with schema_conn:
            market_migrations = apply_market_migrations(schema_conn)
    with closing(sqlite3.connect(str(user))) as schema_conn:
        with schema_conn:
            user_migrations = apply_user_migrations(schema_conn)

    if catalog_migrations or market_migrations or user_migrations:
        LOGGER.info(
            "Adatbázis-migrációk | catalog=%s market=%s user=%s",
            catalog_migrations, market_migrations, user_migrations,
        )
    LOGGER.debug("Adatbázis megnyitása: %s", catalog.name)

    conn = sqlite3.connect(str(catalog), factory=ManagedConnection)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("ATTACH DATABASE ? AS market", (str(market),))
    conn.execute("ATTACH DATABASE ? AS user", (str(user),))
    return conn
