from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.ui.market_cards import relative_age


class MarketCardTests(unittest.TestCase):
    def test_relative_age_is_human_readable(self) -> None:
        now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(relative_age("2026-09-13T11:43:00Z", now), "17 perce")
        self.assertEqual(relative_age("2026-09-13T08:00:00Z", now), "4 órája")
        self.assertEqual(relative_age("2026-09-13T09:46:00Z", now), "2 óra 14 perce")
        self.assertEqual(relative_age("2026-09-11T12:00:00Z", now), "2 napja")
        self.assertEqual(relative_age("0001-01-01T00:00:00", now), "nincs adat")


if __name__ == "__main__":
    unittest.main()
