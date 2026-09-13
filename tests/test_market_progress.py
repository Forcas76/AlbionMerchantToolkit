from __future__ import annotations

import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

from app.services.market_api import do_fetch


class MarketProgressTests(unittest.TestCase):
    def test_full_fetch_reports_chunk_progress(self) -> None:
        events: list[tuple[int, int, str]] = []
        with closing(sqlite3.connect(":memory:")) as conn, conn:
            conn.execute("CREATE TABLE items(uniquename TEXT)")
            conn.executemany("INSERT INTO items VALUES (?)", [("A",), ("B",)])
            with (
                patch("app.services.market_api.chunk_ids", return_value=[["A"], ["B"]]),
                patch("app.services.market_api.fetch_prices_for_chunk", return_value=[]),
                patch("app.services.market_api.save_prices", return_value=0),
            ):
                do_fetch(conn, locations=["Caerleon"], progress_callback=lambda *args: events.append(args))
        self.assertEqual([(event[0], event[1]) for event in events], [(0, 2), (1, 2), (2, 2)])


if __name__ == "__main__":
    unittest.main()
