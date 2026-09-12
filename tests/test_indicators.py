import numpy as np
import pytest

from trading.indicators import atr, donchian, log_return_correlation

DAY_MS = 86_400_000


def test_atr_returns_none_without_enough_history(candle_row):
    assert (
        atr([candle_row(ts=i, o=10, h=11, l=9, c=10) for i in range(5)], period=14)
        is None
    )


def test_atr_of_constant_range_bars_is_that_range(candle_row):
    candles = [candle_row(ts=i, o=10, h=11, l=9, c=10) for i in range(20)]
    assert atr(candles, period=14) == 2.0  # high-low = 2 every bar, no gaps


def test_atr_accounts_for_gaps_beyond_the_bar_range(candle_row):
    # bar range is 1, but each bar gaps 10 above the previous close, so TR is driven
    # by |high - prev_close|, not high-low. A close-to-close measure would miss this.
    candles = [
        candle_row(
            ts=i, o=100 + 10 * i, h=100.5 + 10 * i, l=99.5 + 10 * i, c=100 + 10 * i
        )
        for i in range(20)
    ]
    assert atr(candles, period=14) == 10.5


def test_donchian_returns_extremes_of_the_window(candle_row):
    candles = [candle_row(ts=i, o=10, h=10 + i, l=10 - i, c=10) for i in range(10)]
    high, low = donchian(candles, lookback=5)
    assert high == 19  # highest high in bars 5-9
    assert low == 1  # lowest low in bars 5-9


def test_donchian_returns_none_when_history_is_shorter_than_lookback(candle_row):
    assert (
        donchian(
            [candle_row(ts=i, o=10, h=11, l=9, c=10) for i in range(3)], lookback=5
        )
        is None
    )


# --- log_return_correlation --------------------------------------------------


def test_log_return_correlation_of_perfectly_correlated_series_is_near_one(
    rng, prices_from_returns, close_candles
):
    returns = list(rng.uniform(-0.03, 0.03, size=120))
    prices_a = prices_from_returns(returns, start_price=100.0)
    prices_b = prices_from_returns(
        [2.0 * r for r in returns], start_price=50.0
    )  # scaled, same sign
    candles_a = close_candles(prices_a)
    candles_b = close_candles(prices_b)
    as_of = candles_a[-1][0] + DAY_MS  # one bar past the end -> all bars visible
    corr = log_return_correlation(candles_a, candles_b, as_of)
    assert corr == pytest.approx(1.0)


def test_log_return_correlation_of_perfectly_anti_correlated_series_is_near_negative_one(
    rng, prices_from_returns, close_candles
):
    returns = list(rng.uniform(-0.03, 0.03, size=120))
    prices_a = prices_from_returns(returns, start_price=100.0)
    prices_b = prices_from_returns([-r for r in returns], start_price=50.0)
    candles_a = close_candles(prices_a)
    candles_b = close_candles(prices_b)
    as_of = candles_a[-1][0] + DAY_MS
    corr = log_return_correlation(candles_a, candles_b, as_of)
    assert corr == pytest.approx(-1.0)


def test_log_return_correlation_of_independent_series_is_near_zero(
    rng, prices_from_returns, close_candles
):
    # Two spawned streams are independent of each other yet reproducible
    # from the session's TEST_SEED.
    rng_a, rng_b = rng.spawn(2)
    returns_a = list(rng_a.uniform(-0.03, 0.03, size=300))
    returns_b = list(rng_b.uniform(-0.03, 0.03, size=300))
    prices_a = prices_from_returns(returns_a, start_price=100.0)
    prices_b = prices_from_returns(returns_b, start_price=50.0)
    candles_a = close_candles(prices_a)
    candles_b = close_candles(prices_b)
    as_of = candles_a[-1][0] + DAY_MS
    corr = log_return_correlation(candles_a, candles_b, as_of, lookback=300)
    assert corr is not None
    assert abs(corr) < 0.3


def test_log_return_correlation_returns_none_with_insufficient_overlap(close_candles):
    candles_a = close_candles([100.0 + i for i in range(20)])
    candles_b = close_candles([50.0 + i for i in range(20)])
    as_of = candles_a[-1][0] + DAY_MS
    assert log_return_correlation(candles_a, candles_b, as_of, min_overlap=30) is None


def test_log_return_correlation_does_not_use_bars_after_the_as_of_point(
    rng, prices_from_returns, close_candles
):
    """Two series move together for the first half, then diverge sharply after
    the as-of index. The reported correlation must reflect only the first
    half -- if the function looked ahead, the divergence would pull it down."""
    shared_returns = list(rng.uniform(-0.02, 0.02, size=60))
    # after the cutoff, b's returns are the exact negation of a's -- a strong
    # anti-correlated tail that a look-ahead bug would blend into the result.
    diverging_returns_a = list(rng.uniform(-0.02, 0.02, size=60))
    diverging_returns_b = [-r for r in diverging_returns_a]

    prices_a = prices_from_returns(
        shared_returns + diverging_returns_a, start_price=100.0
    )
    prices_b = prices_from_returns(
        shared_returns + diverging_returns_b, start_price=50.0
    )
    candles_a = close_candles(prices_a)
    candles_b = close_candles(prices_b)

    as_of = candles_a[60][0]  # cutoff right at the start of the diverging tail
    corr = log_return_correlation(
        candles_a, candles_b, as_of, lookback=90, min_overlap=30
    )
    assert corr == pytest.approx(
        1.0
    )  # only the shared, perfectly-correlated first half was used


# --- candle_frame factory -----------------------------------------------------


def test_candle_frame_factory_shapes(rng, candle_frame):
    rows = candle_frame(n=10, warmup=14, rng=rng)
    assert len(rows) == 24  # warmup + n
    assert all(len(r) == 6 for r in rows)  # ccxt-style [ts, o, h, l, c, vol]
    dicts = candle_frame(n=3, as_dicts=True)
    assert set(dicts[0]) == {"timestamp", "open", "high", "low", "close", "volume"}
