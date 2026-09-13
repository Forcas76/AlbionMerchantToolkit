from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain.market import (
    DataFreshness,
    FeePolicy,
    MarketQuote,
    MarketStrategy,
    OpportunityRules,
    calculate_opportunity,
    freshness_for_age,
    parse_aodp_timestamp,
)


NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def quote(city: str, sell: int = 1000, buy: int = 900, age: int = 5) -> MarketQuote:
    observed = NOW - timedelta(minutes=age)
    return MarketQuote("T4_BAG", city, 1, 0, sell, observed, buy, observed, 1.5)


class TimestampTests(unittest.TestCase):
    def test_parses_z_and_naive_as_utc(self) -> None:
        self.assertEqual(parse_aodp_timestamp("2026-09-12T10:00:00Z").tzinfo, timezone.utc)
        self.assertEqual(parse_aodp_timestamp("2026-09-12T10:00:00").tzinfo, timezone.utc)

    def test_dotnet_minimum_and_invalid_are_missing(self) -> None:
        self.assertIsNone(parse_aodp_timestamp("0001-01-01T00:00:00"))
        self.assertIsNone(parse_aodp_timestamp("not-a-date"))

    def test_freshness_boundaries(self) -> None:
        self.assertEqual(freshness_for_age(None), DataFreshness.MISSING)
        self.assertEqual(freshness_for_age(15), DataFreshness.FRESH)
        self.assertEqual(freshness_for_age(16), DataFreshness.AGING)
        self.assertEqual(freshness_for_age(61), DataFreshness.STALE)


class FeePolicyTests(unittest.TestCase):
    def test_percentage_fees_round_up_conservatively(self) -> None:
        fees = FeePolicy(transaction_tax_bps=250, setup_fee_bps=100)
        self.assertEqual(fees.transaction_tax(101), 3)
        self.assertEqual(fees.setup_fee(101), 2)

    def test_invalid_fee_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            FeePolicy(transaction_tax_bps=10_001)


class OpportunityTests(unittest.TestCase):
    def test_instant_trade_is_net_of_tax_transport_and_risk(self) -> None:
        result = calculate_opportunity(
            quote("Bridgewatch", sell=1000),
            quote("Martlock", buy=1500),
            MarketStrategy.INSTANT,
            FeePolicy(transaction_tax_bps=400, transport_flat=50, risk_reserve_bps=100),
            OpportunityRules(),
            NOW,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.net_profit, 380)  # 1500 - 1000 - 60 - 50 - 10
        self.assertEqual(result.roi_percent, Decimal("36.19"))
        self.assertEqual(result.freshness, DataFreshness.FRESH)

    def test_stale_quote_is_not_an_opportunity(self) -> None:
        result = calculate_opportunity(
            quote("Bridgewatch", age=61),
            quote("Martlock", buy=2000),
            MarketStrategy.INSTANT,
            FeePolicy(),
            OpportunityRules(max_age_minutes=60),
            NOW,
        )
        self.assertIsNone(result)

    def test_relist_includes_listing_and_expected_relist_fees(self) -> None:
        result = calculate_opportunity(
            quote("Bridgewatch", sell=1000),
            quote("Martlock", sell=1501),
            MarketStrategy.RELIST,
            FeePolicy(transaction_tax_bps=400, setup_fee_bps=250, expected_relists=Decimal("1")),
            OpportunityRules(),
            NOW,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.expected_sale_price, 1500)
        self.assertEqual(result.setup_fees, 75)
        self.assertEqual(result.net_profit, 365)

    def test_local_spread_uses_order_prices_and_both_setup_fees(self) -> None:
        local = quote("Lymhurst", sell=1200, buy=800)
        result = calculate_opportunity(
            local,
            local,
            MarketStrategy.LOCAL_SPREAD,
            FeePolicy(transaction_tax_bps=400, setup_fee_bps=100),
            OpportunityRules(),
            NOW,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.buy_price, 801)
        self.assertEqual(result.expected_sale_price, 1199)
        self.assertEqual(result.setup_fees, 21)
        self.assertEqual(result.net_profit, 329)

    def test_different_quality_cannot_be_paired(self) -> None:
        other = MarketQuote("T4_BAG", "Martlock", 2, 0, 1000, NOW, 2000, NOW)
        self.assertIsNone(calculate_opportunity(
            quote("Bridgewatch"), other, MarketStrategy.INSTANT,
            FeePolicy(), OpportunityRules(), NOW,
        ))


if __name__ == "__main__":
    unittest.main()
