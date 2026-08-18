import asyncio
from datetime import datetime, timezone

import ccxt

from trading.backtest.engine import bootstrap_trade_returns, run_backtest, split_train_test
from trading.data.crypto import fetch_ohlcv_range
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState
from trading.strategy.crypto import generate_signal


async def main():
    exchange = ccxt.binance({"enableRateLimit": True})
    until = exchange.milliseconds()
    since = until - 270 * 24 * 60 * 60 * 1000  # 270 days: middle of the 6-12 month
    # window that's defensible before BTC's post-ETF regime shift makes older data
    # a different market structure (see Prompt 8 research).
    candles = await fetch_ohlcv_range(exchange, "BTC/USDT", "1h", since, until)
    print(
        f"fetched {len(candles)} candles: "
        f"{datetime.fromtimestamp(candles[0][0] / 1000, tz=timezone.utc)} -> "
        f"{datetime.fromtimestamp(candles[-1][0] / 1000, tz=timezone.utc)}"
    )

    train, test = split_train_test(candles, train_frac=0.7)

    for label, window in [("TRAIN (in-sample)", train), ("TEST (out-of-sample)", test)]:
        account = AccountState(equity=100_000.0, peak_equity=100_000.0)
        result = run_backtest(window, "BTC/USDT", generate_signal, RiskEngine(), account)
        r = result.report
        boot = bootstrap_trade_returns(result.trades, account.equity, seed=42)
        print(f"\n{label}: {len(window)} candles")
        print(f"  total_return_pct   {r.total_return_pct:.2%}")
        print(f"  max_drawdown_pct   {r.max_drawdown_pct:.2%}")
        print(f"  sharpe_ratio       {r.sharpe_ratio:.3f} (per-bar, not annualized)")
        print(f"  win_rate           {r.win_rate:.2%}  (95% CI {r.win_rate_ci_low:.1%}-{r.win_rate_ci_high:.1%})")
        print(f"  win_rate_p_value   {r.win_rate_p_value:.3f}  (H0: win rate = 33.3% breakeven)")
        print(f"  avg_r_multiple     {r.avg_r_multiple:.2f}")
        print(f"  num_trades         {r.num_trades}  -- {r.sample_size_verdict}")
        print(
            f"  bootstrap return   p5={boot['p5']:.2%}  p50={boot['p50']:.2%}  p95={boot['p95']:.2%}"
            "  (2000 resamples of realized trades, order/selection-independent)"
        )


if __name__ == "__main__":
    asyncio.run(main())
