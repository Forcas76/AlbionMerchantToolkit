from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing

from app.core.database import apply_migrations
from app.services.history_api import _history_url, save_history


class HistoryApiTests(unittest.TestCase):
    def test_url_contains_supported_filters(self) -> None:
        url = _history_url(
            ["T4_BAG"], locations=["Caerleon"], qualities=[1, 2],
            time_scale=6, start_date="2026-09-01", end_date="2026-09-02",
        )
        self.assertIn("T4_BAG.json", url)
        self.assertIn("locations=Caerleon", url)
        self.assertIn("qualities=1%2C2", url)
        self.assertIn("time-scale=6", url)

    def test_history_upsert_is_idempotent(self) -> None:
        payload = [{
            "item_id": "T4_BAG",
            "location": "Caerleon",
            "quality": 1,
            "data": [{
                "timestamp": "2026-09-12T00:00:00",
                "item_count": 12,
                "avg_price": 1000,
            }],
        }]
        with closing(sqlite3.connect(":memory:")) as conn, conn:
            apply_migrations(conn)
            self.assertEqual(save_history(conn, payload, 24), 1)
            payload[0]["data"][0]["item_count"] = 14
            self.assertEqual(save_history(conn, payload, 24), 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*), item_count FROM market_history").fetchone(),
                (1, 14),
            )


if __name__ == "__main__":
    unittest.main()
