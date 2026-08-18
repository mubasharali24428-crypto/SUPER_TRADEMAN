"""Head-to-head: repaired mean reversion vs. trend following, train/test split.

Caches the candle fetch so repeated runs are fast. Delete the cache file to refetch.
"""
import asyncio
import json
import os
import sys

import ccxt

from trading.backtest.engine import (
    BacktestConfig,
    bootstrap_trade_returns,
    run_backtest,
    split_train_test,
)
from trading.data.crypto import fetch_ohlcv_range
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState, RiskConfig
from trading.strategy.crypto import generate_signal
from trading.strategy.trend import generate_trend_signal

CACHE = os.path.expanduser("~/.cache/algo-trading-system/btc_270d_1h.json")
DAYS = 270


async def load_candles():
    if os.path.exists(CACHE):
        return json.load(open(CACHE))
    exchange = ccxt.binance({"enableRateLimit": True})
    until = exchange.milliseconds()
    candles = await fetch_ohlcv_range(
        exchange, "BTC/USDT", "1h", until - DAYS * 24 * 60 * 60 * 1000, until
    )
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(candles, open(CACHE, "w"))
    return candles


def resample(candles, factor):
    """1h -> Nh. o=first, h=max, l=min, c=last, v=sum."""
    out = []
    for i in range(0, len(candles) - factor + 1, factor):
        chunk = candles[i : i + factor]
        out.append(
            [
                chunk[0][0],
                chunk[0][1],
                max(c[2] for c in chunk),
                min(c[3] for c in chunk),
                chunk[-1][4],
                sum(c[5] for c in chunk),
            ]
        )
    return out


def report(label, candles, strategy_fn, risk_engine, backtest_config):
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    result = run_backtest(
        candles, "BTC/USDT", strategy_fn, risk_engine, account, config=backtest_config
    )
    r = result.report
    boot = bootstrap_trade_returns(result.trades, account.equity, seed=42)
    edge = "YES" if r.win_rate > r.breakeven_win_rate and r.total_return_pct > 0 else "no"
    print(f"\n  {label}")
    print(f"    return {r.total_return_pct:+7.2%}   maxDD {r.max_drawdown_pct:6.2%}   "
          f"Sharpe/bar {r.sharpe_ratio:+.4f}")
    print(f"    win rate {r.win_rate:6.2%} (CI {r.win_rate_ci_low:.1%}-{r.win_rate_ci_high:.1%})"
          f"   vs breakeven {r.breakeven_win_rate:.2%}   p={r.win_rate_p_value:.3f}")
    print(f"    avg R {r.avg_r_multiple:+.3f}   trades {r.num_trades}   [{r.sample_size_verdict}]")
    print(f"    bootstrap p5 {boot['p5']:+.2%} / p50 {boot['p50']:+.2%} / p95 {boot['p95']:+.2%}"
          f"     profitable edge: {edge}")
    return r


async def main():
    candles_1h = await load_candles()
    print(f"BTC/USDT 1h, {len(candles_1h)} candles ({DAYS}d)")

    # Mean reversion: min_reward_risk drops from the global 2.0 to 1.0. Forcing 2:1 on a
    # mean-reversion strategy was a category error -- it is structurally a high-win-rate,
    # low-R:R trade, and the 2.0 floor was rejecting 87% of signals while selecting the
    # survivors for volatility expansion, the worst possible regime for it (ANTIGRAVITY D2).
    mr_engine = RiskEngine(RiskConfig(min_reward_risk=1.0))
    mr_config = BacktestConfig(max_hold_bars=24)

    # Trend: keeps the 2.0 floor (breakouts clear it naturally) and exits on a trailing
    # stop, because trend profit lives in the right tail that a fixed target would cut off.
    trend_engine = RiskEngine(RiskConfig(min_reward_risk=2.0))
    trend_config = BacktestConfig(max_hold_bars=500, trail_atr_mult=2.0)

    for name, data, fn, engine, cfg in [
        ("MEAN REVERSION (repaired) 1h", candles_1h, generate_signal, mr_engine, mr_config),
        ("TREND FOLLOWING 1h", candles_1h, generate_trend_signal, trend_engine, trend_config),
        ("TREND FOLLOWING 4h", resample(candles_1h, 4), generate_trend_signal, trend_engine, trend_config),
        ("TREND FOLLOWING 12h", resample(candles_1h, 12), generate_trend_signal, trend_engine, trend_config),
    ]:
        train, test = split_train_test(data, train_frac=0.7)
        print(f"\n{'=' * 78}\n{name}  ({len(data)} bars)")
        report("TRAIN (in-sample)", train, fn, engine, cfg)
        report("TEST  (out-of-sample)", test, fn, engine, cfg)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
