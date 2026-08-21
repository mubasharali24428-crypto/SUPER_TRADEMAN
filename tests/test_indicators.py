import math
import random

import pytest

from trading.indicators import atr, donchian, log_return_correlation


def _c(ts, o, h, l, c):
    return [ts, o, h, l, c, 1.0]


DAY_MS = 86_400_000


def _candles_from_closes(closes, start_ts=0):
    """Daily-spaced candles (ts, close, close, close, close, vol) from a close series."""
    return [[start_ts + i * DAY_MS, c, c, c, c, 1.0] for i, c in enumerate(closes)]


def _prices_from_returns(returns, start_price):
    prices = [start_price]
    for r in returns:
        prices.append(prices[-1] * math.exp(r))
    return prices


def test_atr_returns_none_without_enough_history():
    assert atr([_c(i, 10, 11, 9, 10) for i in range(5)], period=14) is None


def test_atr_of_constant_range_bars_is_that_range():
    candles = [_c(i, 10, 11, 9, 10) for i in range(20)]
    assert atr(candles, period=14) == 2.0  # high-low = 2 every bar, no gaps


def test_atr_accounts_for_gaps_beyond_the_bar_range():
    # bar range is 1, but each bar gaps 10 above the previous close, so TR is driven
    # by |high - prev_close|, not high-low. A close-to-close measure would miss this.
    candles = [_c(i, 100 + 10 * i, 100.5 + 10 * i, 99.5 + 10 * i, 100 + 10 * i) for i in range(20)]
    assert atr(candles, period=14) == 10.5


def test_donchian_returns_extremes_of_the_window():
    candles = [_c(i, 10, 10 + i, 10 - i, 10) for i in range(10)]
    high, low = donchian(candles, lookback=5)
    assert high == 19  # highest high in bars 5-9
    assert low == 1  # lowest low in bars 5-9


def test_donchian_returns_none_when_history_is_shorter_than_lookback():
    assert donchian([_c(i, 10, 11, 9, 10) for i in range(3)], lookback=5) is None


# --- log_return_correlation --------------------------------------------------


def test_log_return_correlation_of_perfectly_correlated_series_is_near_one():
    rng = random.Random(1)
    returns = [rng.uniform(-0.03, 0.03) for _ in range(120)]
    prices_a = _prices_from_returns(returns, start_price=100.0)
    prices_b = _prices_from_returns([2.0 * r for r in returns], start_price=50.0)  # scaled, same sign
    candles_a = _candles_from_closes(prices_a)
    candles_b = _candles_from_closes(prices_b)
    as_of = candles_a[-1][0] + DAY_MS  # one bar past the end -> all bars visible
    corr = log_return_correlation(candles_a, candles_b, as_of)
    assert corr == pytest.approx(1.0)


def test_log_return_correlation_of_perfectly_anti_correlated_series_is_near_negative_one():
    rng = random.Random(2)
    returns = [rng.uniform(-0.03, 0.03) for _ in range(120)]
    prices_a = _prices_from_returns(returns, start_price=100.0)
    prices_b = _prices_from_returns([-r for r in returns], start_price=50.0)
    candles_a = _candles_from_closes(prices_a)
    candles_b = _candles_from_closes(prices_b)
    as_of = candles_a[-1][0] + DAY_MS
    corr = log_return_correlation(candles_a, candles_b, as_of)
    assert corr == pytest.approx(-1.0)


def test_log_return_correlation_of_independent_series_is_near_zero():
    rng_a, rng_b = random.Random(10), random.Random(20)
    returns_a = [rng_a.uniform(-0.03, 0.03) for _ in range(300)]
    returns_b = [rng_b.uniform(-0.03, 0.03) for _ in range(300)]
    prices_a = _prices_from_returns(returns_a, start_price=100.0)
    prices_b = _prices_from_returns(returns_b, start_price=50.0)
    candles_a = _candles_from_closes(prices_a)
    candles_b = _candles_from_closes(prices_b)
    as_of = candles_a[-1][0] + DAY_MS
    corr = log_return_correlation(candles_a, candles_b, as_of, lookback=300)
    assert corr is not None
    assert abs(corr) < 0.3


def test_log_return_correlation_returns_none_with_insufficient_overlap():
    candles_a = _candles_from_closes([100.0 + i for i in range(20)])
    candles_b = _candles_from_closes([50.0 + i for i in range(20)])
    as_of = candles_a[-1][0] + DAY_MS
    assert log_return_correlation(candles_a, candles_b, as_of, min_overlap=30) is None


def test_log_return_correlation_does_not_use_bars_after_the_as_of_point():
    """Two series move together for the first half, then diverge sharply after
    the as-of index. The reported correlation must reflect only the first
    half -- if the function looked ahead, the divergence would pull it down."""
    rng = random.Random(3)
    shared_returns = [rng.uniform(-0.02, 0.02) for _ in range(60)]
    # after the cutoff, b's returns are the exact negation of a's -- a strong
    # anti-correlated tail that a look-ahead bug would blend into the result.
    diverging_returns_a = [rng.uniform(-0.02, 0.02) for _ in range(60)]
    diverging_returns_b = [-r for r in diverging_returns_a]

    prices_a = _prices_from_returns(shared_returns + diverging_returns_a, start_price=100.0)
    prices_b = _prices_from_returns(shared_returns + diverging_returns_b, start_price=50.0)
    candles_a = _candles_from_closes(prices_a)
    candles_b = _candles_from_closes(prices_b)

    as_of = candles_a[60][0]  # cutoff right at the start of the diverging tail
    corr = log_return_correlation(candles_a, candles_b, as_of, lookback=90, min_overlap=30)
    assert corr == pytest.approx(1.0)  # only the shared, perfectly-correlated first half was used

