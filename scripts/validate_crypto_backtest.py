import asyncio
from datetime import datetime, timezone

import ccxt

import numpy as np

from trading.backtest.engine import bootstrap_trade_returns, run_backtest, split_train_test
from trading.data.crypto import fetch_ohlcv_range
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState
from trading.stats.pbo import compute_pbo_cscv
from trading.stats.sharpe_variants import deflated_sharpe_ratio, probabilistic_sharpe_ratio
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

        # VB-061: Deflated Sharpe Ratio (DSR) — probability of skill after
        # correcting for selection bias across multiple strategy trials.
        if r.num_trades >= 5:
            trades_arr = np.array(result.trades)
            returns = np.diff(trades_arr) / trades_arr[:-1] if len(trades_arr) > 1 else np.array([0.0])
            n_obs = max(1, len(returns))
            skew = float(np.mean((returns - np.mean(returns))**3) / (np.std(returns)**3 + 1e-12)) if n_obs > 2 else 0.0
            kurt = float(np.mean((returns - np.mean(returns))**4) / (np.std(returns)**4 + 1e-12)) + 3.0 if n_obs > 2 else 3.0
            n_trials = 10  # assume ~10 strategies considered; conservative floor
            dsr = deflated_sharpe_ratio(
                sharpe_observed=r.sharpe_ratio, n_trials=n_trials,
                n_observations=n_obs, skew=skew, kurtosis=kurt,
            )
            print(f"  dsr (deflated sharpe) {dsr:.3f}  (n_trials={n_trials}, n_obs={n_obs})")

            # Probabilistic Sharpe Ratio (PSR) against zero benchmark
            psr = probabilistic_sharpe_ratio(
                benchmark_sharpe=0.0, sharpe_observed=r.sharpe_ratio,
                n_observations=n_obs, skew=skew, kurtosis=kurt,
            )
            print(f"  psr (vs 0)           {psr:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
