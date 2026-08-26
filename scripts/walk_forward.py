"""CLI: run a walk-forward validation cycle on synthetic seeded data (demo) or real candles."""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.risk.engine import RiskEngine  # noqa: E402
from trading.risk.models import AccountState  # noqa: E402
from trading.walkforward.report import render_tearsheet  # noqa: E402
from trading.walkforward.validator import WalkForwardConfig, run_walk_forward  # noqa: E402


def _synthetic_candles(days: int, seed: int = 42) -> list[list[float]]:
    rng = random.Random(seed)
    candles = []
    ts = 1_700_000_000_000
    price = 60_000.0
    cadence_ms = 24 * 60 * 60 * 1000  # daily bars
    for _ in range(days):
        drift = rng.gauss(0.0005, 0.02)
        price = max(1.0, price * (1 + drift))
        candles.append([ts, price * 0.99, price * 1.01, price * 0.985, price, 1000.0])
        ts += cadence_ms
    return candles


def _strategy_fn(candles, account):  # pragma: no cover — demo strategy hook
    return None


def _account_factory() -> "AccountState":
    from trading.risk.models import Position

    return AccountState(equity=10_000.0, peak_equity=10_000.0,
                        open_positions=[], correlations={})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a walk-forward validation cycle.")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument("--train", type=int, default=180)
    parser.add_argument("--validate", type=int, default=60)
    parser.add_argument("--step", type=int, default=30)
    parser.add_argument("--days", type=int, default=400, help="total synthetic history length")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    candles = _synthetic_candles(args.days, seed=args.seed)
    cfg = WalkForwardConfig(train_days=args.train, validate_days=args.validate, step_days=args.step)
    folds = run_walk_forward(
        candles_by_asset={args.symbol: candles},
        strategy_fn=_strategy_fn,
        risk_engine=RiskEngine(),
        account_factory=_account_factory,  # fresh 10k demo account per fold
        cfg=cfg,
    )
    print(render_tearsheet(folds, meta={"symbol": args.symbol, "timeframe": args.timeframe,
                                        "config": vars(args)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
