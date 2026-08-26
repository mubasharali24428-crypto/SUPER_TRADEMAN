# WALK_FORWARD.md — Walk-Forward Validation

## What it does
Rolling train/validate folds over candle history. Each fold backtests the validate
slice through the real portfolio engine, then scores it with the production stats
stack: Sharpe (from fold equity curve), Deflated Sharpe Ratio (single-trial), and
CSCV PBO. Verdict FAIL when DSR < min_sharpe or PBO > max_pbo.

This is the production consumer of `trading.stats.*` — closing register finding VB-061
(stats stack previously had zero callers).

## Usage

CLI (synthetic demo data):
    .venv/bin/python scripts/walk_forward.py --symbol BTC/USDT --days 400 \
        --train 180 --validate 60 --step 60 --seed 42

Programmatic:
    from trading.walkforward import run_walk_forward, WalkForwardConfig
    folds = run_walk_forward(candles_by_asset, strategy_fn, risk_engine,
                             account_factory, WalkForwardConfig(...))

API (authenticated):
    GET  /api/walkforward/status   (VIEWER+)   -> last run summary + due flag
    POST /api/walkforward/trigger  (OPERATOR+) -> kick a cycle

## Fold math
Fold i (0-based): train [i*step, i*step+train), validate [train_end, +validate).
Folds step by step_days; generation stops when the validate window exceeds total.
Insufficient-data folds return verdict SKIP with reason — never silent passes.

## Verdict rules
- FAIL if DSR < min_sharpe OR CSCV PBO > max_pbo OR no measurable Sharpe.
- SKIP on insufficient data or fold backtest error (error text preserved in reason).
- PASS requires measurable Sharpe with DSR/PBO inside thresholds.

## Notes
- Day units are bar-cadence units of the input series (proportional slicing);
  feed daily bars for calendar-day semantics.
- Train slices are passed to strategy_fn for warmup; validate slices are scored
  out-of-sample (point-in-time).
- Evidence tearsheets persist via deployment_metrics in scheduler service mode.
