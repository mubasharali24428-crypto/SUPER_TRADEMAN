"""Tests for data/pipeline.py — pagination, gap detection, schema guard.

Fake exchange serves deterministic pages; fake pool records executemany
calls. No network, no real Postgres.
"""

import asyncpg
import pytest

from trading.data.crypto import SchemaMissingError
from trading.data.pipeline import (
    detect_gaps,
    expected_cadence_ms,
    fetch_and_store_ohlcv,
)


class FakePageExchange:
    """Serves pre-built pages of ccxt-style candles; records since-args."""

    id = "fakeexchange"

    def __init__(self, pages):
        # pages: list[list[[ts,o,h,l,c,v], ...]]
        self.pages = list(pages)
        self.since_args = []

    async def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
        self.since_args.append(since)
        if not self.pages:
            return []
        return self.pages.pop(0)


class FakePool:
    def __init__(self):
        self.batches = []
        self.raise_undefined_table = False

    async def executemany(self, sql, rows):
        if self.raise_undefined_table:
            raise _NamedUndefinedTable("ohlcv")
        self.batches.append((sql, list(rows)))
        return len(rows)


class _NamedUndefinedTable(asyncpg.UndefinedTableError):
    def __init__(self, relation_name: str):
        super().__init__(f'relation "{relation_name}" does not exist')


TF_MS = expected_cadence_ms("1m")  # 60_000


def candles(start_ms, n):
    return [[start_ms + i * TF_MS, 1.0, 2.0, 0.5, 1.5, 10.0] for i in range(n)]


# ------------------------------- cadence -------------------------------- #

def test_expected_cadence_known_and_compound_timeframes():
    assert expected_cadence_ms("1m") == 60_000
    assert expected_cadence_ms("5m") == 300_000
    assert expected_cadence_ms("1h") == 3_600_000
    assert expected_cadence_ms("4h") == 14_400_000
    assert expected_cadence_ms("2h") == 7_200_000
    with pytest.raises(ValueError):
        expected_cadence_ms("7x")


def test_detect_gaps_flags_only_true_gaps():
    ts = [0, 60_000, 120_000, 600_000, 660_000]   # one 8-minute hole
    gaps = detect_gaps(ts, TF_MS)
    assert gaps == [(180_000, 600_000)]


# ------------------------------ pagination ------------------------------ #

@pytest.mark.asyncio
async def test_pagination_stops_on_short_page_and_stores_all_rows():
    page_a, page_b = candles(0, 3), candles(3 * TF_MS, 2)
    ex = FakePageExchange([page_a, page_b])
    pool = FakePool()

    stored = await fetch_and_store_ohlcv(
        ex, "BTC/USDT", "1m", since=0, pool=pool, page_limit=3
    )

    assert stored == 5
    assert len(pool.batches) == 2
    assert [len(b[1]) for b in pool.batches] == [3, 2]
    # second fetch advanced the cursor to the last seen open-time
    assert ex.since_args[1] == 2 * TF_MS


@pytest.mark.asyncio
async def test_full_pages_keep_paging_until_short_page():
    pages = [candles(i * 100 * TF_MS, 100) for i in range(3)]
    pages.append(candles(3 * 100 * TF_MS, 40))          # short => stop
    ex = FakePageExchange(list(pages))
    pool = FakePool()

    stored = await fetch_and_store_ohlcv(
        ex, "BTC/USDT", "1m", since=0, pool=pool, page_limit=100
    )
    assert stored == 340
    assert len(ex.since_args) == 4                       # exactly four fetches


@pytest.mark.asyncio
async def test_overlapping_rows_are_deduped_not_double_stored():
    p1 = candles(0, 3)
    p2 = candles(2 * TF_MS, 3)                            # overlaps ts 2m
    ex = FakePageExchange([p1, p2])
    pool = FakePool()

    stored = await fetch_and_store_ohlcv(ex, "BTC/USDT", "1m", since=0, pool=pool, page_limit=3)
    assert stored == 5                                    # dup ts dropped


@pytest.mark.asyncio
async def test_max_pages_caps_the_loop():
    ex = FakePageExchange([candles(i * 100 * TF_MS, 100) for i in range(10)])
    pool = FakePool()
    stored = await fetch_and_store_ohlcv(
        ex, "BTC/USDT", "1m", since=0, pool=pool, page_limit=100, max_pages=2
    )
    assert stored == 200 and len(ex.since_args) == 2


# --------------------------------- gaps --------------------------------- #

@pytest.mark.asyncio
async def test_gap_detection_logs_missing_candles(caplog):
    page = candles(0, 2) + candles(9 * TF_MS, 1)          # hole between 2m..9m
    ex = FakePageExchange([page])
    pool = FakePool()
    caplog.set_level("WARNING", logger="trading.data.pipeline")

    stored = await fetch_and_store_ohlcv(ex, "BTC/USDT", "1m", since=0, pool=pool, page_limit=10)
    assert stored == 3
    assert any("OHLCV GAP" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_maintenance_calendar_windows_suppress_gap_warnings(caplog):
    page = candles(0, 2) + candles(9 * TF_MS, 1)
    ex = FakePageExchange([page])
    pool = FakePool()
    caplog.set_level("WARNING", logger="trading.data.pipeline")

    calendar = lambda: [(2 * TF_MS, 9 * TF_MS)]
    stored = await fetch_and_store_ohlcv(
        ex, "BTC/USDT", "1m", since=0, pool=pool, page_limit=10,
        maintenance_calendar=calendar,
    )
    assert stored == 3
    assert not any("OHLCV GAP" in r.message for r in caplog.records)


# ----------------------------- schema guard ----------------------------- #

@pytest.mark.asyncio
async def test_schema_missing_error_passthrough_from_pool():
    ex = FakePageExchange([candles(0, 2)])
    pool = FakePool()
    pool.raise_undefined_table = True

    with pytest.raises(SchemaMissingError) as err:
        await fetch_and_store_ohlcv(ex, "BTC/USDT", "1m", since=0, pool=pool)
    assert isinstance(err.value.__cause__, asyncpg.UndefinedTableError)
    assert "alembic" in str(err.value).lower()


@pytest.mark.asyncio
async def test_no_pool_raises_loud_guidance():
    ex = FakePageExchange([])
    with pytest.raises(SchemaMissingError):
        await fetch_and_store_ohlcv(ex, "BTC/USDT", "1m", since=0, pool=None)


@pytest.mark.asyncio
async def test_upsert_sql_matches_ohlcv_conflict_key():
    ex = FakePageExchange([candles(0, 1)])
    pool = FakePool()
    await fetch_and_store_ohlcv(ex, "BTC/USDT", "1m", since=0, pool=pool)
    sql = pool.batches[0][0]
    assert "INSERT INTO ohlcv" in sql
    assert "ON CONFLICT (exchange, symbol, timeframe, timestamp)" in sql
