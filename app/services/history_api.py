"""AODP sell-history importer used for liquidity and trend analysis."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlencode

import requests

from app.services.market_api import ApiRateLimiter, CITIES, MAX_URL_LEN
from app.version import APP_USER_AGENT

BASE_URL = "https://europe.albion-online-data.com/api/v2/stats/history"


def _history_url(
    item_ids: Iterable[str],
    *,
    locations: Iterable[str] = CITIES,
    qualities: Iterable[int] = (1, 2, 3, 4, 5),
    time_scale: int = 24,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    if time_scale not in (1, 6, 24):
        raise ValueError("History time scale must be 1, 6 or 24 hours")
    params: dict[str, str | int] = {
        "locations": ",".join(locations),
        "qualities": ",".join(str(value) for value in qualities),
        "time-scale": time_scale,
    }
    if start_date:
        params["date"] = start_date
    if end_date:
        params["end_date"] = end_date
    return f"{BASE_URL}/{','.join(item_ids)}.json?{urlencode(params)}"


def fetch_history(
    item_ids: list[str],
    *,
    locations: Iterable[str] = CITIES,
    qualities: Iterable[int] = (1, 2, 3, 4, 5),
    time_scale: int = 24,
    start_date: str | None = None,
    end_date: str | None = None,
    retries: int = 4,
    session: requests.Session | None = None,
    rate_limiter: ApiRateLimiter | None = None,
) -> list[dict[str, Any]]:
    url = _history_url(
        item_ids,
        locations=locations,
        qualities=qualities,
        time_scale=time_scale,
        start_date=start_date,
        end_date=end_date,
    )
    if len(url) > MAX_URL_LEN:
        raise ValueError(f"History request URL is {len(url)} characters; limit is {MAX_URL_LEN}")
    http = session or requests.Session()
    http.headers.update({
        "Accept-Encoding": "gzip",
        "User-Agent": APP_USER_AGENT,
    })
    limiter = rate_limiter or ApiRateLimiter()
    try:
        for attempt in range(retries):
            try:
                limiter.wait_for_slot()
                response = http.get(url, timeout=60)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError("The history API returned a non-list JSON payload")
                return payload
            except (requests.RequestException, ValueError):
                if attempt == retries - 1:
                    raise
    finally:
        if session is None:
            http.close()
    return []


def save_history(conn, payload: Iterable[dict[str, Any]], time_scale: int) -> int:
    fetched_at = datetime.now(timezone.utc).isoformat()
    saved = 0
    for series in payload:
        item_id = str(series.get("item_id", ""))
        city = series.get("location") or series.get("city")
        try:
            quality = int(series.get("quality"))
        except (TypeError, ValueError):
            continue
        if not item_id or not city:
            continue
        points = series.get("data", [])
        if not isinstance(points, list):
            continue
        for point in points:
            if not isinstance(point, dict) or not point.get("timestamp"):
                continue
            try:
                item_count = int(point.get("item_count", 0))
                avg_price = int(point.get("avg_price", 0))
            except (TypeError, ValueError):
                continue
            conn.execute(
                """INSERT INTO market_history (
                       item_uniquename, city, quality, timestamp,
                       item_count, avg_price, time_scale_hours, fetched_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(
                       item_uniquename, city, quality, timestamp, time_scale_hours
                   ) DO UPDATE SET
                       item_count=excluded.item_count,
                       avg_price=excluded.avg_price,
                       fetched_at=excluded.fetched_at""",
                (
                    item_id, city, quality, point["timestamp"], item_count,
                    avg_price, time_scale, fetched_at,
                ),
            )
            saved += 1
    conn.commit()
    return saved
