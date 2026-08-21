import argparse
import time
from pathlib import Path

from trading.backtest.portfolio import run_portfolio_backtest
from trading.risk.engine import RiskEngine
from trading.risk.models import RiskConfig, AccountState, Signal, Side
from trading.backtest.engine import BacktestConfig

def generate_candles(num_bars: int, start_price: float = 100.0) -> list[list[float]]:
    """Generate flat OHLCV candles for simplicity.
    Each candle is [timestamp_ms, open, high, low, close, volume].
    """
    candles = []
    base_ts = int(time.time() * 1000)
    for i in range(num_bars):
        ts = base_ts + i * 86_400_000  # daily interval
        o = h = l = c = start_price
        v = 1.0
        candles.append([ts, o, h, l, c, v])
    return candles

def fixed_signal_strategy(fire_map):
    """Factory returning a strategy function that fires signals according to fire_map.
    fire_map: dict[asset] -> dict[bar_index] -> (Side, entry, stop, target)
    """
    fired = set()
    def fn(candles, asset, ts):
        idx = len(candles) - 1
        spec = fire_map.get(asset, {}).get(idx)
        if spec is None or (asset, idx) in fired:
            return None
        fired.add((asset, idx))
        side, entry, stop, target = spec
        return Signal(
            asset=asset,
            asset_class="crypto",
            side=side,
            entry_price=entry,
            confidence=1.0,
            timestamp=ts,
            rationale="benchmark",
            suggested_stop=stop,
            suggested_target=target,
        )
    return fn

def benchmark(num_assets: int, bars_per_asset: int, repetitions: int):
    # Build candle dict
    candles_by_asset = {
        f"ASSET{i}/USDT": generate_candles(bars_per_asset, start_price=100.0 + i)
        for i in range(num_assets)
    }
    # Fire a LONG entry at the last bar for each asset
    fire_map = {
        f"ASSET{i}/USDT": {bars_per_asset - 1: (Side.LONG, 100.0 + i, 95.0 + i, 110.0 + i)}
        for i in range(num_assets)
    }
    strategy_fn = fixed_signal_strategy(fire_map)
    risk_engine = RiskEngine(RiskConfig(risk_pct=0.01, min_reward_risk=2.0))
    starting_account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    config = BacktestConfig()

    times = []
    for _ in range(repetitions):
        start = time.perf_counter()
        _ = run_portfolio_backtest(
            candles_by_asset=candles_by_asset,
            strategy_fn=strategy_fn,
            risk_engine=risk_engine,
            starting_account=starting_account,
            config=config,
        )
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    avg_time = sum(times) / repetitions
    print(f"Benchmark results for {num_assets} assets, {bars_per_asset} bars each, {repetitions} repetitions:")
    print(f"  Avg execution time: {avg_time:.4f} s")
    print(f"  Min: {min(times):.4f}s, Max: {max(times):.4f}s")

def main():
    parser = argparse.ArgumentParser(description="Benchmark portfolio backtest performance.")
    parser.add_argument("--assets", type=int, default=5, help="Number of assets in the portfolio.")
    parser.add_argument("--bars", type=int, default=1000, help="Candles per asset.")
    parser.add_argument("--reps", type=int, default=5, help="Number of repetitions.")
    args = parser.parse_args()
    benchmark(args.assets, args.bars, args.reps)

if __name__ == "__main__":
    main()
