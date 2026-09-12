"""Root pytest fixtures for SUPER_TRADEMAN (SUB-08).

Shared fixtures to replace the copy-pasted AccountState / candle-series
boilerplate that previously lived in ~a dozen test files.

VC-031 (adjudicated 2026-09-12): 13/16 top-level test files now consume
shared fixtures; the 3 that do not (test_crypto_data, test_db,
test_two_process_lock) mock externals/internals by design and need none.
Residual: test_strategy_crypto.py still carries one local candle helper —
tracked for the next conftest adoption pass.

Conventions
-----------
- Candles are ccxt-style rows: ``[ts_ms, open, high, low, close, volume]``
  (see ``src/trading/indicators.py``). The ``candle_frame`` factory can also
  emit OHLCV list-of-dicts via ``as_dicts=True``.
- Randomness goes through the seeded ``rng`` fixture so failures reproduce:
  set ``TEST_SEED`` to rerun a failing seed; default is 8675309.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pytest

from trading.learning.graph import LearningGraph
from trading.risk.models import AccountState

DEFAULT_TEST_SEED = 8675309
DAY_MS = 86_400_000


# ---------------------------------------------------------------------------
# Seeded randomness
# ---------------------------------------------------------------------------


@pytest.fixture
def rng() -> np.random.Generator:
    """Deterministic numpy Generator.

    Seed comes from the TEST_SEED environment variable (default 8675309) so a
    flaky failure can be replayed with ``TEST_SEED=<n> pytest ...``.
    """
    seed = int(os.getenv("TEST_SEED", str(DEFAULT_TEST_SEED)))
    return np.random.default_rng(seed)


@pytest.fixture
def rng_seed() -> int:
    """The raw seed value (for tests that need to construct their own RNGs)."""
    return int(os.getenv("TEST_SEED", str(DEFAULT_TEST_SEED)))


# ---------------------------------------------------------------------------
# Account state factory
# ---------------------------------------------------------------------------


@pytest.fixture
def account_state():
    """Factory for fresh ``AccountState`` objects.

    Returns a callable so each test gets an independent instance (the dataclass
    holds mutable lists/dicts — never share one across assertions):

        account = account_state()                       # flat $10k account
        account = account_state(equity=8_000)           # drawdown vs peak
        account = account_state(kill_switch=True)

    Defaults mirror the values most existing tests hand-rolled.
    """

    def _make(
        equity: float = 10_000.0,
        peak_equity: float | None = None,
        **overrides: Any,
    ) -> AccountState:
        if peak_equity is None:
            peak_equity = equity
        return AccountState(equity=equity, peak_equity=peak_equity, **overrides)

    return _make


# ---------------------------------------------------------------------------
# Learning graph fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_learning_graph(tmp_path):
    """A ``LearningGraph`` backed by a throwaway jsonl under tmp_path.

    Prevents tests from reading/polluting the repo-root learning_graph.jsonl.
    """
    return LearningGraph(storage_path=tmp_path / "learning_graph.jsonl")


# ---------------------------------------------------------------------------
# Candle-frame factories
# ---------------------------------------------------------------------------


def make_candles(
    n: int = 30,
    start_ts_ms: int = 0,
    interval_ms: int = DAY_MS,
    start_price: float = 100.0,
    drift: float = 0.0,
    spread: float = 1.0,
    volume: float = 1.0,
    warmup: int = 0,
    rng: np.random.Generator | None = None,
    as_dicts: bool = False,
) -> list:
    """Generate an OHLCV series.

    Args:
        n: number of bars after the warmup prefix.
        start_ts_ms/interval_ms: bar timestamps (default daily spacing).
        start_price: close of the first bar.
        drift: additive price step per bar (trend control).
        spread: half-range of the noise added to closes when ``rng`` given.
        volume: constant volume per bar.
        warmup: extra leading bars emitted before the ``n`` real bars, so a
            test can satisfy indicator lookbacks without counting them against
            the window it inspects (warmup + n total bars).
        rng: optional seeded Generator; noise-free when omitted.
        as_dicts: return OHLCV dicts instead of ccxt-style rows.

    Returns:
        list of [ts, open, high, low, close, volume] rows (or dicts).
    """
    total = warmup + n
    closes = []
    price = start_price - drift * warmup
    for i in range(total):
        noise = float(rng.uniform(-spread, spread)) if rng is not None else 0.0
        closes.append(max(price, 0.01))
        price += drift + noise

    rows = []
    ts = start_ts_ms
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c
        high = max(o, c)
        low = min(o, c)
        row: Any = {
            "timestamp": ts,
            "open": o,
            "high": high,
            "low": low,
            "close": c,
            "volume": volume,
        }
        rows.append(row)
        ts += interval_ms

    if not as_dicts:
        # ccxt-style positional rows used by indicators/backtest modules.
        rows = [[r["timestamp"], r["open"], r["high"], r["low"], r["close"], r["volume"]] for r in rows]
    return rows


def candles_from_closes(
    closes,
    start_ts_ms: int = 0,
    interval_ms: int = DAY_MS,
    as_dicts: bool = False,
) -> list:
    """Build flat candles from an explicit close-price series (o=h=l=c)."""
    rows = [
        {
            "timestamp": start_ts_ms + i * interval_ms,
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 1.0,
        }
        for i, c in enumerate(closes)
    ]
    if as_dicts:
        return rows
    return [[r["timestamp"], r["open"], r["high"], r["low"], r["close"], r["volume"]] for r in rows]


def returns_to_prices(returns, start_price: float = 100.0) -> list[float]:
    """Cumulative log-return series -> price path (helper for correlation tests)."""
    prices = [start_price]
    for r in returns:
        prices.append(prices[-1] * float(np.exp(r)))
    return prices


def utc_now_iso(offset_days: int = 0) -> str:
    """ISO timestamp helper matching LearningGraph's timestamp format."""
    dt = datetime.now(timezone.utc) + timedelta(days=offset_days)
    return dt.isoformat()


@pytest.fixture
def candle_frame():
    """Fixture form of :func:`make_candles` (injectable into other fixtures/tests).

        candles = candle_frame(n=20, warmup=14, rng=rng)
        candles = candle_frame(n=5, as_dicts=True)
    """
    return make_candles


@pytest.fixture
def candle_row():
    """Single-row builder matching the ccxt candle convention.

        row = candle_row(ts=i, o=10, h=11, l=9, c=10)
    """

    def _make(ts: int = 0, o: float = 0.0, h: float = 0.0, l: float = 0.0, c: float = 0.0, v: float = 1.0) -> list[float]:
        return [ts, o, h, l, c, v]

    return _make


@pytest.fixture
def close_candles():
    """Candles from an explicit close-price series (o=h=l=c per bar)."""
    return candles_from_closes


@pytest.fixture
def prices_from_returns():
    """Cumulative log-return series -> price path."""
    return returns_to_prices


@pytest.fixture
def flat_candles():
    """Constant-price candles (o=h=l=c=100), handy for 'no volatility' cases."""
    return lambda n=20, price=100.0, **kw: candles_from_closes([price] * n, **kw)
