"""Albion Online Data Project market-price importer.

The item table is the immutable catalogue; market_prices contains one current
snapshot per item, city, quality and enchantment combination.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

import requests

from app.paths import CATALOG_DB_FILE

LOGGER = logging.getLogger(__name__)

BASE_URL = "https://europe.albion-online-data.com/api/v2/stats/prices"
MAX_URL_LEN = 4000
REQUESTS_PER_MINUTE = 150
REQUESTS_PER_FIVE_MINUTES = 300
CITIES = (
    "Caerleon", "Bridgewatch", "Martlock", "Fort Sterling",
    "Thetford", "Lymhurst", "Brecilien", "Black Market",
)
DB_FILE = str(CATALOG_DB_FILE)


class ApiRateLimiter:
    """Rolling-window limiter for both API quotas."""

    def __init__(
        self,
        per_minute: int = REQUESTS_PER_MINUTE,
        per_five_minutes: int = REQUESTS_PER_FIVE_MINUTES,
    ) -> None:
        self.limits = ((60.0, per_minute), (300.0, per_five_minutes))
        self.calls: deque[float] = deque()

    def wait_for_slot(self) -> None:
        while True:
            now = time.monotonic()
            oldest_window = 0.0
            for window, limit in self.limits:
                while self.calls and self.calls[0] <= now - window:
                    self.calls.popleft()
                if len(self.calls) >= limit:
                    oldest_window = max(
                        oldest_window,
                        self.calls[0] + window - now,
                    )
            if oldest_window <= 0:
                self.calls.append(now)
                return
            time.sleep(oldest_window)


def _request_url(item_ids: Iterable[str], locations: Iterable[str] = CITIES) -> str:
    ids = ",".join(item_ids)
    query = urlencode({"locations": ",".join(locations)})
    return f"{BASE_URL}/{ids}.json?{query}"


def chunk_ids(
    ids: Iterable[str],
    max_len: int = MAX_URL_LEN,
    locations: Iterable[str] = CITIES,
) -> list[list[str]]:
    """Split IDs so the complete request URL never exceeds ``max_len``."""
    location_list = tuple(locations)
    chunks: list[list[str]] = []
    current: list[str] = []
    for item_id in ids:
        candidate = current + [item_id]
        if len(_request_url(candidate, location_list)) > max_len:
            if not current:
                raise ValueError(
                    f"Item ID cannot fit into a {max_len}-character URL: {item_id}"
                )
            chunks.append(current)
            current = [item_id]
            if len(_request_url(current, location_list)) > max_len:
                raise ValueError(
                    f"Item ID cannot fit into a {max_len}-character URL: {item_id}"
                )
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def fetch_item_ids(conn: sqlite3.Connection) -> list[str]:
    """Return the canonical IDs expected by the Albion Data Project."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
    identity = "COALESCE(market_id, uniquename)" if "market_id" in columns else "uniquename"
    rows = conn.execute(
        f"SELECT DISTINCT {identity} FROM items ORDER BY {identity}"
    ).fetchall()
    return [row[0] for row in rows]


def fetch_prices_for_chunk(
    item_ids: list[str],
    retries: int = 5,
    initial_wait: float = 2,
    rate_limiter: ApiRateLimiter | None = None,
    session: requests.Session | None = None,
    locations: Iterable[str] = CITIES,
) -> list[dict[str, Any]]:
    location_list = tuple(locations)
    url = _request_url(item_ids, location_list)
    if len(url) > MAX_URL_LEN:
        raise ValueError(f"Request URL is {len(url)} characters; limit is {MAX_URL_LEN}")
    limiter = rate_limiter or ApiRateLimiter()
    http = session or requests.Session()
    http.headers.update({
        "Accept-Encoding": "gzip",
        "User-Agent": "AlbionMerchantToolkit/1.0",
    })
    for attempt in range(retries):
        try:
            LOGGER.debug(
                "Piaci API kérés | itemek=%s városok=%s próbálkozás=%s/%s",
                len(item_ids), location_list, attempt + 1, retries,
            )
            limiter.wait_for_slot()
            response = http.get(url, timeout=60)
            if response.status_code == 429:
                if attempt == retries - 1:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else initial_wait * (2 ** attempt)
                time.sleep(wait)
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("The market API returned a non-list JSON payload")
            return payload
        except (requests.RequestException, ValueError):
            LOGGER.warning(
                "Piaci API kérés sikertelen | próbálkozás=%s/%s",
                attempt + 1, retries, exc_info=attempt == retries - 1,
            )
            if attempt == retries - 1:
                raise
            time.sleep(initial_wait * (2 ** attempt))
    raise RuntimeError("Unreachable retry state")


