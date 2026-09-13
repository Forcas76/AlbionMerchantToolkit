"""Refresh category metadata while preserving downloaded market prices."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import connect_database
from app.services.item_catalog import import_item_categories


def main() -> None:
    with connect_database() as conn:
        category_count, item_count = import_item_categories(
            conn, PROJECT_ROOT / "items.json"
        )
    print(
        f"Kész: {category_count} kategóriacsomópont és "
        f"{item_count} item frissítve. A piaci adatok megmaradtak."
    )


if __name__ == "__main__":
    main()
