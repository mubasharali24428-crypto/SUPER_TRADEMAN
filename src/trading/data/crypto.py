"""Crypto market-data ingestion (OHLCV + funding rates).

Schema ownership: the ``ohlcv`` / ``funding_rates`` tables are owned by Alembic
migrations (see ``alembic/versions/``). This module performs NO runtime DDL —
the old ``CREATE TABLE IF NOT EXISTS`` self-healing was removed (HK-1 /
SUB-03 follow-up) so schema drift surfaces loudly instead of being silently
papered over. Apply the schema with::

    POSTGRES_URL=postgresql://... .venv/bin/alembic upgrade head
"""

import asyncio
import logging
from datetime import datetime, timezone

import asyncpg
import ccxt

logger = logging.getLogger(__name__)

__all__ = [
    "SchemaMissingError",
    "fetch_ohlcv_with_backoff",
    "fetch_ohlcv_range",
    "store_ohlcv",
    "store_funding_rates",
    "ingest_ohlcv",
    "ingest_funding_rates",
]


class SchemaMissingError(RuntimeError):
    """A required data table does not exist because migrations were not applied."""


def _schema_missing_guidance(table: str) -> str:
    return (
        f"Relation '{table}' is missing: the database schema is owned by Alembic "
        f"and runtime DDL self-healing has been removed from "
        f"trading.data.crypto. Apply migrations before ingesting: "
        f"POSTGRES_URL=<url> alembic upgrade head"
    )


async def fetch_ohlcv_with_backoff(exchange, symbol, timeframe, since, limit, max_retries=5):
    delay = 1.0
    for attempt in range(max_retries):
        try:
            return await asyncio.to_thread(exchange.fetch_ohlcv, symbol, timeframe, since, limit)
        except ccxt.NetworkError:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2


async def fetch_ohlcv_range(exchange, symbol, timeframe, since, until, limit=1000):
    """Fetch OHLCV candles over [since, until) with pagination.

    Sub-09 fixes:
    - Termination is now explicit and logged: stop when a page returns fewer
      rows than requested (exchange history exhausted) OR when the cursor
      fails to advance (stuck/exhausted cursor), instead of relying on the
      empty-batch break alone.
    - Rows fetched are logged per page and in total.
    """
    timeframe_ms = exchange.parse_timeframe(timeframe) * 1000
    candles = []
    cursor = since
    rows_fetched = 0
    while cursor < until:
        batch = await fetch_ohlcv_with_backoff(exchange, symbol, timeframe, cursor, limit)
        if not batch:
            logger.info(
                "fetch_ohlcv_range %s %s: empty page at cursor=%s; stopping (%d rows fetched)",
                symbol,
                timeframe,
                cursor,
                rows_fetched,
            )
            break
        candles.extend(batch)
        rows_fetched += len(batch)
        logger.info(
            "fetch_ohlcv_range %s %s: page cursor=%s -> %d rows (%d total)",
            symbol,
            timeframe,
            cursor,
            len(batch),
            rows_fetched,
        )
        next_cursor = batch[-1][0] + timeframe_ms
        # Page shorter than the requested limit => exchange has no more history.
        if len(batch) < limit:
            break
        # Cursor exhausted / no forward progress would loop forever.
        if next_cursor <= cursor:
            logger.warning(
                "fetch_ohlcv_range %s %s: cursor did not advance (%s -> %s); stopping "
                "(%d rows fetched)",
                symbol,
                timeframe,
                cursor,
                next_cursor,
                rows_fetched,
            )
            break
        cursor = next_cursor
    logger.info("fetch_ohlcv_range %s %s: complete, %d rows fetched", symbol, timeframe, rows_fetched)
    return [c for c in candles if c[0] < until]


async def _translate_missing_table(coro):
    """Await a store/ingest coroutine, mapping undefined-table to loud guidance.

    asyncpg raises ``asyncpg.exceptions.UndefinedTableError`` (SQLSTATE 42P01)
    when a relation does not exist. Since Alembic owns the schema and runtime
    DDL is gone, that condition is a deployment mistake — re-raise it as
    :class:`SchemaMissingError` with remediation instructions.
    """
    try:
        return await coro
    except asyncpg.UndefinedTableError as exc:
        table = getattr(exc, "relation_name", None) or "ohlcv/funding_rates"
        raise SchemaMissingError(_schema_missing_guidance(table)) from exc


async def store_ohlcv(pool: asyncpg.Pool, exchange_id, asset_class, symbol, timeframe, candles):
    rows = [
        (
            exchange_id,
            symbol,
            asset_class,
            timeframe,
            datetime.fromtimestamp(c[0] / 1000, tz=timezone.utc),
            c[1],
            c[2],
            c[3],
            c[4],
            c[5],
        )
        for c in candles
    ]
    await pool.executemany(
        """
        INSERT INTO ohlcv (exchange, symbol, asset_class, timeframe, timestamp, open, high, low, close, volume)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (exchange, symbol, timeframe, timestamp) DO NOTHING
        """,
        rows,
    )


async def store_funding_rates(pool: asyncpg.Pool, exchange_id: str, symbol: str, funding_events: list[dict]):
    rows = [
        (
            exchange_id,
            symbol,
            datetime.fromtimestamp(event["timestamp"] / 1000, tz=timezone.utc)
            if isinstance(event["timestamp"], (int, float))
            else event["timestamp"],
            event["fundingRate"],
            event.get("markPrice", 0.0),
        )
        for event in funding_events
    ]
    await pool.executemany(
        """
        INSERT INTO funding_rates (exchange, symbol, timestamp, funding_rate, mark_price)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (exchange, symbol, timestamp) DO NOTHING
        """,
        rows,
    )


async def ingest_ohlcv(pool: asyncpg.Pool, exchange, symbol, timeframe, since, limit, asset_class="crypto"):
    candles = await fetch_ohlcv_with_backoff(exchange, symbol, timeframe, since, limit)
    await _translate_missing_table(
        store_ohlcv(pool, exchange.id, asset_class, symbol, timeframe, candles)
    )
    return candles


async def ingest_funding_rates(pool: asyncpg.Pool, exchange, symbol, since, limit=1000):
    funding_events = await asyncio.to_thread(exchange.fetch_funding_rate_history, symbol, since, limit)
    await _translate_missing_table(
        store_funding_rates(pool, exchange.id, symbol, funding_events)
    )
    return funding_events

