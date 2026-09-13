"""Read market snapshots and rank domain-level opportunities."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime

from app.domain.market import (
    FeePolicy,
    MarketOpportunity,
    MarketQuote,
    MarketStrategy,
    OpportunityRules,
    calculate_opportunity,
    parse_aodp_timestamp,
)


def load_quotes(
    conn: sqlite3.Connection,
    item_ids: list[str] | None = None,
    cities: list[str] | None = None,
    qualities: list[int] | None = None,
) -> list[MarketQuote]:
    parameters: list[str] = []
    predicates = ["(mp.sell_price_min > 0 OR mp.buy_price_max > 0)"]
    if item_ids:
        placeholders = ",".join("?" for _ in item_ids)
        predicates.append(f"mp.item_uniquename IN ({placeholders})")
        parameters.extend(item_ids)
    if cities:
        placeholders = ",".join("?" for _ in cities)
        predicates.append(f"mp.city IN ({placeholders})")
        parameters.extend(cities)
    if qualities:
        placeholders = ",".join("?" for _ in qualities)
        predicates.append(f"mp.quality IN ({placeholders})")
        parameters.extend(qualities)
    where = "WHERE " + " AND ".join(predicates)
    rows = conn.execute(
        f"""SELECT mp.item_uniquename, mp.city, mp.quality, mp.enchantment,
                   mp.sell_price_min, mp.sell_price_min_date,
                   mp.buy_price_max, mp.buy_price_max_date,
                   COALESCE(i.weight, 0)
            FROM market_prices mp
            JOIN items i ON i.uniquename = mp.item_uniquename
            {where}
            ORDER BY mp.item_uniquename, mp.quality, mp.enchantment, mp.city""",
        parameters,
    ).fetchall()
    return [
        MarketQuote(
            item_id=row[0],
            city=row[1],
            quality=row[2],
            enchantment=row[3],
            sell_price_min=row[4],
            sell_price_min_date=parse_aodp_timestamp(row[5]),
            buy_price_max=row[6],
            buy_price_max_date=parse_aodp_timestamp(row[7]),
            weight=row[8],
        )
        for row in rows
    ]


def scan_opportunities(
    conn: sqlite3.Connection,
    strategy: MarketStrategy,
    fees: FeePolicy,
    rules: OpportunityRules,
    *,
    item_ids: list[str] | None = None,
    cities: list[str] | None = None,
    qualities: list[int] | None = None,
    limit: int = 100,
    now: datetime | None = None,
) -> list[MarketOpportunity]:
    grouped: dict[tuple[str, int, int], list[MarketQuote]] = defaultdict(list)
    for quote in load_quotes(conn, item_ids, cities, qualities):
        grouped[(quote.item_id, quote.quality, quote.enchantment)].append(quote)

    opportunities: list[MarketOpportunity] = []
    for quotes in grouped.values():
        if strategy is MarketStrategy.LOCAL_SPREAD:
            pairs = ((quote, quote) for quote in quotes)
        else:
            pairs = (
                (source, destination)
                for source in quotes
                for destination in quotes
                if source.city != destination.city
            )
        for source, destination in pairs:
            opportunity = calculate_opportunity(
                source, destination, strategy, fees, rules, now
            )
            if opportunity is not None:
                opportunities.append(opportunity)

    opportunities.sort(
        key=lambda row: (row.net_profit, row.confidence, row.roi_percent),
        reverse=True,
    )
    return opportunities[:limit]
