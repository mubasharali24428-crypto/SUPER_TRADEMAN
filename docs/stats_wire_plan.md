# Stats-Stack Wiring Plan (VB-061 / VB-007)

**Status:** WIRE-PLANNED (wave-6 RECT-ALPHA). The multiple-testing stack
(`src/trading/stats/`) is library-grade tested but has zero production call
sites; this document pins the exact first integration so the next squad can
execute it mechanically. Wiring touches `scripts/validate_crypto_backtest.py`
and/or CI promotion checks, which sit outside the wave-6 RECT-ALPHA owned-file
set (`src/trading/stats/*`, `src/trading/risk/hmm_regime.py`,
`src/trading/risk/equity.py`, `src/trading/data/*` + their tests), hence a
plan instead of a diff.

## Current state (verified by grep @ bf03115 + wave-6 edits)

| Symbol | Defined in | Production callers |
|---|---|---|
| `effective_trials` | stats/effective_trials.py | **0** (tests only) |
| `deflated_sharpe_ratio` | stats/sharpe_variants.py | **0** |
| `probabilistic_sharpe_ratio` | stats/sharpe_variants.py | **0** |
| `expected_max_sharpe` | stats/sharpe_variants.py | **0** |
| `estimate_moments` (new, VB-079) | stats/sharpe_variants.py | **0** |
| `compute_pbo_cscv` | stats/pbo.py | **0** |

## Target consumer #1 — `scripts/validate_crypto_backtest.py`

The script already fetches 270d of 1h candles, runs `run_backtest` per
window, and prints `result.report`. Add a "multiple-testing block" under each
window label:

```python
from trading.stats.effective_trials import effective_trials
from trading.stats.sharpe_variants import (
    deflated_sharpe_ratio,
    estimate_moments,
)
from trading.stats.pbo import CSCVConfig, compute_pbo_cscv

# 1. Per-period return series of the selected strategy.
#    Use bar-level equity marks if available; otherwise trade returns via
#    the existing bootstrap helper:
trade_returns = bootstrap_trade_returns(result.trades, account.equity, seed=42)
sr, g3, g4 = estimate_moments(trade_returns)   # biased Pearson moments ONLY

# 2. Deflate against the trial count actually tried this session.
N = int(os.environ.get("STRATEGY_TRIALS", "1"))
T = len(trade_returns)
var_sr_trials = float(np.var(trial_sharpes)) if N > 1 else 1.0 / T
dsr = deflated_sharpe_ratio(
    sharpe_observed=sr,
    n_trials=N,
    n_observations=T,
    skew=g3,
    kurtosis=g4,
    var_sharpe_trials=var_sr_trials,
)
print(f"  dsr(N={N})         {dsr:.3f}  {'PASS' if dsr >= 0.95 else 'FAIL'}")

# 3. Effective trials from the trial-return matrix M (n_obs, N).
#    REQUIRED wrapping (VB-007 caller contract): np.corrcoef output can be
#    numerically inadmissible -> map failure to conservative N_eff = N.
try:
    n_eff = effective_trials(M)
except ValueError as exc:
    logger.warning("effective_trials inadmissible rho (%s); using N", exc)
    n_eff = float(N)

# 4. PBO over the trial matrix (needs >= 32 obs; use 16 blocks).
pbo_res = compute_pbo_cscv(M, CSCVConfig())
gate_pbo = max(pbo_res.pbo, pbo_res.max_conditional_pbo)   # VB-003 gate
```

## Target consumer #2 — promotion CI check

In whatever job gates a strategy promotion, fail when ANY of:

- `dsr < 0.95`
- `gate_pbo > 0.5` (pooled **or** max conditional — see VB-003 rationale)
- `n_eff < 2` while `N > 10` (trial family too correlated to deflate honestly;
  report both numbers so reviewers see the dilution)

## Test obligations when wired

- Extend `tests/integration/test_full_pipeline.py` (it already imports the
  stats modules) with an end-to-end assertion that the new report fields are
  finite and in range.
- One negative test: promotion check FAILS on a synthetic overfit field
  (reuse the `_synthetic_field` pattern from `tests/trading/stats/test_pbo_cscv.py`).

## Non-goals

- No wiring into the live daemon heartbeat: PSR/DSR are backtest-selection
  statistics, not runtime risk gates.
- Do NOT feed pandas `.skew()`/`.kurt()` outputs into DSR (VB-026/VB-079 units
  trap); always go through `estimate_moments`.
