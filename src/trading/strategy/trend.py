from datetime import datetime

from trading.indicators import atr, donchian
from trading.risk.models import Side, Signal


def generate_trend_signal(
    candles: list[list[float]],
    asset: str,
    timestamp: datetime,
    entry_lookback: int = 55,
    atr_period: int = 14,
    atr_stop_mult: float = 2.0,
    atr_target_mult: float = 6.0,
) -> Signal | None:
    """Donchian channel breakout -- classic time-series momentum / trend following.

    Long when the close breaks above the highest high of the prior `entry_lookback`
    bars, short on the mirror. Stop at `atr_stop_mult` x ATR. Defaults are the
    published Turtle parameters (55-bar entry, 2x ATR stop), deliberately untuned:
    they are a starting point, not a fitted result.

    Why this strategy sits alongside the mean-reversion one rather than replacing it:

    - It is the complement, not a competitor. `strategy/crypto.py` explicitly
      refuses to trade when the trend is strong; this one trades only then. The
      two cover disjoint regimes.
    - Trend following genuinely produces reward:risk above 2, so the risk engine's
      existing `min_reward_risk=2.0` floor is correct for it as written -- unlike
      mean reversion, where that same floor was silently selecting for the worst
      possible entries (see ANTIGRAVITY.md D2).
    - Fewer, longer-held trades means the ~0.4% round-trip friction is amortised
      over a much larger move. Friction was costing the 1h mean-reversion strategy
      about 0.33R per trade (D3).

    The target here is a backstop, not the plan. Trend-following profit lives in
    the right tail, so the real exit is the backtester's trailing stop
    (`BacktestConfig.trail_atr_mult`). A fixed target would cap the exact outcome
    the strategy exists to capture.
    """
    if len(candles) < max(entry_lookback + 1, atr_period + 1):
        return None

    channel = donchian(candles[:-1], entry_lookback)  # exclude current bar: it can't break its own high
    if channel is None:
        return None
    channel_high, channel_low = channel

    stop_distance = atr(candles, atr_period)
    if stop_distance is None or stop_distance == 0:
        return None

    price = candles[-1][4]

    if price > channel_high:
        side = Side.LONG
        stop = price - atr_stop_mult * stop_distance
        target = price + atr_target_mult * stop_distance
        breached = channel_high
    elif price < channel_low:
        side = Side.SHORT
        stop = price + atr_stop_mult * stop_distance
        target = price - atr_target_mult * stop_distance
        breached = channel_low
    else:
        return None

    # how far beyond the channel the breakout closed, in ATRs -- a decisive break
    # is a stronger signal than one that closed a hair over the line
    excursion_atr = abs(price - breached) / stop_distance
    confidence = min(excursion_atr, 1.0)

    return Signal(
        asset=asset,
        asset_class="crypto",
        side=side,
        entry_price=price,
        confidence=confidence,
        timestamp=timestamp,
        rationale=(
            f"{entry_lookback}-bar Donchian breakout: close {price:.2f} vs channel "
            f"{channel_low:.2f}-{channel_high:.2f} ({excursion_atr:.2f} ATR beyond); "
            f"stop {atr_stop_mult}x ATR({atr_period})"
        ),
        suggested_stop=stop,
        suggested_target=target,
    )
