"""OHLCV ingestion pipeline: paginated ccxt fetch -> Postgres upsert.

Replaces synthetic feeds for SHADOW mode with real venue candles.

Contract (mission §P3-ALPHA):
* :func:`fetch_and_store_ohlcv` pages ``exchange.fetch_ohlcv`` until a short
  page terminates the loop, detects gaps against the expected candle cadence
  for the timeframe, upserts into the ``ohlcv`` table through an asyncpg pool
  from :mod:`trading.db.postgres`, and returns the number of rows stored.
* A missing table is never swallowed: asyncpg's UndefinedTableError passes
  through as :class:`trading.data.crypto.SchemaMissingError` with remediation
  guidance (schema is owned by Alembic; runtime code issues no DDL).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from trading.data.crypto import (
    SchemaMissingError,
    _schema_missing_guidance,
    _translate_missing_table,
)

__all__ = [
    "TIMEFRAME_MS",
    "expected_cadence_ms",
    "detect_gaps",
    "fetch_and_store_ohlcv",
]

logger = logging.getLogger(__name__)

#: ccxt timeframe string -> milliseconds.
TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def expected_cadence_ms(timeframe: str) -> int:
    """Milliseconds between consecutive candles for ``timeframe``."""
    if timeframe in TIMEFRAME_MS:
        return TIMEFRAME_MS[timeframe]
    # ccxt also accepts compound forms like "2h" handled above; fall back to
    # parsing "<n><unit>" so exotic but valid timeframes still get cadence.
    unit_ms = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    try:
        n = int(timeframe[:-1])
        return n * unit_ms[timeframe[-1]]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"unsupported timeframe {timeframe!r}") from exc


def detect_gaps(
    timestamps_ms: list[int],
    cadence_ms: int,
) -> list[tuple[int, int]]:
    """Return [(gap_start_exclusive, first_ts_after_gap)] pairs.

    A gap is any consecutive delta strictly greater than one cadence step.
    Duplicate/overlapping timestamps are ignored (delta <= 0).
    """
    gaps: list[tuple[int, int]] = []
    for prev, cur in zip(timestamps_ms, timestamps_ms[1:]):
        delta = cur - prev
        if delta > cadence_ms:
            gaps.append((prev + cadence_ms, cur))
    return gaps


_UPSERT_SQL = """
INSERT INTO ohlcv (exchange, symbol, asset_class, timeframe, timestamp, open, high, low, close, volume)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (exchange, symbol, timeframe, timestamp) DO NOTHING
"""


def _to_rows(exchange_id, symbol, asset_class, timeframe, candles) -> list[tuple]:
    rows = []
    for c in candles:
        ts = datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc)
        rows.append((exchange_id, symbol, asset_class, timeframe, ts, c[1], c[2], c[3], c[4], c[5]))
    return rows


async def fetch_and_store_ohlcv(
    exchange,
    symbol: str,
    timeframe: str,
    since: int,
    *,
    pool: asyncpg.Pool,
    page_limit: int = 1000,
    max_pages: Optional[int] = None,
    asset_class: str = "crypto",
    timeout_s: float = 30.0,
    maintenance_calendar: Optional[Any] = None,
) -> int:
    """Fetch OHLCV pages from ``exchange`` and upsert them into Postgres.

    Pagination: each call requests ``page_limit`` candles starting at the last
    seen open-time; a page shorter than requested terminates the loop. Gaps
    versus the expected candle cadence are detected and logged (and recorded
    on the returned result via log only — data is stored as received).
    Returns the number of NEW rows actually stored (asyncpg executemany row
    count; conflicts do not count).

    ``maintenance_calendar`` (optional): callable returning a sorted list of
    ``(start_ms, end_ms)`` closed windows for the symbol; candles falling in a
    window are not flagged as gaps (venue-closed, not missing).
    """
    if pool is None:
        raise SchemaMissingError(
            "no database pool provided — cannot store OHLCV. Apply the schema "
            "with `POSTGRES_URL=... .venv/bin/alembic upgrade head`, then pass "
            "an asyncpg pool from trading.db.postgres.get_pool()."
        )

    exchange_id = getattr(exchange, "id", str(exchange))
    cadence = expected_cadence_ms(timeframe)
    windows = list(maintenance_calendar()) if maintenance_calendar else []

    def _in_maintenance(ts_ms: int) -> bool:
        return any(start <= ts_ms < end for start, end in windows)

    cursor: Optional[int] = since
    all_timestamps: list[int] = []
    stored_total = 0
    pages = 0

    while True:
        try:
            page = await _translate_missing_table(
                _fetch_page(exchange, symbol, timeframe, cursor, page_limit, timeout_s)
            )
        except asyncpg.UndefinedTableError as exc:
            # Defensive: _translate_missing_table already converts this; if a
            # raw one escapes anyway it must not be swallowed.
            raise SchemaMissingError(_schema_missing_guidance("ohlcv")) from exc
        pages += 1

        fresh: list[list[Any]] = []
        for candle in page or []:
            ts = candle[0]
            if all_timestamps and ts <= all_timestamps[-1]:
                continue  # overlap guard
            all_timestamps.append(ts)
            fresh.append(candle)

        if fresh:
            stored = await _translate_missing_table(
                pool.executemany(_UPSERT_SQL, _to_rows(exchange_id, symbol, asset_class, timeframe, fresh))
            )
            stored_total += stored or 0

        if len(page or []) < page_limit:
            break  # short page => end of available range
        if max_pages is not None and pages >= max_pages:
            logger.warning("max_pages=%d reached for %s %s", max_pages, symbol, timeframe)
            break
        if not fresh and page:
            # Fully-overlapping page: advance cursor past it to guarantee progress.
            cursor = all_timestamps[-1] if all_timestamps else cursor
        else:
            cursor = all_timestamps[-1] if all_timestamps else (
                (cursor or since) + page_limit * cadence
            )

    gaps = [g for g in detect_gaps(all_timestamps, cadence) if not _in_maintenance(g[0])]
    for gap_start, gap_end in gaps:
        logger.warning(
            "OHLCV GAP %s %s: missing candles between %s and %s (%d ms)",
            symbol, timeframe,
            datetime.fromtimestamp(gap_start / 1000, tz=timezone.utc).isoformat(),
            datetime.fromtimestamp(gap_end / 1000, tz=timezone.utc).isoformat(),
            gap_end - gap_start,
        )

    logger.info(
        "fetch_and_store_ohlcv %s %s: %d pages, %d candles seen, %d rows stored, %d gaps",
        symbol, timeframe, pages, len(all_timestamps), stored_total, len(gaps),
    )
    return stored_total


async def _fetch_page(exchange, symbol, timeframe, since, limit, timeout_s):
    """One ccxt fetch_ohlcv page under asyncio timeout (no silent retries here;
    transport-level retry/backoff lives in websocket_feed / callers)."""
    coro = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
    return await asyncio.wait_for(coro, timeout=timeout_s)
