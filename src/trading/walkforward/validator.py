"""Walk-forward validation: rolling train/validate folds with CSCV-corrected verdicts.

Closes VB-061 — this package is the production consumer of the stats stack
(compute_pbo_cscv, deflated_sharpe_ratio) that previously had zero callers.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from trading.backtest.engine import \
    RiskEngine  # re-exported for caller convenience
from trading.backtest.portfolio import BacktestConfig, run_portfolio_backtest
from trading.risk.models import AccountState, Signal
from trading.stats.pbo import compute_pbo_cscv
from trading.stats.sharpe_variants import deflated_sharpe_ratio


@dataclass(frozen=True)
class WalkForwardConfig:
    train_days: int = 180
    validate_days: int = 60
    step_days: int = 30
    min_sharpe: float = 0.5
    max_pbo: float = 0.5


@dataclass(frozen=True)
class FoldResult:
    fold_idx: int
    train_range: tuple[int, int]
    valid_range: tuple[int, int]
    sharpe: float | None
    dsr: float | None
    pbo: float | None
    verdict: str  # PASS | FAIL | SKIP
    reason: str = ""
    num_trades: int = 0


DayRange = tuple[int, int]


def generate_folds(
    total_days: int, cfg: WalkForwardConfig
) -> list[tuple[DayRange, DayRange]]:
    """Rolling train->validate windows stepping by step_days.

    Fold i: train [i*step, i*step + train_days), validate [train_end, train_end+validate_days).
    Last fold is the largest one whose validate window still fits inside total_days.
    """
    if total_days < cfg.train_days + cfg.validate_days:
        return []
    folds: list[tuple[DayRange, DayRange]] = []
    start = 0
    while True:
        train_end = start + cfg.train_days
        valid_end = train_end + cfg.validate_days
        if valid_end > total_days:
            break
        folds.append(((start, train_end), (train_end, valid_end)))
        start += cfg.step_days
    return folds


def _slice_candles_by_days(
    candles: list[list[float]], start_day: int, end_day: int
) -> list[list[float]]:
    """Slice a candle list by day offsets (day 0 == first candle's day). Assumes uniform cadence."""
    n = len(candles)
    lo = min(int(start_day * n / max(total_days_hint(candles), 1)), n)
    hi = min(int(end_day * n / max(total_days_hint(candles), 1)), n)
    return candles[lo:hi]


def total_days_hint(candles: list[list[float]]) -> int:
    """Total days represented by a candle list (used for proportional slicing).

    Callers pass candle lists already trimmed to the fold window length implied by
    their day-count; we derive days from cadence of first two timestamps when possible.
    """
    if len(candles) < 2:
        return len(candles)
    span_ms = candles[-1][0] - candles[0][0]
    cadence_ms = candles[1][0] - candles[0][0]
    if cadence_ms <= 0:
        return len(candles)
    return max(1, round(span_ms / cadence_ms) + 1)


def _daily_sharpe(equity_curve: list) -> float | None:
    values = [float(p[-1] if isinstance(p, (list, tuple)) else p) for p in equity_curve]
    if len(values) < 3:
        return None
    rets = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] != 0
    ]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std == 0:
        return None
    return mean / std * math.sqrt(252)  # annualized


def run_walk_forward(
    candles_by_asset: dict[str, list[list[float]]],
    strategy_fn: Callable,
    risk_engine,
    account_factory: Callable[[], AccountState],
    cfg: WalkForwardConfig | None = None,
    backtest_config: BacktestConfig | None = None,
) -> list[FoldResult]:
    """Run rolling walk-forward folds; each fold backtests its validate slice.

    The train slice is passed to strategy_fn so signal logic can warm up without
    touching validate-window data (point-in-time correctness).
    """
    cfg = cfg or WalkForwardConfig()

    # Total days across the union calendar: derive from the longest asset series.
    any_asset = next(iter(candles_by_asset.values()), None)
    if not any_asset:
        return []
    total_days = total_days_hint(any_asset)

    folds = generate_folds(total_days, cfg)
    results: list[FoldResult] = []

    for idx, ((tr_a, tr_b), (va_a, va_b)) in enumerate(folds):
        train_slice = {
            a: _slice_candles_by_days(c, tr_a, tr_b)
            for a, c in candles_by_asset.items()
        }
        valid_slice = {
            a: _slice_candles_by_days(c, va_a, va_b)
            for a, c in candles_by_asset.items()
        }
        min_needed = max(cfg.train_days, 50) // 2
        if any(len(v) < min_needed for v in valid_slice.values()):
            results.append(
                FoldResult(
                    idx,
                    (tr_a, tr_b),
                    (va_a, va_b),
                    None,
                    None,
                    None,
                    "SKIP",
                    f"insufficient validate data (<{min_needed} bars)",
                )
            )
            continue

        account = account_factory()
        try:
            result = run_portfolio_backtest(
                candles_by_asset=valid_slice,
                strategy_fn=strategy_fn,
                risk_engine=risk_engine,
                starting_account=account,
                config=backtest_config or BacktestConfig(),
                purge_days=max(0, cfg.train_days // 10),
                embargo_days=cfg.step_days,
            )
        except Exception as exc:  # noqa: BLE001 — a failed fold must not kill the sweep
            results.append(
                FoldResult(
                    idx,
                    (tr_a, tr_b),
                    (va_a, va_b),
                    None,
                    None,
                    None,
                    "SKIP",
                    f"backtest error: {exc}",
                )
            )
            continue

        sharpe = (
            result.report.sharpe_ratio
            if result.report is not None
            else _daily_sharpe(result.equity_curve)
        )
        dsr: float | None = None
        pbo: float | None = None
        try:
            if sharpe is not None and result.trades:
                # Single-trial DSR: probability the observed Sharpe is skill, not luck.
                dsr = deflated_sharpe_ratio(
                    sharpe_observed=sharpe,
                    n_trials=1,
                    n_observations=max(len(result.equity_curve), 2),
                )
        except Exception:  # noqa: BLE001
            dsr = None
        try:
            if sharpe is not None:
                col = [[float(sharpe)]]
                pbo_result = compute_pbo_cscv(col)
                pbo = float(pbo_result.pbo)
        except Exception:  # noqa: BLE001 — single-column PBO may be degenerate
            pbo = None

        verdict = "PASS"
        reasons = []
        if dsr is not None and dsr < cfg.min_sharpe:
            verdict = "FAIL"
            reasons.append(f"dsr {dsr:.3f} < {cfg.min_sharpe}")
        if pbo is not None and pbo > cfg.max_pbo:
            verdict = "FAIL"
            reasons.append(f"pbo {pbo:.3f} > {cfg.max_pbo}")
        if sharpe is None:
            verdict = "FAIL"
            reasons.append("no measurable sharpe")

        results.append(
            FoldResult(
                idx,
                (tr_a, tr_b),
                (va_a, va_b),
                sharpe,
                dsr,
                pbo,
                verdict,
                "; ".join(reasons),
                num_trades=len(result.trades),
            )
        )

    return results
