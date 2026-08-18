import asyncio
from collections import Counter

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
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    result = run_backtest(train, "BTC/USDT", generate_signal, RiskEngine(), account)

    print("num trades:", len(result.trades))
    reasons = Counter(t.exit_reason for t in result.trades)
    print("exit reason breakdown:", dict(reasons))
    for reason in reasons:
        subset = [t for t in result.trades if t.exit_reason == reason]
        pnls = [t.net_pnl for t in subset]
        rs = [t.r_multiple for t in subset]
        wins = sum(1 for p in pnls if p > 0)
        print(f"  {reason}: n={len(subset)} wins={wins} total_pnl={sum(pnls):.0f} avg_r={sum(rs) / len(rs):.2f}")

    print()
    print("trade log (side, entry->exit, reason, R, pnl, running equity):")
    equity = 100_000.0
    for t in result.trades:
        equity += t.net_pnl
        print(
            f"  {t.side.value:5s} {t.entry_time.date()} -> {t.exit_time.date()}  "
            f"{t.exit_reason:10s} R={t.r_multiple:+.2f}  pnl={t.net_pnl:+8.0f}  equity={equity:9.0f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
