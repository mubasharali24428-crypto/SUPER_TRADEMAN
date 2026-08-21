import ccxt
import pytest
import asyncpg

from trading.config import Settings
from trading.data.crypto import ingest_ohlcv
from trading.db.postgres import get_pool


async def test_ingest_known_historical_btc_data():
    settings = Settings()
    try:
        pool = await get_pool(settings)
    except (OSError, asyncpg.PostgresError) as e:
        pytest.skip(f"PostgreSQL not reachable: {e}")
    exchange = ccxt.binance({"enableRateLimit": True})
    try:
        since = exchange.parse8601("2020-01-01T00:00:00Z")
        candles = await ingest_ohlcv(pool, exchange, "BTC/USDT", "1h", since, 24)

        assert len(candles) == 24
        timestamps = [c[0] for c in candles]
        assert timestamps == sorted(timestamps)
        assert all(b - a == 3_600_000 for a, b in zip(timestamps, timestamps[1:]))
        assert all(5000 < c[4] < 10000 for c in candles)  # BTC/USDT close, Jan 2020 sanity band

        rows = await pool.fetch(
            "SELECT close FROM ohlcv WHERE exchange = $1 AND symbol = $2 AND timeframe = $3",
            exchange.id,
            "BTC/USDT",
            "1h",
        )
        assert len(rows) == 24

        await ingest_ohlcv(pool, exchange, "BTC/USDT", "1h", since, 24)  # re-ingest must not duplicate
        rows_again = await pool.fetch(
            "SELECT close FROM ohlcv WHERE exchange = $1 AND symbol = $2 AND timeframe = $3",
            exchange.id,
            "BTC/USDT",
            "1h",
        )
        assert len(rows_again) == 24
    finally:
        await pool.execute(
            "DELETE FROM ohlcv WHERE exchange = $1 AND symbol = $2", exchange.id, "BTC/USDT"
        )
        await pool.close()
