"""Small persistent UI preferences stored outside the distributable catalogue."""

from __future__ import annotations

import sqlite3


def get_int(conn: sqlite3.Connection, key: str, default: int) -> int:
    row = conn.execute("SELECT value FROM user.user_settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return default


def set_int(conn: sqlite3.Connection, key: str, value: int) -> None:
    conn.execute(
        """INSERT INTO user.user_settings(key,value) VALUES(?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP""",
        (key, str(value)),
    )
    conn.commit()
