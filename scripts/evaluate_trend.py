"""Honest evaluation of trend following: multi-year, multi-asset, with a
sensitivity sweep on the trailing stop.

270 days of one asset cannot evaluate a trend system -- trend following earns its
return from a handful of large moves per year, so a 9-month single-symbol window is
mostly noise. This fetches multiple years across several majors and reports each
configuration's aggregate behaviour.
"""
import asyncio
import json
import math
import os
import statistics
import sys

import ccxt

from trading.backtest.engine import BacktestConfig, run_backtest, split_train_test
from trading.data.crypto import fetch_ohlcv_range
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState, RiskConfig
from trading.strategy.trend import generate_trend_signal

CACHE_DIR = os.path.expanduser("~/.cache/algo-trading-system")
SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"]
TIMEFRAME = os.environ.get("TF", "4h")
YEARS = float(os.environ.get("YEARS", 5))


async def load(exchange, symbol, timeframe, years):
    key = f"{symbol.replace('/', '')}_{timeframe}_{years}y.json"
    path = os.path.join(CACHE_DIR, key)
    if os.path.exists(path):
        return json.load(open(path))
    until = exchange.milliseconds()
    since = until - int(years * 365.25 * 24 * 60 * 60 * 1000)
    candles = await fetch_ohlcv_range(exchange, symbol, timeframe, since, until)
    os.makedirs(CACHE_DIR, exist_ok=True)
    json.dump(candles, open(path, "w"))
    return candles


def market_context(candles, label):
    closes = [c[4] for c in candles]
    buy_hold = (closes[-1] - closes[0]) / closes[0]
    log_r = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    # Kaufman efficiency ratio: net move / total path travelled, over 30-bar blocks.
    # Near 1 = clean trend, near 0 = chop. Trend systems need the high end.
    ers = []
    for i in range(30, len(closes), 30):
        block = closes[i - 30 : i + 1]
        path = sum(abs(b - a) for a, b in zip(block, block[1:]))
        if path:
            ers.append(abs(block[-1] - block[0]) / path)
    print(f"  {label:12s} buy&hold {buy_hold:+8.1%}   median efficiency ratio "
          f"{statistics.median(ers):.3f}   bars {len(candles)}")
    return statistics.median(ers)


def run(candles, symbol, trail, engine):
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    cfg = BacktestConfig(max_hold_bars=2000, trail_atr_mult=trail)
    return run_backtest(candles, symbol, generate_trend_signal, engine, account, config=cfg)


async def main():
    exchange = ccxt.binance({"enableRateLimit": True})
    data = {}
    print(f"market context ({TIMEFRAME}, {YEARS}y):")
    for sym in SYMBOLS:
        data[sym] = await load(exchange, sym, TIMEFRAME, YEARS)
        market_context(data[sym], sym)

    engine = RiskEngine(RiskConfig(min_reward_risk=2.0))
    print(f"\n{'trail':>6} {'split':>6} {'trades':>7} {'win%':>7} {'avgR':>8} "
          f"{'total R':>9} {'ret/sym':>9} {'exit mix (stop/target/time)':>28}")
    for trail in [None, 2.0, 3.0, 5.0, 8.0]:
        for split_name in ("train", "test"):
            all_trades = []
            returns = []
            for sym in SYMBOLS:
                train, test = split_train_test(data[sym], train_frac=0.7)
                window = train if split_name == "train" else test
                result = run(window, sym, trail, engine)
                all_trades.extend(result.trades)
                returns.append(result.report.total_return_pct)
            if not all_trades:
                print(f"{str(trail):>6} {split_name:>6} {'0':>7}")
                continue
            wins = [t for t in all_trades if t.net_pnl > 0]
            rs = [t.r_multiple for t in all_trades]
            mix = {k: sum(1 for t in all_trades if t.exit_reason == k)
                   for k in ("stop", "target", "time_stop", "end_of_data")}
            print(f"{str(trail):>6} {split_name:>6} {len(all_trades):>7} "
                  f"{len(wins) / len(all_trades):>6.1%} {statistics.mean(rs):>+8.3f} "
                  f"{sum(rs):>+9.1f} {statistics.mean(returns):>+8.2%}   "
                  f"{mix['stop']:>4}/{mix['target']:>4}/{mix['time_stop']:>4}/{mix['end_of_data']:>4}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