def _int_or_none(value: Any) -> int | None:
    if value in (None, "", 0):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def save_prices(conn: sqlite3.Connection, prices: Iterable[dict[str, Any]]) -> int:
    saved = 0
    fetched_at = datetime.now(timezone.utc).isoformat()
    for record in prices:
        raw_id = str(record.get("item_id", ""))
        if not raw_id:
            continue
        base_id, separator, suffix = raw_id.rpartition("@")
        if not separator or not suffix.isdigit():
            base_id, enchantment = raw_id, 0
        else:
            enchantment = int(suffix)
        city = record.get("city")
        quality = _int_or_none(record.get("quality"))
        if not city or quality is None:
            continue
        item = conn.execute(
            "SELECT id, uniquename FROM items WHERE market_id = ?", (raw_id,)
        ).fetchone()
        if item is None:
            item = conn.execute(
                "SELECT id, uniquename FROM items "
                "WHERE uniquename IN (?, ?) ORDER BY uniquename = ? DESC LIMIT 1",
                (raw_id, base_id, raw_id),
            ).fetchone()
        if item is None:
            continue
        values = (
            item[0], item[1], city, quality, enchantment,
            _int_or_none(record.get("sell_price_min")),
            record.get("sell_price_min_date"),
            _int_or_none(record.get("sell_price_max")),
            record.get("sell_price_max_date"),
            _int_or_none(record.get("buy_price_min")),
            record.get("buy_price_min_date"),
            _int_or_none(record.get("buy_price_max")),
            record.get("buy_price_max_date"),
            fetched_at,
        )
        conn.execute(
            """INSERT INTO market_prices (
                item_id, item_uniquename, city, quality, enchantment,
                sell_price_min, sell_price_min_date, sell_price_max,
                sell_price_max_date, buy_price_min, buy_price_min_date,
                buy_price_max, buy_price_max_date, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_uniquename, city, quality, enchantment) DO UPDATE SET
                item_id=excluded.item_id,
                item_uniquename=excluded.item_uniquename,
                sell_price_min=excluded.sell_price_min,
                sell_price_min_date=excluded.sell_price_min_date,
                sell_price_max=excluded.sell_price_max,
                sell_price_max_date=excluded.sell_price_max_date,
                buy_price_min=excluded.buy_price_min,
                buy_price_min_date=excluded.buy_price_min_date,
                buy_price_max=excluded.buy_price_max,
                buy_price_max_date=excluded.buy_price_max_date,
                fetched_at=excluded.fetched_at
            """,
            values,
        )
        saved += 1
    conn.commit()
    return saved


def do_fetch(
    conn: sqlite3.Connection,
    locations: Iterable[str] = CITIES,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> int:
    location_list = tuple(locations)
    if not location_list:
        raise ValueError("At least one market location must be selected")
    ids = fetch_item_ids(conn)
    total = 0
    limiter = ApiRateLimiter()
    session = requests.Session()
    chunks = chunk_ids(ids, locations=location_list)
    LOGGER.info(
        "Teljes piaci frissítés indul | itemek=%s csomagok=%s városok=%s",
        len(ids), len(chunks), location_list,
    )
    chunk_count = len(chunks)
    if progress_callback:
        progress_callback(0, chunk_count, "API-frissítés előkészítve")
    try:
        for number, chunk in enumerate(chunks, 1):
            prices = fetch_prices_for_chunk(
                chunk,
                rate_limiter=limiter,
                session=session,
                locations=location_list,
            )
            total += save_prices(conn, prices)
            LOGGER.debug(
                "Piaci csomag kész | %s/%s api_rekord=%s mentve_összesen=%s",
                number, chunk_count, len(prices), total,
            )
            if progress_callback:
                progress_callback(
                    number,
                    chunk_count,
                    f"{number}/{chunk_count} csomag · {total:,} rekord mentve",
                )
    finally:
        session.close()
    LOGGER.info("Teljes piaci frissítés kész | mentett rekordok=%s", total)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Albion market prices")
    parser.add_argument("--db", default=DB_FILE)
    args = parser.parse_args()
    from app.core.database import connect_database

    with connect_database(args.db) as conn:
        print(f"Mentés: {do_fetch(conn)} piaci rekord")


if __name__ == "__main__":
    main()
