"""Persistent crafting preferences used by the graphical interface."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from app.paths import DATA_DIR, SOURCE_ROOT

SETTINGS_FILE = DATA_DIR / "craft_settings.json"
LEGACY_SETTINGS_FILE = SOURCE_ROOT / "app" / "ui" / "settings.json"
LOGGER = logging.getLogger(__name__)


@dataclass
class CraftSettings:
    premium: bool = False
    material_source: str = "buy"
    crafting_price: int = 0


def load_craft_settings() -> CraftSettings:
    source = SETTINGS_FILE if SETTINGS_FILE.exists() else LEGACY_SETTINGS_FILE
    if not source.exists():
        return CraftSettings()
    try:
        with source.open(encoding="utf-8") as settings_file:
            data = json.load(settings_file)
    except (OSError, ValueError, TypeError):
        return CraftSettings()
    material_source = data.get("material_source", "buy")
    if material_source not in {"buy", "farm"}:
        material_source = "buy"
    return CraftSettings(
        premium=bool(data.get("premium", False)),
        material_source=material_source,
        crafting_price=max(0, int(data.get("crafting_price", 0))),
    )


def save_craft_settings(settings: CraftSettings) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{SETTINGS_FILE}.tmp")
    with temporary.open("w", encoding="utf-8") as settings_file:
        json.dump(asdict(settings), settings_file, indent=2)
    temporary.replace(SETTINGS_FILE)
    LOGGER.info(
        "Craft beállítások mentve | premium=%s | material_source=%s | crafting_price=%s",
        settings.premium,
        settings.material_source,
        settings.crafting_price,
    )
