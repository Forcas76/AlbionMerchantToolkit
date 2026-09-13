"""Canonical project paths shared by services, scripts and user interfaces."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DB_FILE = DATA_DIR / "albion.db"
ITEMS_FILE = PROJECT_ROOT / "items.json"
LOCALIZATION_FILE = PROJECT_ROOT / "localization.json"
