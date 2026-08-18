"""Is daily trend following an edge, or just long-biased beta in a bull market?

The decisive test is the short book. If shorts are also profitable, the system is
trading trend. If all profit comes from longs while buy&hold ran +500-2000%, it is
levered beta wearing a strategy costume.
"""
import asyncio
import json
import os
import statistics
import sys

import ccxt

from trading.backtest.engine import BacktestConfig, bootstrap_trade_returns, run_backtest, split_train_test
from trading.data.crypto import fetch_ohlcv_range
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState, RiskConfig, Side
from trading.strategy.trend import generate_trend_signal

CACHE_DIR = os.path.expanduser("~/.cache/algo-trading-system")
SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"]


async def load(exchange, symbol):
    path = os.path.join(CACHE_DIR, f"{symbol.replace('/', '')}_1d_7.0y.json")
    if os.path.exists(path):
        return json.load(open(path))
    until = exchange.milliseconds()
    candles = await fetch_ohlcv_range(exchange, symbol, "1d", until - int(7 * 365.25 * 86400_000), until)
    os.makedirs(CACHE_DIR, exist_ok=True)
    json.dump(candles, open(path, "w"))
    return candles


async def main():
    exchange = ccxt.binance({"enableRateLimit": True})
    engine = RiskEngine(RiskConfig(min_reward_risk=2.0))
    cfg = BacktestConfig(max_hold_bars=2000, trail_atr_mult=5.0)

    print(f"{'symbol':10s} {'split':6s} {'trades':>6} {'ret':>8} {'maxDD':>7} {'Sharpe':>8} "
          f"{'longR':>8} {'shortR':>8} {'b&h':>9}")
    per_split = {"train": [], "test": []}
    for sym in SYMBOLS:
        candles = await load(exchange, sym)
        for name, window in zip(("train", "test"), split_train_test(candles, 0.7)):
            account = AccountState(equity=100_000.0, peak_equity=100_000.0)
            res = run_backtest(window, sym, generate_trend_signal, engine, account, config=cfg)
            longs = [t.r_multiple for t in res.trades if t.side is Side.LONG]
            shorts = [t.r_multiple for t in res.trades if t.side is Side.SHORT]
            bh = (window[-1][4] - window[0][4]) / window[0][4]
            per_split[name].extend(res.trades)
            print(f"{sym:10s} {name:6s} {res.report.num_trades:>6} "
                  f"{res.report.total_return_pct:>+7.2%} {res.report.max_drawdown_pct:>6.2%} "
                  f"{res.report.sharpe_ratio:>+8.4f} "
                  f"{sum(longs):>+8.1f} {sum(shorts):>+8.1f} {bh:>+8.1%}")

    print()
    for name, trades in per_split.items():
        longs = [t for t in trades if t.side is Side.LONG]
        shorts = [t for t in trades if t.side is Side.SHORT]
        boot = bootstrap_trade_returns(trades, 100_000.0, seed=42)
        print(f"{name.upper()} aggregate: {len(trades)} trades")
        for label, subset in (("LONG ", longs), ("SHORT", shorts)):
            if subset:
                rs = [t.r_multiple for t in subset]
                wins = sum(1 for t in subset if t.net_pnl > 0)
                print(f"  {label} n={len(subset):>4}  win {wins / len(subset):>5.1%}  "
                      f"avgR {statistics.mean(rs):>+6.3f}  totalR {sum(rs):>+7.1f}")
        print(f"  bootstrap terminal return: p5 {boot['p5']:+.2%}  p50 {boot['p50']:+.2%}  "
              f"p95 {boot['p95']:+.2%}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
