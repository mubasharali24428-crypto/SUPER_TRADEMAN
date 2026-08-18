import statistics
from datetime import datetime

from trading.indicators import atr
from trading.risk.models import Side, Signal


def _rsi(closes: list[float], period: int) -> float:
    window = closes[-(period + 1) :]
    deltas = [window[i] - window[i - 1] for i in range(1, len(window))]
    gains = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def generate_signal(
    candles: list[list[float]],
    asset: str,
    timestamp: datetime,
    window: int = 20,
    z_threshold: float = 2.0,
    atr_period: int = 14,
    atr_stop_mult: float = 1.5,
    trend_window: int = 100,
    trend_lookback: int = 20,
    trend_strength_threshold: float = 0.008,
    rsi_period: int = 14,
    rsi_oversold: float = 30.0,
    rsi_overbought: float = 70.0,
) -> Signal | None:
    """Mean-reversion: signal when price deviates > z_threshold std devs from its
    trailing rolling mean, targeting reversion back to that mean.

    Takes raw candles ([ts_ms, o, h, l, c, v]) rather than closes, because the
    stop is sized from ATR, which needs the intrabar range.

    Gated by:
    - a hard regime filter: no entries at all (either direction) when the
      longer-term trend is strong, since naive mean-reversion doesn't have an
      edge against trend continuation regardless of which side it fades.
    - RSI confirmation (only buy when RSI is also oversold, only sell when
      RSI is also overbought).

    Two calibration notes, both from measured behaviour on 270d of BTC/USDT 1h
    (see ANTIGRAVITY.md sections 4-5):

    - `trend_strength_threshold` was 0.02, which sat ABOVE the 99th percentile of
      observed trend slopes at signal time and so blocked only 3.9% of signals --
      the filter was very nearly a no-op. 0.008 sits between the observed p50
      (0.41%) and p90 (1.21%) and blocks roughly the top quartile.

    - the stop was `1.5 * pstdev(last 50 closes)`. Standard deviation of raw price
      *levels* is dominated by trend drift rather than noise, and it made the stop
      distance a function of the same quantity as the entry trigger: since the
      target is the 20-bar mean, reward/risk reduced to sigma_20/sigma_50, so the
      risk engine's reward:risk floor was really a filter on volatility expansion
      -- the one regime where mean reversion reliably fails. ATR decouples the two.
    """
    min_history = max(
        window + 1, trend_window + trend_lookback, rsi_period + 1, atr_period + 1
    )
    if len(candles) < min_history:
        return None

    closes = [c[4] for c in candles]
    lookback = closes[-window - 1 : -1]
    price = closes[-1]
    mean = statistics.mean(lookback)
    std = statistics.pstdev(lookback)
    if std == 0:
        return None

    z = (price - mean) / std
    if abs(z) < z_threshold:
        return None

    trend_now = statistics.mean(closes[-trend_window:])
    trend_prior = statistics.mean(closes[-trend_window - trend_lookback : -trend_lookback])
    trend_slope_pct = (trend_now - trend_prior) / trend_prior
    if abs(trend_slope_pct) > trend_strength_threshold:
        return None  # regime filter: don't mean-revert against a strong trend in either direction

    stop_distance = atr(candles, atr_period)
    if stop_distance is None or stop_distance == 0:
        return None
    stop_distance *= atr_stop_mult

    rsi = _rsi(closes, rsi_period)

    if z <= -z_threshold:
        side = Side.LONG
        if rsi >= rsi_oversold:
            return None  # not actually oversold, RSI disagrees with the z-score
        stop = price - stop_distance
        target = mean
    else:
        side = Side.SHORT
        if rsi <= rsi_overbought:
            return None  # not actually overbought, RSI disagrees with the z-score
        stop = price + stop_distance
        target = mean

    confidence = min(abs(z) / (z_threshold * 2), 1.0)

    return Signal(
        asset=asset,
        asset_class="crypto",
        side=side,
        entry_price=price,
        confidence=confidence,
        timestamp=timestamp,
        rationale=(
            f"z-score {z:.2f} vs {window}-bar mean {mean:.2f} (threshold {z_threshold}); "
            f"trend slope {trend_slope_pct:+.2%} over {trend_lookback} bars; RSI {rsi:.1f}; "
            f"stop {atr_stop_mult}x ATR({atr_period})"
        ),
        suggested_stop=stop,
        suggested_target=target,
    )
