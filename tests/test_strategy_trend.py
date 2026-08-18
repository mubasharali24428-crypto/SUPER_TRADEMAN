from datetime import datetime, timezone

from trading.risk.models import Side
from trading.strategy.trend import generate_trend_signal

NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candles(closes, spread=1.0):
    return [[i * 3600_000, c, c + spread, c - spread, c, 1.0] for i, c in enumerate(closes)]


def test_no_signal_without_enough_history():
    assert generate_trend_signal(_candles([100.0] * 10), "BTC/USDT", NOW) is None


def test_no_signal_inside_the_channel():
    closes = [100 + (i % 3) for i in range(80)]
    assert generate_trend_signal(_candles(closes), "BTC/USDT", NOW) is None


def test_long_on_upside_breakout():
    closes = [100 + (i % 3) for i in range(80)] + [150.0]
    signal = generate_trend_signal(_candles(closes), "BTC/USDT", NOW)
    assert signal is not None
    assert signal.side is Side.LONG
    assert signal.suggested_stop < signal.entry_price < signal.suggested_target


def test_short_on_downside_breakout():
    closes = [100 + (i % 3) for i in range(80)] + [50.0]
    signal = generate_trend_signal(_candles(closes), "BTC/USDT", NOW)
    assert signal is not None
    assert signal.side is Side.SHORT
    assert signal.suggested_target < signal.entry_price < signal.suggested_stop


def test_reward_risk_clears_the_risk_engine_floor():
    """The point of pairing this strategy with the existing RiskConfig: unlike mean
    reversion, a breakout's geometry naturally satisfies min_reward_risk=2.0."""
    closes = [100 + (i % 3) for i in range(80)] + [150.0]
    signal = generate_trend_signal(_candles(closes), "BTC/USDT", NOW, atr_stop_mult=2.0, atr_target_mult=6.0)
    reward = abs(signal.suggested_target - signal.entry_price)
    risk = abs(signal.entry_price - signal.suggested_stop)
    assert reward / risk >= 2.0


def test_current_bar_cannot_break_its_own_channel():
    """A rising series where every bar is the highest so far must not fire on the
    strength of the current bar being included in its own lookback window."""
    closes = [100 + i * 0.01 for i in range(80)]  # drifts up slowly, no real breakout
    signal = generate_trend_signal(_candles(closes, spread=0.001), "BTC/USDT", NOW)
    assert signal is None or signal.entry_price > 100 + 79 * 0.01 - 1
