import sys
import time
import logging
from pathlib import Path
from dataclasses import replace

# Suppress risk rejection logging during high-volume benchmark
logging.getLogger("trading.risk").setLevel(logging.ERROR)

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from trading.backtest.engine import run_backtest, BacktestConfig
from trading.backtest.strategies import generate_synthetic_candles, make_momentum_strategy
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState, RiskConfig
from trading.learning.graph import LearningGraph
from trading.learning.policy import ContextualBanditAllocator

def generate_trending_candles(num: int, start_price: float = 100.0, trend_drift: float = 0.0005, seed: int = 42):
    """Generates large volume synthetic candles with controlled trend drift."""
    import random
    from datetime import datetime, timezone
    rng = random.Random(seed)
    candles = []
    ts0 = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    price = start_price
    for i in range(num):
        change = rng.uniform(-0.015, 0.015) + trend_drift
        open_p = price
        high = price * (1 + max(change, 0) + 0.004)
        low = price * (1 + min(change, 0) - 0.004)
        close = price * (1 + change)
        candles.append([ts0 + i * 60_000, open_p, high, low, close, 0.0])
        price = close
    return candles


def run_10k_benchmark():
    print("=" * 120)
    print("SUPER_TRADEMAN: HIGH-VOLUME BENCHMARK (10,000+ TRADES PER CONFIGURATION)")
    print("=" * 120)

    # 120,000 candles produce thousands of trades per run
    CANDLE_COUNT = 120_000

    print(f"\nGenerating {CANDLE_COUNT:,} synthetic candles for high-sample statistical validity...")
    candles_trend_btc = generate_trending_candles(CANDLE_COUNT, start_price=100.0, trend_drift=0.0005, seed=42)
    candles_trend_eth = generate_trending_candles(CANDLE_COUNT, start_price=2000.0, trend_drift=0.0005, seed=99)
    candles_trend_sol = generate_trending_candles(CANDLE_COUNT, start_price=20.0, trend_drift=0.0005, seed=123)

    candles_rw_btc = generate_synthetic_candles(CANDLE_COUNT, start_price=100.0, seed=42)
    candles_rw_eth = generate_synthetic_candles(CANDLE_COUNT, start_price=2000.0, seed=99)
    candles_rw_sol = generate_synthetic_candles(CANDLE_COUNT, start_price=20.0, seed=123)

    def execute_suite(candles_btc, candles_eth, candles_sol, regime_name):
        configs = [
            {
                "name": "1. Baseline (1.0% Risk Cap, 17.5% Max DD)",
                "risk_cfg": RiskConfig(risk_pct=0.01, max_drawdown=0.175),
                "strategy": make_momentum_strategy(target_mult=0.03, stop_pct=0.01),
                "bt_cfg": BacktestConfig(),
                "account": AccountState(equity=10000.0, peak_equity=10000.0),
                "candles": candles_btc,
                "asset": "BTC/USDT",
            },
            {
                "name": "2. Low-Risk Base (0.5% Risk, 30% Max DD, Target 3%)",
                "risk_cfg": RiskConfig(risk_pct=0.005, max_drawdown=0.30, max_heat=0.10, correlation_threshold=0.5),
                "strategy": make_momentum_strategy(target_mult=0.03, stop_pct=0.01),
                "bt_cfg": BacktestConfig(),
                "account": AccountState(equity=10000.0, peak_equity=10000.0),
                "candles": candles_btc,
                "asset": "BTC/USDT",
            },
            {
                "name": "3. Low-Risk + Target 4% (R/R = 4:1 Reward:Risk)",
                "risk_cfg": RiskConfig(risk_pct=0.005, max_drawdown=0.30, max_heat=0.10, correlation_threshold=0.5),
                "strategy": make_momentum_strategy(target_mult=0.04, stop_pct=0.01),
                "bt_cfg": BacktestConfig(),
                "account": AccountState(equity=10000.0, peak_equity=10000.0),
                "candles": candles_btc,
                "asset": "BTC/USDT",
            },
            {
                "name": "4. Low-Risk + Trailing ATR Stop (2x ATR)",
                "risk_cfg": RiskConfig(risk_pct=0.005, max_drawdown=0.30, max_heat=0.10, correlation_threshold=0.5),
                "strategy": make_momentum_strategy(target_mult=0.04, stop_pct=0.01),
                "bt_cfg": BacktestConfig().with_trailing_atr(mult=2.0, period=14),
                "account": AccountState(equity=10000.0, peak_equity=10000.0),
                "candles": candles_btc,
                "asset": "BTC/USDT",
            },
            {
                "name": "5. High Drawdown Tolerance (Max DD = 50%)",
                "risk_cfg": RiskConfig(risk_pct=0.005, max_drawdown=0.50, max_heat=0.10, correlation_threshold=0.5),
                "strategy": make_momentum_strategy(target_mult=0.04, stop_pct=0.01),
                "bt_cfg": BacktestConfig().with_trailing_atr(mult=2.0, period=14),
                "account": AccountState(equity=10000.0, peak_equity=10000.0),
                "candles": candles_btc,
                "asset": "BTC/USDT",
            },
            {
                "name": "6. Volatility Filter Active (high_volatility=True)",
                "risk_cfg": RiskConfig(risk_pct=0.005, max_drawdown=0.50, max_heat=0.10, max_heat_high_vol=0.08),
                "strategy": make_momentum_strategy(target_mult=0.04, stop_pct=0.01),
                "bt_cfg": BacktestConfig().with_trailing_atr(mult=2.0, period=14),
                "account": AccountState(equity=10000.0, peak_equity=10000.0, high_volatility=True),
                "candles": candles_btc,
                "asset": "BTC/USDT",
            },
        ]

        results = []
        for item in configs:
            t0 = time.perf_counter()
            engine = RiskEngine(item["risk_cfg"])
            res = run_backtest(
                candles=item["candles"],
                asset=item["asset"],
                strategy_fn=item["strategy"],
                risk_engine=engine,
                starting_account=item["account"],
                config=item["bt_cfg"],
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            rpt = res.report
            results.append({
                "name": item["name"],
                "trades": rpt.num_trades,
                "win_rate": rpt.win_rate,
                "bayesian_wr": rpt.bayesian_win_rate,
                "avg_r": rpt.avg_r_multiple,
                "total_return_pct": rpt.total_return_pct,
                "max_dd_pct": rpt.max_drawdown_pct,
                "sharpe": rpt.sharpe_ratio,
                "sortino": rpt.sortino_ratio,
                "tail_var_99": rpt.tail_var_99,
                "runtime_ms": elapsed_ms,
            })

        # 7. Multi-Asset 10k Basket: BTC + ETH + SOL
        t0 = time.perf_counter()
        shared_risk_cfg = RiskConfig(risk_pct=0.005, max_drawdown=0.50, max_heat=0.10, max_heat_high_vol=0.08)
        shared_engine = RiskEngine(shared_risk_cfg)
        acc = AccountState(equity=10000.0, peak_equity=10000.0)
        strat = make_momentum_strategy(target_mult=0.04, stop_pct=0.01)
        bt_cfg = BacktestConfig().with_trailing_atr(mult=2.0, period=14)

        res_btc = run_backtest(candles_btc, "BTC/USDT", strat, shared_engine, acc, bt_cfg)
        acc_eth = replace(acc, equity=res_btc.report.final_equity, peak_equity=max(acc.peak_equity, res_btc.report.final_equity))
        res_eth = run_backtest(candles_eth, "ETH/USDT", strat, shared_engine, acc_eth, bt_cfg)
        acc_sol = replace(acc_eth, equity=res_eth.report.final_equity, peak_equity=max(acc_eth.peak_equity, res_eth.report.final_equity))
        res_sol = run_backtest(candles_sol, "SOL/USDT", strat, shared_engine, acc_sol, bt_cfg)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        all_trades = res_btc.trades + res_eth.trades + res_sol.trades
        wins = [t for t in all_trades if t.net_pnl > 0]
        wr = len(wins) / len(all_trades) if all_trades else 0
        bayesian_wr = (len(wins) + 1.0) / (len(all_trades) + 2.0)
        avg_r = sum(t.r_multiple for t in all_trades) / len(all_trades) if all_trades else 0
        combined_return = (res_sol.report.final_equity - 10000.0) / 10000.0
        combined_max_dd = max(res_btc.report.max_drawdown_pct, res_eth.report.max_drawdown_pct, res_sol.report.max_drawdown_pct)

        results.append({
            "name": "7. Multi-Asset 10k Basket (BTC + ETH + SOL)",
            "trades": len(all_trades),
            "win_rate": wr,
            "bayesian_wr": bayesian_wr,
            "avg_r": avg_r,
            "total_return_pct": combined_return,
            "max_dd_pct": combined_max_dd,
            "sharpe": (res_btc.report.sharpe_ratio + res_eth.report.sharpe_ratio + res_sol.report.sharpe_ratio) / 3,
            "sortino": (res_btc.report.sortino_ratio + res_eth.report.sortino_ratio + res_sol.report.sortino_ratio) / 3,
            "tail_var_99": max(res_btc.report.tail_var_99, res_eth.report.tail_var_99, res_sol.report.tail_var_99),
            "runtime_ms": elapsed_ms,
        })

        return results

    def print_table(results, title):
        print(f"\n### {title}")
        print(f"| {'Configuration':<45} | {'Trades':<8} | {'Win Rate':<8} | {'Bayes WR':<8} | {'Avg R':<6} | {'Total Return':<13} | {'Max DD':<6} | {'Sharpe':<6} | {'Sortino':<7} | {'Tail-VaR':<8} | {'Time (ms)':<9} |")
        print(f"|{'-'*47}|{'-'*10}|{'-'*10}|{'-'*10}|{'-'*8}|{'-'*15}|{'-'*8}|{'-'*8}|{'-'*9}|{'-'*10}|{'-'*11}|")
        for r in results:
            tot_ret_str = f"{r['total_return_pct']*100:,.1f}%" if abs(r['total_return_pct']) < 1e6 else f"{r['total_return_pct']:.2e}%"
            print(f"| {r['name']:<45} | {r['trades']:<8,} | {r['win_rate']:>7.1%} | {r['bayesian_wr']:>7.1%} | {r['avg_r']:>6.2f} | {tot_ret_str:>13} | {r['max_dd_pct']:>6.1%} | {r['sharpe']:>6.3f} | {r['sortino']:>7.3f} | {r['tail_var_99']*100:>7.2f}% | {r['runtime_ms']:>9.1f} |")

    trend_results = execute_suite(candles_trend_btc, candles_trend_eth, candles_trend_sol, "Trending / Momentum")
    rw_results = execute_suite(candles_rw_btc, candles_rw_eth, candles_rw_sol, "Random Walk / Chop")

    print_table(trend_results, "Regime A: Trending / Momentum Market (120,000 Candles)")
    print_table(rw_results, "Regime B: Random Walk / Chop Market (120,000 Candles)")

    # 10,000 Step Policy Gradient Convergence Simulation
    print("\n" + "=" * 120)
    print("10,000-STEP POLICY GRADIENT CONTEXTUAL BANDIT CONVERGENCE & TREND ANALYSIS")
    print("=" * 120)

    allocator = ContextualBanditAllocator(strategies=["trend_momentum", "mean_reversion", "breakout"])
    
    import random
    rng = random.Random(42)
    
    checkpoint_steps = [100, 500, 1000, 2500, 5000, 10000]
    print(f"\n| {'Step':<8} | {'Trend Mom Policy Prob':<22} | {'Mean Rev Policy Prob':<20} | {'Breakout Policy Prob':<20} | {'Leading Strategy':<18} |")
    print(f"|{'-'*10}|{'-'*24}|{'-'*22}|{'-'*22}|{'-'*20}|")

    for step in range(1, 10001):
        r_trend = rng.gauss(1.5, 1.0)
        r_mean = rng.gauss(-0.5, 1.0)
        r_break = rng.gauss(0.2, 1.0)

        allocator.update_from_trade("trend_momentum", reward_r=r_trend)
        allocator.update_from_trade("mean_reversion", reward_r=r_mean)
        allocator.update_from_trade("breakout", reward_r=r_break)

        if step in checkpoint_steps:
            summary = allocator.summary()
            tm_p = summary["trend_momentum"]["policy_prob"]
            mr_p = summary["mean_reversion"]["policy_prob"]
            bo_p = summary["breakout"]["policy_prob"]
            leading = max(summary.items(), key=lambda x: x[1]["policy_prob"])[0]
            print(f"| {step:<8,} | {tm_p:>21.2%} | {mr_p:>19.2%} | {bo_p:>19.2%} | {leading:<18} |")

if __name__ == "__main__":
    run_10k_benchmark()
