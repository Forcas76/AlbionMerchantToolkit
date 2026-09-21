"""Canonical resource and writable paths for source and packaged execution."""

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
SOURCE_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
# Compatibility name: in a frozen build resources live below PyInstaller's
# bundle root, while a source checkout keeps its historical project root.
PROJECT_ROOT = BUNDLE_ROOT if FROZEN else SOURCE_ROOT

if FROZEN:
    local_app_data = Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    DATA_DIR = local_app_data / "AlbionPrizeShower" / "data"
else:
    DATA_DIR = SOURCE_ROOT / "data"

BUNDLED_DATA_DIR = BUNDLE_ROOT / "data"
BUNDLED_CATALOG_DB_FILE = BUNDLED_DATA_DIR / "catalog.db"
LOG_DIR = DATA_DIR.parent / "logs"
LEGACY_DB_FILE = DATA_DIR / "albion.db"
CATALOG_DB_FILE = DATA_DIR / "catalog.db"
MARKET_DB_FILE = DATA_DIR / "market.db"
USER_DB_FILE = DATA_DIR / "user.db"
# Compatibility alias: new code should use the explicit database constants.
DB_FILE = CATALOG_DB_FILE
ITEMS_FILE = BUNDLE_ROOT / "items.json"
LOCALIZATION_FILE = BUNDLE_ROOT / "localization.json"
