from trading.indicators import atr, donchian


def _c(ts, o, h, l, c):
    return [ts, o, h, l, c, 1.0]


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
