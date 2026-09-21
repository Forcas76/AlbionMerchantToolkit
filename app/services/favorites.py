"""Manual, context-specific item favorites stored in the private user database."""

from __future__ import annotations

import logging
import sqlite3
from typing import Literal

FavoriteContext = Literal["price", "crafting", "flip"]
VALID_CONTEXTS = frozenset(("price", "crafting", "flip"))
LOGGER = logging.getLogger(__name__)


def _validate_context(context: str) -> None:
    if context not in VALID_CONTEXTS:
        raise ValueError(f"Unknown favorite context: {context}")


def is_favorite(conn: sqlite3.Connection, context: FavoriteContext, item_id: str) -> bool:
    _validate_context(context)
    return conn.execute(
        "SELECT 1 FROM user.favorites WHERE context=? AND item_uniquename=?",
        (context, item_id),
    ).fetchone() is not None


def set_favorite(
    conn: sqlite3.Connection,
    context: FavoriteContext,
    item_id: str,
    enabled: bool,
) -> None:
    _validate_context(context)
    if enabled:
        conn.execute(
            "INSERT OR IGNORE INTO user.favorites(context,item_uniquename) VALUES(?,?)",
            (context, item_id),
        )
    else:
        conn.execute(
            "DELETE FROM user.favorites WHERE context=? AND item_uniquename=?",
            (context, item_id),
        )
    conn.commit()
    LOGGER.info(
        "Kedvenc módosítva | context=%s | item=%s | enabled=%s",
        context,
        item_id,
        enabled,
    )


def toggle_favorite(
    conn: sqlite3.Connection, context: FavoriteContext, item_id: str
) -> bool:
    enabled = not is_favorite(conn, context, item_id)
    set_favorite(conn, context, item_id, enabled)
    return enabled


def favorite_ids(conn: sqlite3.Connection, context: FavoriteContext) -> set[str]:
    _validate_context(context)
    return {
        row[0]
        for row in conn.execute(
            "SELECT item_uniquename FROM user.favorites WHERE context=?", (context,)
        )
    }


def list_favorites(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        """SELECT f.context, f.item_uniquename, i.name_en, i.tier, i.enchantment,
                  f.created_at
           FROM user.favorites f
           LEFT JOIN items i ON i.uniquename=f.item_uniquename
           ORDER BY f.context, COALESCE(i.name_en, f.item_uniquename)"""
    ).fetchall()
