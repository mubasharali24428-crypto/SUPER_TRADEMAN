import asyncio
from datetime import datetime, timezone

import asyncpg
import ccxt


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
    timeframe_ms = exchange.parse_timeframe(timeframe) * 1000
    candles = []
    cursor = since
    while cursor < until:
        batch = await fetch_ohlcv_with_backoff(exchange, symbol, timeframe, cursor, limit)
        if not batch:
            break
        candles.extend(batch)
        cursor = batch[-1][0] + timeframe_ms
        if len(batch) < limit:
            break
    return [c for c in candles if c[0] < until]


async def ensure_schema(pool: asyncpg.Pool):
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS ohlcv (
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            asset_class TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            open DOUBLE PRECISION NOT NULL,
            high DOUBLE PRECISION NOT NULL,
            low DOUBLE PRECISION NOT NULL,
            close DOUBLE PRECISION NOT NULL,
            volume DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (exchange, symbol, timeframe, timestamp)
        );

        CREATE TABLE IF NOT EXISTS funding_rates (
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            funding_rate DOUBLE PRECISION NOT NULL,
            mark_price DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (exchange, symbol, timestamp)
        );
        """
    )


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
    await ensure_schema(pool)
    await store_ohlcv(pool, exchange.id, asset_class, symbol, timeframe, candles)
    return candles


async def ingest_funding_rates(pool: asyncpg.Pool, exchange, symbol, since, limit=1000):
    funding_events = await asyncio.to_thread(exchange.fetch_funding_rate_history, symbol, since, limit)
    await ensure_schema(pool)
    await store_funding_rates(pool, exchange.id, symbol, funding_events)
    return funding_events

