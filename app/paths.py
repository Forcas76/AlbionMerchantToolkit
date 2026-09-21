"""Canonical resource and writable paths for source and packaged execution."""

import os
import shutil
import sys
from pathlib import Path

PRODUCT_NAME = "Albion Merchant Toolkit"
PRODUCT_ID = "AlbionMerchantToolkit"
LEGACY_PRODUCT_ID = "AlbionPrizeShower"
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
    DATA_DIR = local_app_data / PRODUCT_ID / "data"
    LEGACY_DATA_DIR = local_app_data / LEGACY_PRODUCT_ID / "data"
else:
    DATA_DIR = SOURCE_ROOT / "data"
    LEGACY_DATA_DIR = DATA_DIR

BUNDLED_DATA_DIR = BUNDLE_ROOT / "data"
BUNDLED_CATALOG_DB_FILE = BUNDLED_DATA_DIR / "catalog.db"
BRANDING_DIR = BUNDLE_ROOT / "assets" / "branding"
APP_ICON_FILE = BRANDING_DIR / "AlbionMerchantToolkit_icon.png"
APP_LOGO_FILE = BRANDING_DIR / "AlbionMerchantToolkit.png"
LOG_DIR = DATA_DIR.parent / "logs"
LEGACY_DB_FILE = DATA_DIR / "albion.db"
CATALOG_DB_FILE = DATA_DIR / "catalog.db"
MARKET_DB_FILE = DATA_DIR / "market.db"
USER_DB_FILE = DATA_DIR / "user.db"
# Compatibility alias: new code should use the explicit database constants.
DB_FILE = CATALOG_DB_FILE
ITEMS_FILE = BUNDLE_ROOT / "items.json"
LOCALIZATION_FILE = BUNDLE_ROOT / "localization.json"


def migrate_legacy_data_directory(
    destination: Path = DATA_DIR,
    legacy: Path = LEGACY_DATA_DIR,
) -> bool:
    """Copy pre-rename user data on the first run of the renamed application."""

    destination = Path(destination)
    legacy = Path(legacy)
    if destination.resolve() == legacy.resolve() or destination.exists() or not legacy.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(legacy, destination)
    return True
