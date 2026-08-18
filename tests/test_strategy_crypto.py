from datetime import datetime, timezone

from trading.risk.models import Side
from trading.strategy.crypto import generate_signal

NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _candles(closes, spread=0.5):
    """generate_signal takes raw candles now (it sizes its stop from ATR, which
    needs the intrabar range). These tests reason in closes, so wrap them."""
    return [[i * 3600_000, c, c + spread, c - spread, c, 1.0] for i, c in enumerate(closes)]


def test_no_signal_with_insufficient_history():
    assert generate_signal(_candles([100.0] * 5), "BTC/USDT", NOW, window=20) is None


def test_no_signal_when_price_within_band():
    closes = [100.0] * 130  # flat throughout -> zero std in the z-score lookback -> no signal
    assert generate_signal(_candles(closes), "BTC/USDT", NOW, window=20) is None


def test_long_signal_fires_when_trend_is_flat():
    # 120 flat-oscillating bars (trend history) + 20 more (z-score lookback) + a sharp drop
    closes = [100 + (i % 2) for i in range(140)] + [80.0]
    signal = generate_signal(_candles(closes), "BTC/USDT", NOW, window=20, z_threshold=2.0)
    assert signal is not None
    assert signal.side is Side.LONG
    assert signal.suggested_stop < signal.entry_price
    assert signal.suggested_target > signal.entry_price


def test_short_signal_fires_when_trend_is_flat():
    closes = [100 + (i % 2) for i in range(140)] + [130.0]
    signal = generate_signal(_candles(closes), "BTC/USDT", NOW, window=20, z_threshold=2.0)
    assert signal is not None
    assert signal.side is Side.SHORT
    assert signal.suggested_stop > signal.entry_price
    assert signal.suggested_target < signal.entry_price


def test_long_signal_suppressed_during_confirmed_downtrend():
    # 120-bar steep decline (200 -> ~100), then a flat lookback block, then a sharp drop.
    # Without the trend gate this would fire LONG (buying the dip); the gate should block
    # it because the longer-term trend is clearly down.
    trend_segment = [200 - i * (100 / 119) for i in range(120)]
    lookback_block = [100 + (i % 2) for i in range(20)]
    closes = trend_segment + lookback_block + [80.0]
    assert generate_signal(_candles(closes), "BTC/USDT", NOW, window=20, z_threshold=2.0) is None


def test_short_signal_suppressed_during_confirmed_uptrend():
    trend_segment = [100 + i * (100 / 119) for i in range(120)]
    lookback_block = [200 + (i % 2) for i in range(20)]
    closes = trend_segment + lookback_block + [230.0]
    assert generate_signal(_candles(closes), "BTC/USDT", NOW, window=20, z_threshold=2.0) is None


def test_long_signal_now_blocked_during_uptrend_too():
    # "Buy the dip in an uptrend": the old direction-only gate allowed this (LONG
    # wasn't fighting the uptrend). The new hard regime filter blocks entries
    # during any strong trend, direction notwithstanding -- research showed
    # directional-only gating still let through unprofitable trend-continuation risk.
    trend_segment = [100 + i * (100 / 119) for i in range(120)]
    lookback_block = [200 + (i % 2) for i in range(20)]
    closes = trend_segment + lookback_block + [180.0]
    assert generate_signal(_candles(closes), "BTC/USDT", NOW, window=20, z_threshold=2.0) is None


def test_short_signal_now_blocked_during_downtrend_too():
    trend_segment = [200 - i * (100 / 119) for i in range(120)]
    lookback_block = [100 + (i % 2) for i in range(20)]
    closes = trend_segment + lookback_block + [120.0]
    assert generate_signal(_candles(closes), "BTC/USDT", NOW, window=20, z_threshold=2.0) is None


def test_long_signal_blocked_when_rsi_disagrees_with_zscore():
    # z-score lookback is a steady 20-bar rise (95 -> 104), so a drop to 90 is a huge
    # z-score move (low std, since the ramp is smooth) -- but the RSI window over that
    # same rise is mostly gains, so RSI stays just above 30 (not actually oversold).
    prefix = [95.0] * 120  # flat trend history -> trend gate doesn't interfere
    ramp = [95 + i * (9 / 19) for i in range(20)]
    closes = prefix + ramp + [90.0]
    assert generate_signal(_candles(closes), "BTC/USDT", NOW, window=20, z_threshold=2.0) is None


def test_short_signal_blocked_when_rsi_disagrees_with_zscore():
    prefix = [104.0] * 120
    ramp = [104 - i * (9 / 19) for i in range(20)]
    closes = prefix + ramp + [109.0]
    assert generate_signal(_candles(closes), "BTC/USDT", NOW, window=20, z_threshold=2.0) is None
