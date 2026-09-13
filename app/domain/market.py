"""Market-domain types and deterministic opportunity calculations.

This module deliberately has no dependency on SQLite, requests or Qt.  All
amounts are integer silver and percentage fees are expressed as basis points
(100 bps == 1%).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from enum import Enum


class MarketStrategy(str, Enum):
    INSTANT = "instant"
    RELIST = "relist"
    LOCAL_SPREAD = "local_spread"


class DataFreshness(str, Enum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    MISSING = "missing"


def parse_aodp_timestamp(value: str | None) -> datetime | None:
    """Parse an AODP timestamp as an aware UTC datetime.

    AODP uses the .NET minimum date when no observation exists.  Treat that
    value, empty strings and malformed timestamps as missing market data.
    """

    if not value or value.startswith("0001-01-01"):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_minutes(observed_at: datetime | None, now: datetime | None = None) -> float | None:
    if observed_at is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current.astimezone(timezone.utc) - observed_at).total_seconds() / 60)


def freshness_for_age(
    minutes: float | None,
    fresh_minutes: int = 15,
    stale_minutes: int = 60,
) -> DataFreshness:
    if minutes is None:
        return DataFreshness.MISSING
    if minutes <= fresh_minutes:
        return DataFreshness.FRESH
    if minutes <= stale_minutes:
        return DataFreshness.AGING
    return DataFreshness.STALE


@dataclass(frozen=True, slots=True)
class MarketQuote:
    item_id: str
    city: str
    quality: int
    enchantment: int
    sell_price_min: int | None
    sell_price_min_date: datetime | None
    buy_price_max: int | None
    buy_price_max_date: datetime | None
    weight: float = 0.0


@dataclass(frozen=True, slots=True)
class FeePolicy:
    """Configurable fees; no game rule is hidden in the calculator."""

    transaction_tax_bps: int = 0
    setup_fee_bps: int = 0
    expected_relists: Decimal = Decimal("0")
    transport_flat: int = 0
    risk_reserve_bps: int = 0

    def __post_init__(self) -> None:
        for name in ("transaction_tax_bps", "setup_fee_bps", "risk_reserve_bps"):
            if not 0 <= getattr(self, name) <= 10_000:
                raise ValueError(f"{name} must be between 0 and 10000")
        if self.expected_relists < 0 or self.transport_flat < 0:
            raise ValueError("Expected relists and transport cost cannot be negative")

    @staticmethod
    def _percentage(amount: int, basis_points: int | Decimal) -> int:
        value = Decimal(amount) * Decimal(basis_points) / Decimal(10_000)
        return int(value.quantize(Decimal("1"), rounding=ROUND_CEILING))

    def transaction_tax(self, sale_price: int) -> int:
        return self._percentage(sale_price, self.transaction_tax_bps)

    def setup_fee(self, listed_price: int, listings: Decimal = Decimal("1")) -> int:
        return self._percentage(
            listed_price,
            Decimal(self.setup_fee_bps) * listings,
        )

    def risk_reserve(self, capital: int) -> int:
        return self._percentage(capital, self.risk_reserve_bps)


@dataclass(frozen=True, slots=True)
class OpportunityRules:
    min_profit: int = 1
    min_roi_percent: Decimal = Decimal("0")
    max_age_minutes: int = 60
    fresh_minutes: int = 15
    stale_minutes: int = 60


@dataclass(frozen=True, slots=True)
class MarketOpportunity:
    strategy: MarketStrategy
    item_id: str
    quality: int
    enchantment: int
    source_city: str
    destination_city: str
    buy_price: int
    expected_sale_price: int
    transaction_tax: int
    setup_fees: int
    transport_cost: int
    risk_reserve: int
    net_profit: int
    roi_percent: Decimal
    source_age_minutes: float
    destination_age_minutes: float
    freshness: DataFreshness
    confidence: Decimal

    @property
    def capital_required(self) -> int:
        return self.buy_price + self.setup_fees + self.transport_cost


def _confidence(max_age: float, stale_minutes: int) -> Decimal:
    if stale_minutes <= 0 or max_age >= stale_minutes:
        return Decimal("0")
    result = Decimal("1") - Decimal(str(max_age)) / Decimal(stale_minutes)
    return max(Decimal("0"), result.quantize(Decimal("0.001")))


def calculate_opportunity(
    source: MarketQuote,
    destination: MarketQuote,
    strategy: MarketStrategy,
    fees: FeePolicy,
    rules: OpportunityRules,
    now: datetime | None = None,
) -> MarketOpportunity | None:
    """Calculate one conservative single-unit opportunity."""

    if (
        source.item_id != destination.item_id
        or source.quality != destination.quality
        or source.enchantment != destination.enchantment
    ):
        return None

    if strategy is MarketStrategy.INSTANT:
        if source.city == destination.city:
            return None
        buy_price = source.sell_price_min
        sale_price = destination.buy_price_max
        source_time = source.sell_price_min_date
        destination_time = destination.buy_price_max_date
        setup_fees = 0
    elif strategy is MarketStrategy.RELIST:
        if source.city == destination.city:
            return None
        buy_price = source.sell_price_min
        sale_price = (
            destination.sell_price_min - 1
            if destination.sell_price_min and destination.sell_price_min > 1
            else destination.sell_price_min
        )
        source_time = source.sell_price_min_date
        destination_time = destination.sell_price_min_date
        setup_fees = (
            fees.setup_fee(sale_price, Decimal("1") + fees.expected_relists)
            if sale_price
            else 0
        )
    elif strategy is MarketStrategy.LOCAL_SPREAD:
        if source.city != destination.city:
            return None
        buy_price = source.buy_price_max + 1 if source.buy_price_max else None
        sale_price = source.sell_price_min - 1 if source.sell_price_min and source.sell_price_min > 1 else None
        source_time = source.buy_price_max_date
        destination_time = source.sell_price_min_date
        setup_fees = (
            fees.setup_fee(buy_price) + fees.setup_fee(sale_price, Decimal("1") + fees.expected_relists)
            if buy_price and sale_price
            else 0
        )
    else:  # pragma: no cover - Enum guards normal callers
        raise ValueError(f"Unsupported market strategy: {strategy}")

    if not buy_price or not sale_price or buy_price <= 0 or sale_price <= 0:
        return None
    source_age = age_minutes(source_time, now)
    destination_age = age_minutes(destination_time, now)
    if source_age is None or destination_age is None:
        return None
    max_age = max(source_age, destination_age)
    if max_age > rules.max_age_minutes:
        return None

    tax = fees.transaction_tax(sale_price)
    risk = fees.risk_reserve(buy_price)
    transport = 0 if strategy is MarketStrategy.LOCAL_SPREAD else fees.transport_flat
    profit = sale_price - buy_price - tax - setup_fees - transport - risk
    capital = buy_price + setup_fees + transport
    if capital <= 0:
        return None
    roi = (Decimal(profit) * Decimal(100) / Decimal(capital)).quantize(Decimal("0.01"))
    if profit < rules.min_profit or roi < rules.min_roi_percent:
        return None

    return MarketOpportunity(
        strategy=strategy,
        item_id=source.item_id,
        quality=source.quality,
        enchantment=source.enchantment,
        source_city=source.city,
        destination_city=destination.city,
        buy_price=buy_price,
        expected_sale_price=sale_price,
        transaction_tax=tax,
        setup_fees=setup_fees,
        transport_cost=transport,
        risk_reserve=risk,
        net_profit=profit,
        roi_percent=roi,
        source_age_minutes=source_age,
        destination_age_minutes=destination_age,
        freshness=freshness_for_age(max_age, rules.fresh_minutes, rules.stale_minutes),
        confidence=_confidence(max_age, rules.stale_minutes),
    )
