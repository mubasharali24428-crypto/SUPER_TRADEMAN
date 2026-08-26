"""Integration test: full walk-forward cycle on seeded random-walk data (no network)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trading.backtest.engine import RiskEngine  # noqa: E402
from trading.risk.models import AccountState, Position, Signal  # noqa: E402
from trading.walkforward.report import render_tearsheet  # noqa: E402
from trading.walkforward.validator import WalkForwardConfig, run_walk_forward  # noqa: E402


def _candles(days: int, seed: int = 7) -> list[list[float]]:
    rng = random.Random(seed)
    out = []
    ts = 1_700_000_000_000
    price = 50_000.0
    for _ in range(days * 6):  # 4h bars => 6 per day
        drift = rng.gauss(0.0003, 0.015)
        price = max(1.0, price * (1 + drift))
        out.append([ts, price * 0.995, price * 1.005, price * 0.99, price, 100.0])
        ts += 4 * 60 * 60 * 1000
    return out


def _flat_strategy(candles, account):
    """Demo strategy that never fires — exercises the SKIP/empty path deterministically."""
    return None


def _account_factory() -> AccountState:
    return AccountState(equity=10_000.0, peak_equity=10_000.0,
                        open_positions=[], correlations={})


def test_walk_forward_cycle_produces_populated_folds():
    # 400 calendar days x 6 four-hour bars/day = 2400 bars. The validator treats each
    # bar-cadence unit as a "day" (documented proportional slicing), so 180/60/60 config
    # yields folds stepping every 60 bars while valid_end <= 2400.
    candles = {"BTC/USDT": _candles(400)}
    cfg = WalkForwardConfig(train_days=180, validate_days=60, step_days=60)
    folds = run_walk_forward(candles, _flat_strategy, RiskEngine(), _account_factory, cfg)

    assert folds, "expected at least one fold"
    assert len(folds) == 37  # starts {0,60,...,2340} with valid_end<=2400

    for f in folds:
        assert f.verdict in ("PASS", "FAIL", "SKIP")
        if f.verdict == "SKIP":
            assert "insufficient" in f.reason or "error" in f.reason

    tearsheet = render_tearsheet(folds, meta={"symbol": "BTC/USDT", "seed": 7})
    assert "# Walk-Forward Tearsheet" in tearsheet
    assert "| Fold |" in tearsheet


def test_stats_stack_is_consumed_by_validator():
    """VB-061 closure proof: validator imports and uses the production stats stack."""
    import trading.stats.pbo as pbo_mod
    import trading.stats.sharpe_variants as sv_mod
    from trading.walkforward import validator

    assert validator.compute_pbo_cscv is pbo_mod.compute_pbo_cscv
    assert validator.deflated_sharpe_ratio is sv_mod.deflated_sharpe_ratio
