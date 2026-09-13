"""Split the legacy monolithic database into catalogue, market and user stores."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import (
    apply_catalog_migrations,
    apply_market_migrations,
    apply_user_migrations,
)
from app.domain.item_identity import normalize_item_identity
from app.paths import CATALOG_DB_FILE, LEGACY_DB_FILE, MARKET_DB_FILE, USER_DB_FILE

CATALOG_TABLES = ("items", "item_categories", "recipes", "recipe_materials")
MARKET_TABLES = ("market_prices", "market_history", "sync_runs")


def _table_exists(conn: sqlite3.Connection, name: str, schema: str = "main") -> bool:
    return conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _count(conn: sqlite3.Connection, table: str, schema: str = "main") -> int:
    if not _table_exists(conn, table, schema):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {schema}.{table}").fetchone()[0])


def _safe_temp(path: Path) -> Path:
    return path.with_name(path.name + ".rebuild")


def _prepare_catalog(source: Path, target: Path) -> dict[str, int]:
    temp = _safe_temp(target)
    temp.unlink(missing_ok=True)
    with closing(sqlite3.connect(source)) as source_conn, closing(
        sqlite3.connect(temp)
    ) as target_conn:
        with target_conn:
            source_conn.backup(target_conn)
    with closing(sqlite3.connect(temp)) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        for view in ("best_purchase_prices", "best_sale_prices"):
            conn.execute(f"DROP VIEW IF EXISTS {view}")
        for table in (*MARKET_TABLES, "schema_migrations"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        apply_catalog_migrations(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
        category_column = "shopsubcategory" if "shopsubcategory" in columns else None
        rows = conn.execute(
            "SELECT id, uniquename, enchantment, shopcategory, "
            + ("shopsubcategory" if category_column else "NULL")
            + " FROM items"
        ).fetchall()
        for item_id, uniquename, enchantment, category, subcategory in rows:
            identity = normalize_item_identity(
                uniquename,
                enchantment if enchantment else None,
                category,
                subcategory,
            )
            conn.execute(
                "UPDATE items SET market_id=?, enchantment=? WHERE id=?",
                (identity.market_id, identity.enchantment, item_id),
            )
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Catalogue integrity check failed: {integrity}")
        counts = {table: _count(conn, table) for table in CATALOG_TABLES}
        conn.execute("VACUUM")
    os.replace(temp, target)
    return counts


def _prepare_market(source: Path, target: Path) -> dict[str, int]:
    temp = _safe_temp(target)
    temp.unlink(missing_ok=True)
    with closing(sqlite3.connect(temp)) as conn:
        apply_market_migrations(conn)
        conn.execute("ATTACH DATABASE ? AS legacy", (str(source),))
        for table in MARKET_TABLES:
            if not _table_exists(conn, table, "legacy"):
                continue
            columns = [row[1] for row in conn.execute(f"PRAGMA main.table_info({table})")]
            legacy_columns = {
                row[1] for row in conn.execute(f"PRAGMA legacy.table_info({table})")
            }
            common = [column for column in columns if column in legacy_columns]
            names = ", ".join(common)
            conn.execute(
                f"INSERT INTO main.{table} ({names}) SELECT {names} FROM legacy.{table}"
            )
        conn.commit()
        conn.execute("DETACH DATABASE legacy")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Market integrity check failed: {integrity}")
        counts = {table: _count(conn, table) for table in MARKET_TABLES}
        conn.execute("VACUUM")
    os.replace(temp, target)
    return counts


def _prepare_user(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(target)) as conn:
        with conn:
            apply_user_migrations(conn)


def split_database(source: Path = LEGACY_DB_FILE) -> tuple[dict[str, int], dict[str, int]]:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Legacy database not found: {source}")
    CATALOG_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as conn:
        expected = {
            table: _count(conn, table) for table in (*CATALOG_TABLES, *MARKET_TABLES)
        }
    catalog_counts = _prepare_catalog(source, CATALOG_DB_FILE)
    market_counts = _prepare_market(source, MARKET_DB_FILE)
    _prepare_user(USER_DB_FILE)
    actual = {**catalog_counts, **market_counts}
    mismatches = {
        table: (expected[table], actual.get(table, 0))
        for table in expected
        if expected[table] != actual.get(table, 0)
    }
    if mismatches:
        raise RuntimeError(f"Row-count verification failed: {mismatches}")
    return catalog_counts, market_counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=LEGACY_DB_FILE)
    args = parser.parse_args()
    catalog, market = split_database(args.source)
    print(f"Catalogue: {catalog}")
    print(f"Market: {market}")
    print(f"Created: {CATALOG_DB_FILE}, {MARKET_DB_FILE}, {USER_DB_FILE}")


if __name__ == "__main__":
    main()
