"""Shared price indicators. Kept in one place so the strategies and the
backtester's trailing stop all measure volatility the same way.

Everything here takes raw ccxt candles: [ts_ms, open, high, low, close, volume].
"""

import math
import statistics


def atr(candles: list[list[float]], period: int = 14) -> float | None:
    """Average True Range. Returns None if there isn't enough history.

    True Range includes the high-low bar range, so it reflects the intrabar
    excursion that actually triggers a stop. Close-to-close standard deviation
    does not, which is why stops sized from it get hit by wicks more often than
    the estimate implies.

    ponytail: simple mean of TR, not Wilder's smoothing. Wilder's is stickier
    across regime shifts; switch if stop distances prove too jumpy.
    """
    if len(candles) < period + 1:
        return None
    true_ranges = []
    for prev, cur in zip(candles[-period - 1 : -1], candles[-period:]):
        high, low, prev_close = cur[2], cur[3], prev[4]
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(true_ranges) / period


def donchian(candles: list[list[float]], lookback: int) -> tuple[float, float] | None:
    """(highest high, lowest low) over the last `lookback` bars, or None if short."""
    if len(candles) < lookback:
        return None
    window = candles[-lookback:]
    return max(c[2] for c in window), min(c[3] for c in window)


def log_return_correlation(
    candles_a: list[list[float]],
    candles_b: list[list[float]],
    as_of_ts: float,
    lookback: int = 90,
    min_overlap: int = 30,
) -> float | None:
    """Pearson correlation of log returns between two candle series, over the
    trailing `lookback` bars, using ONLY bars strictly before `as_of_ts` (ms).

    Callers may pass each series' full candle list (including bars after
    `as_of_ts`) -- the timestamp filter below is what makes this safe to call
    live during a backtest to gate a new signal: it can never see a bar the
    strategy couldn't have seen yet at that point in time.

    Bars are aligned by TIMESTAMP, not list index, since two assets' candle
    lists can have different lengths/start dates (e.g. a later exchange
    listing). Returns None if fewer than `min_overlap` overlapping return
    pairs are available -- too little shared history to estimate a
    correlation. The risk engine's correlation guard treats a missing entry
    in `AccountState.correlations` as 0.0 (no guard effect), which is the
    correct fallback here rather than raising or fabricating a number.
    """
    closes_a = {ts: close for ts, _o, _h, _l, close, _v in candles_a if ts < as_of_ts}
    closes_b = {ts: close for ts, _o, _h, _l, close, _v in candles_b if ts < as_of_ts}
    common_ts = sorted(set(closes_a) & set(closes_b))[-(lookback + 1) :]
    if len(common_ts) < 2:
        return None

    prices_a = [closes_a[ts] for ts in common_ts]
    prices_b = [closes_b[ts] for ts in common_ts]
    returns_a = [math.log(y / x) for x, y in zip(prices_a, prices_a[1:])]
    returns_b = [math.log(y / x) for x, y in zip(prices_b, prices_b[1:])]
    if len(returns_a) < min_overlap:
        return None
    try:
        return statistics.correlation(returns_a, returns_b)
    except statistics.StatisticsError:  # zero variance in one series
        return None
