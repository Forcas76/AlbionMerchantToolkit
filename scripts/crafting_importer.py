"""Compatibility entry point for crafting imports."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.crafting import *


if __name__ == "__main__":
    from app.services.crafting import import_crafting_data

    recipe_count, material_count, unresolved_count = import_crafting_data()
    print(
        f"Recipes: {recipe_count}, materials: {material_count}, "
        f"unresolved items: {unresolved_count}"
    )
