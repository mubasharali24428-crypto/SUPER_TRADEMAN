# DM-1 Status — Decimal call-site migration wave 1 (risk path) — COMPLETE

Repo: `AG PROJ1/algo-trading-system` @ `9a62e7a` (main). No commits made; all work in the working tree.
Owned scope respected: `src/trading/risk/engine.py`, `src/trading/risk/survival.py`, `tests/trading/risk/test_decimal_dual_run.py` (new), `tests/test_risk_engine.py` (untouched — no change needed), `tests/test_survival.py` (append-only). models.py NOT touched. Other dirty files in the tree belong to parallel squads (dr1/ax1/etc.).

## Design shipped (per plan risk #2 dual-run)

AccountState storage types unchanged. Both risk files now run a Decimal
computation alongside the legacy float path; legacy values still drive every
gate. Divergence is logged, never acted on.

### engine.py (+93 lines)
- New optional ctor param `instruments: Mapping[str,str] | None = None`
  (`{"BTC/USDT": "0.001"}` step sizes). None / empty map / asset-miss /
  empty-string step => quantization inactive => behavior byte-identical.
- At the single size point (`position_size = equity*risk_pct/risk_per_unit`),
  when a step is configured for `signal.asset`:
  - computes `size_decimal_candidate = quantize_to_step(size, step)` via
    trading.core.money (ROUND_DOWN floor convention);
  - returns `_QuantizedRiskDecision(RiskDecision)` — a subclass defined inside
    engine.py with `__slots__ = ("size_decimal_candidate",)` carrying the
    candidate. models.py frozen dataclasses untouched; parent eq/repr see only
    inherited fields so downstream comparisons are unaffected. Plain
    RiskDecision returned when inactive;
  - invariant: `|float(candidate) - legacy| <= one step`; beyond that logs
    **SIZE_DELTA** warning (asset, legacy_float, decimal_candidate, delta,
    tolerance);
  - quantizer failure (bad step) logs **SIZE_QUANTIZE_ERROR** and returns the
    carrier with candidate=None + legacy order (fail-open to legacy).
- Legacy float size/order fields unchanged and still consumed downstream.
- All 4 production `.evaluate()` callers pass `(signal, account)` only —
  signature extension verified safe (grep across src+tests).

### survival.py (+88 lines)
- `_epsilon_flips(account, max_drawdown, daily_loss_limit)` recomputes
  drawdown = (peak-equity)/peak and daily-loss = (equity-day_anchor)/day_anchor
  EXACTLY in Decimal from the SAME stored floats (`decimal_from_float`,
  str()-round-trip) and flags boundaries where **exact Decimal crosses but
  float does not** — i.e. false NEGATIVES of the float gate, the dangerous
  direction of F-0342. Daily-loss float side uses the STORED
  `daily_pnl_pct` (the actual gate input); drawdown float side uses the same
  inline expression as engine/survival gates.
- Wired at top of `_evaluate_raw_status`: each flip logged as
  **DRAWDOWN_EPSILON_FLIP [kind]** warning with exact-decimal value, limit,
  float value, F-0342 tag, "observation only" marker. Fires before any gate
  so the record exists even when the float path rejects; zero effect on tier
  selection. `day_start_equity()` reused as the anchor definition (single SoT).

## Verified numeric facts (F-0342 evidence, probe scripts run against repo venv)
- Classic false-positive: `(100000-82500)/100000 == 0.17500000000000004` → float
  breaches 0.175 that exact math does not.
- Constructive ulp-level search (nextafter around exact boundary) found genuine
  FALSE-NEGATIVE pairs used as test fixtures:
  - drawdown@0.175: peak=250000.31, equity=206250.25575 →
    float dd=0.174999999999999961 (< lim, gate misses) vs exact dd=0.175 (= lim).
  - daily-loss@0.025: anchor=10000.13, equity=9750.12675,
    stored pnl=-0.024999999999999977 (> -lim, gate misses) vs exact -0.025 (= -lim).

## Tests added (all green)
tests/trading/risk/test_decimal_dual_run.py (8):
floor-convention candidate @ injected step 0.001 (246.913578→Decimal("246.913"),
on-grid, ≤ legacy, within one step); byte-compat default (type is RiskDecision,
no candidate attr); asset-miss stays plain; empty-step string inactive;
healthy dual-run emits no SIZE_DELTA; sabotaged quantizer (monkeypatched seam,
candidate ~97 steps off) emits SIZE_DELTA with both values + tolerance, approval
unaffected; invalid negative step → SIZE_QUANTIZE_ERROR + legacy decision;
money.py boundary semantics (min-notional passes exactly on boundary).

tests/test_survival.py (+5 appended, existing 12 untouched):
drawdown epsilon-flip fixture asserts exact ≥ 0.175 > float; daily-loss flip on
stored-vs-exact mismatch; silence when paths agree (healthy + clear-breach);
end-to-end engine call logs DRAWDOWN_EPSILON_FLIP while tier stays CAUTION-via-
moderate-DD rule and NO cooldown fires (the missed kill switch made visible);
formatter unit test carries both values + F-0342 tag.

## VERIFY (real output)
`.venv/bin/python -m pytest tests/trading/risk/ tests/test_risk_engine.py tests/test_survival.py -q`
```
..............................................................           [100%]
62 passed (exit code 0)
```
Pre-existing risk suites untouched & green (incl. test_invariants.py,
test_capital_allocator.py, test_margin_monitor.py, test_portfolio_circuit_breaker.py).
Downstream consumer regression: `tests/test_backtest_portfolio.py` → 9 passed.

## Progress log
- [x] Repo located, HEAD verified 9a62e7a, ownership map confirmed
- [x] engine.py dual-run + _QuantizedRiskDecision carrier + SIZE_DELTA/SIZE_QUANTIZE_ERROR
- [x] survival.py epsilon-flip detector wired into _evaluate_raw_status
- [x] Tests written (13 new; existing suites untouched-green)
- [x] Full verify green (62 passed, exit 0); backtest consumer regression green

## Handoff notes for wave 2
- `_QuantizedRiskDecision` is the integration point: when models.py ownership
  allows, promote `size_decimal_candidate` to a real field and swap the subclass
  for the base class — call sites need no change.
- Detector currently observes only. Flip events are the audit trail for deciding
  per-boundary whether Decimal should take over gating (plan's wave-2 decision).
- `instruments` map shape matches ccxt market["amount"] precision strings so a
  live feed can be plugged in without translation.
