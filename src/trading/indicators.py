"""Shared price indicators. Kept in one place so the strategies and the
backtester's trailing stop all measure volatility the same way.

Everything here takes raw ccxt candles: [ts_ms, open, high, low, close, volume].
"""


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
