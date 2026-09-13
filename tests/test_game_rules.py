from __future__ import annotations

import unittest

from app.domain.game_rules import market_fee_policy


class GameRuleTests(unittest.TestCase):
    def test_premium_market_fees_are_fixed(self) -> None:
        fees = market_fee_policy(True)
        self.assertEqual(fees.transaction_tax_bps, 400)
        self.assertEqual(fees.setup_fee_bps, 250)

    def test_non_premium_market_fees_are_double(self) -> None:
        fees = market_fee_policy(False)
        self.assertEqual(fees.transaction_tax_bps, 800)
        self.assertEqual(fees.setup_fee_bps, 500)


if __name__ == "__main__":
    unittest.main()
