import argparse
import json
from pathlib import Path

from trading.backtest.portfolio_engine import run_portfolio_backtest, PortfolioResult
from trading.risk.engine import RiskEngine
from trading.risk.models import RiskConfig, AccountState
from trading.backtest.engine import BacktestConfig

def load_cached_candles(cache_dir: Path) -> dict:
    """Load cached OHLCV JSON files from the given directory.
    Expected structure: each file named `<asset>.json` containing a list of candles.
    Returns a mapping asset -> list of candles.
    """
    candles_by_asset = {}
    for file in cache_dir.glob("*.json"):
        asset = file.stem
        with file.open("r", encoding="utf-8") as f:
            candles_by_asset[asset] = json.load(f)
    return candles_by_asset

def simple_strategy(histories, asset, ts):
    """A deterministic demo strategy: always go LONG on the first bar.
    Returns a Signal or None.
    """
    # Import lazily to avoid circular imports at module load.
    from trading.risk.models import Signal, Side
    if not histories:
        return None
    # If no open position for this asset, generate entry signal on first candle.
    if len(histories) == 1:
        # Use close price as entry price.
        _, _, _, _, close, _ = histories[-1]
        return Signal(
            asset=asset,
            side=Side.LONG,
            entry_price=close,
            suggested_stop=None,
            suggested_target=None,
        )
    return None

def main():
    parser = argparse.ArgumentParser(description="Run portfolio backtest over cached OHLCV data.")
    parser.add_argument("--cache-dir", type=str, default="data/cache", help="Directory with per-asset JSON candle caches.")
    parser.add_argument("--trail", type=float, default=None, help="Trailing ATR multiplier (optional).")
    args = parser.parse_args()

    cache_path = Path(args.cache_dir)
    if not cache_path.is_dir():
        raise FileNotFoundError(f"Cache directory {cache_path} does not exist.")

    candles_by_asset = load_cached_candles(cache_path)
    if not candles_by_asset:
        raise ValueError("No candle files found in cache directory.")

    # Shared risk engine
    risk_engine = RiskEngine(RiskConfig())
    # Starting account state (default values)
    starting_account = AccountState()
    # Backtest configuration – you may adjust as needed.
    config = BacktestConfig(trail_atr_mult=args.trail)

    result: PortfolioResult = run_portfolio_backtest(
        candles_by_asset=candles_by_asset,
        strategy_fn=simple_strategy,
        risk_engine=risk_engine,
        starting_account=starting_account,
        config=config,
    )

    # Print concise summary
    print("Portfolio Backtest Summary")
    print(f"Initial equity: {result.report.starting_equity:.2f}")
    print(f"Final equity: {result.report.final_equity:.2f}")
    print(f"Total PnL: {result.report.final_equity - result.report.starting_equity:.2f}")
    print(f"Number of trades: {len(result.trades)}")
    print(f"Sharpe (annualized): {result.report.sharpe:.2f}")

    # Optionally dump full result JSON for further analysis.
    output_path = Path("portfolio_backtest_result.json")
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "report": result.report.__dict__,
                "trades": [t.__dict__ for t in result.trades],
                "equity_curve": result.equity_curve,
            },
            f,
            default=str,
            indent=2,
        )
    print(f"Full result written to {output_path.resolve()}")

if __name__ == "__main__":
    main()
