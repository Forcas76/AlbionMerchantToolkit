"""Canonical project paths shared by services, scripts and user interfaces."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
LEGACY_DB_FILE = DATA_DIR / "albion.db"
CATALOG_DB_FILE = DATA_DIR / "catalog.db"
MARKET_DB_FILE = DATA_DIR / "market.db"
USER_DB_FILE = DATA_DIR / "user.db"
# Compatibility alias: new code should use the explicit database constants.
DB_FILE = CATALOG_DB_FILE
ITEMS_FILE = PROJECT_ROOT / "items.json"
LOCALIZATION_FILE = PROJECT_ROOT / "localization.json"
