import asyncio
import math
import statistics
from datetime import datetime, timezone

import ccxt

from trading.data.crypto import fetch_ohlcv_range
from trading.strategy.crypto import generate_signal


def _segment_stats(candles, label):
    closes = [c[4] for c in candles]
    log_returns = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    hourly_vol = statistics.pstdev(log_returns) if len(log_returns) > 1 else 0.0
    annualized_vol = hourly_vol * math.sqrt(24 * 365)

    total_change_pct = (closes[-1] - closes[0]) / closes[0]

    # how often our own regime filter would call this "strongly trending"
    # (mirrors generate_signal's internal trend_slope_pct calc: 100-bar SMA slope
    # over a trailing 20-bar shift), sampled every 20 bars across the segment.
    trend_window, trend_lookback, threshold = 100, 20, 0.02
    trending_bars = 0
    sampled = 0
    for i in range(trend_window + trend_lookback, len(closes), 20):
        window = closes[: i + 1]
        trend_now = statistics.mean(window[-trend_window:])
        trend_prior = statistics.mean(window[-trend_window - trend_lookback : -trend_lookback])
        slope = (trend_now - trend_prior) / trend_prior
        sampled += 1
        if abs(slope) > threshold:
            trending_bars += 1
    pct_trending = trending_bars / sampled if sampled else 0.0

    # raw z-score>=2 signal frequency BEFORE any of our gates (regime/RSI/R:R)
    raw_signals = 0
    window = 20
    for i in range(window + 1, len(closes)):
        lookback = closes[i - window - 1 : i - 1]
        price = closes[i - 1]
        mean = statistics.mean(lookback)
        std = statistics.pstdev(lookback)
        if std == 0:
            continue
        z = (price - mean) / std
        if abs(z) >= 2.0:
            raw_signals += 1

    # signals that actually pass every gate in generate_signal (post-filter)
    passed_signals = 0
    for i in range(len(closes)):
        sig = generate_signal(candles[: i + 1], "X", datetime.now(timezone.utc))
        if sig is not None:
            passed_signals += 1

    print(f"\n{label}: {len(candles)} bars ({len(candles) / 24:.0f} days)")
    print(f"  total_change_pct        {total_change_pct:+.2%}")
    print(f"  annualized_volatility   {annualized_vol:.1%}")
    print(f"  pct_bars_trending       {pct_trending:.1%}  (abs(100-bar SMA slope) > 2%, sampled every 20 bars)")
    print(f"  raw_zscore_signals      {raw_signals}  (z>=2, before any gate)")
    print(f"  signals_passing_gates   {passed_signals}  (regime filter + RSI confirmation)")
    print(f"  gate_pass_rate          {passed_signals / raw_signals:.1%}" if raw_signals else "  gate_pass_rate          n/a")


async def main():
    exchange = ccxt.binance({"enableRateLimit": True})
    until = exchange.milliseconds()
    since = until - 270 * 24 * 60 * 60 * 1000
    candles = await fetch_ohlcv_range(exchange, "BTC/USDT", "1h", since, until)
    print(
        f"fetched {len(candles)} candles: "
        f"{datetime.fromtimestamp(candles[0][0] / 1000, tz=timezone.utc)} -> "
        f"{datetime.fromtimestamp(candles[-1][0] / 1000, tz=timezone.utc)}"
    )

    split_idx = int(len(candles) * 0.7)
    bad_period = candles[:split_idx]  # Nov 2025 -> ~May 2026, lost -17.81%
    good_period = candles[split_idx:]  # ~May 2026 -> Aug 2026, -1.02% but healthier win rate

    _segment_stats(bad_period, "BAD PERIOD (train, older -- lost -17.81% in the strategy backtest)")
    _segment_stats(good_period, "GOOD PERIOD (test, recent -- -1.02%, 57% win rate)")


if __name__ == "__main__":
    asyncio.run(main())
