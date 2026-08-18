import asyncio
import statistics

import ccxt

from trading.backtest.engine import run_backtest, split_train_test
from trading.data.crypto import fetch_ohlcv_range
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState
from trading.strategy.crypto import generate_signal


async def main():
    exchange = ccxt.binance({"enableRateLimit": True})
    until = exchange.milliseconds()
    since = until - 270 * 24 * 60 * 60 * 1000
    candles = await fetch_ohlcv_range(exchange, "BTC/USDT", "1h", since, until)
    train, _test = split_train_test(candles, train_frac=0.7)
    closes = [c[4] for c in train]
    ts_to_idx = {c[0] // 1000: i for i, c in enumerate(train)}

    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    result = run_backtest(train, "BTC/USDT", generate_signal, RiskEngine(), account)

    print(f"{'reason':10s} {'entry_std(50)':>14s} {'realized_std(24 after)':>24s} {'ratio':>7s}")
    for t in result.trades:
        entry_ts = int(t.entry_time.timestamp())
        idx = ts_to_idx.get(entry_ts)
        if idx is None or idx < 51 or idx + 24 >= len(closes):
            continue
        entry_std = statistics.pstdev(closes[idx - 50 : idx])
        realized_std = statistics.pstdev(closes[idx : idx + 24])
        ratio = realized_std / entry_std if entry_std else float("nan")
        print(f"{t.exit_reason:10s} {entry_std:14.1f} {realized_std:24.1f} {ratio:7.2f}")

    stop_ratios = []
    other_ratios = []
    for t in result.trades:
        entry_ts = int(t.entry_time.timestamp())
        idx = ts_to_idx.get(entry_ts)
        if idx is None or idx < 51 or idx + 24 >= len(closes):
            continue
        entry_std = statistics.pstdev(closes[idx - 50 : idx])
        realized_std = statistics.pstdev(closes[idx : idx + 24])
        if entry_std == 0:
            continue
        ratio = realized_std / entry_std
        (stop_ratios if t.exit_reason == "stop" else other_ratios).append(ratio)

    if stop_ratios:
        print(f"\nSTOP trades: mean realized/entry vol ratio = {statistics.mean(stop_ratios):.2f} (n={len(stop_ratios)})")
    if other_ratios:
        print(f"OTHER trades: mean realized/entry vol ratio = {statistics.mean(other_ratios):.2f} (n={len(other_ratios)})")


if __name__ == "__main__":
    asyncio.run(main())
