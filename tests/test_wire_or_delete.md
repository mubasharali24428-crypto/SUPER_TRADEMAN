# Wire-or-Delete Adjudication Log — SUB-09

Mission: adjudicate five allegedly-orphan modules. Pre-ruling: default DELETE unless a
consumer can be wired within this wave using ONLY owned files. Repo main @857b038.
Format is machine-checked by `tests/test_sub09_wire_or_delete.py::test_decision_log_complete`
(each module needs a `Verdict:` and at least one `Evidence:` file:line citation).

## backtest/funding.py
Verdict: DELETE
Evidence: repo-wide grep `from trading.backtest.funding|apply_funding|FundingEvent|PortfolioFundingTracker`
  over `src/` returns ZERO matches — the perps book applies no funding cost anywhere in
  production code today; the only importers are tests
  (`tests/integration/test_full_pipeline.py:11`, `tests/trading/backtest/test_funding.py:5`,
`tests/trading/risk/test_funding_tracker.py:5`).
Evidence: `src/trading/backtest/engine.py:18` carries another squad's
`TODO(F-0091)` on the fill path; applying funding at 8h boundaries there was FORBIDDEN
  this wave (engine.py outside SUB-09 ownership), and no owned file can consume
`apply_funding` meaningfully.
- Execution: deleted `src/trading/backtest/funding.py`,
`tests/trading/backtest/test_funding.py`, `tests/trading/risk/test_funding_tracker.py`;
  removed import + Phase-2 funding section from `tests/integration/test_full_pipeline.py`.
  Re-import check: `python -c "import trading.backtest.engine"` OK post-delete.

## backtest/impact.py
Verdict: KEEP (opt-in utility; documented Phase-2 wire target)
Evidence: `src/trading/backtest/impact.py:3` is a pure pass-through wrapper over
`trading.execution.tca.calculate_market_impact` (sqrt market-impact law) — zero side
  effects, safe to keep unconsumed.
Evidence: `src/trading/backtest/engine.py:18` and `engine.py:218` (another squad's
  TODO(F-0091)) explicitly name `backtest/impact.apply_market_impact` as the intended
  integration point for bar-volume-aware fills — i.e., a concrete Phase-2 wire plan
  already exists in the codebase's own documentation.
Evidence: consumed today by `tests/integration/test_full_pipeline.py:12` (Phase-5
  impact-cap assertion), so keeping costs nothing and retains regression coverage.
- Per pre-ruling allowance: kept + documented as opt-in / Phase-2 wire target instead
  of deleted. Wiring it into engine fills ourselves was forbidden (engine.py not owned).

## synthetic/stale_protection.py
Verdict: KEEP (already wired into production; NOT an orphan)
Evidence: `src/trading/synthetic/strategy_defense.py:305` CONSTRUCTS it —
`StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)` — contradicting
  the brief's claim ("constructed nowhere").
Evidence: `src/trading/synthetic/strategy_defense.py:309` `evaluate_stale_quotes()`
  consumes the instance to filter resting orders; import at `strategy_defense.py:16`.
Evidence: direct coverage at `tests/synthetic/test_stale_protection.py:9`.
- Deletion would break `strategy_defense` imports and its test suite; nothing to do.

## synthetic/ecology.py
Verdict: KEEP (production-consumed; partially wired subsystem)
Evidence: `src/trading/synthetic/strategy_defense.py:10` imports `LiquidityEvent`
  from ecology and consumes it at `strategy_defense.py:216`
  (`on_liquidity_event_killswitch(event: LiquidityEvent)`).
Evidence: agent classes exercised by `tests/synthetic/test_ecology.py:8` and
`tests/synthetic/test_sniper_mode_v2.py:5`.
- Brief called this an "orphaned sim subsystem"; that is wrong for `LiquidityEvent`
  (production dependency) even though the full agent registry has no production driver
  yet. Deleting would break `strategy_defense`; splitting the module was rejected as
  out-of-scope churn beyond exclusive ownership.

## synthetic/event_ingestor.py
Verdict: KEEP (already wired)
Evidence: `src/trading/synthetic/stocks_engine.py:9` and
`src/trading/synthetic/forex_engine.py:8` import from it (news/event ingestion feeds
  both non-crypto engines).
Evidence: covered by `tests/synthetic/test_event_ingestor.py:6` and
`tests/synthetic/test_multi_asset_integration.py:5`.

## Deviations & adjacent fixes (same wave)
- `_rsi` bug location: briefed as being in `src/trading/data/crypto.py`; actually lives
  in `src/trading/strategy/crypto.py:8`. Fixed at the real location (single self-contained
  function, claimed by no other squad per sub03/04/05/07 status files): proper Wilder
  recursive smoothing replaced the plain-mean divide-by-window-length bug, and windows
  shorter than `period+1` (or `period <= 0`) now return NaN instead of garbage;
`generate_signal` treats NaN RSI as disagreement (returns None) instead of silently
  passing the gate.
- `src/trading/data/crypto.py` pagination: `fetch_ohlcv_range` previously relied solely
  on empty-batch to stop; now also stops when a page returns fewer rows than requested
  OR when the cursor fails to advance (would have spun forever on a stuck/exhausted
  cursor), and logs per-page plus total rows fetched.
- Baseline honesty note: `pytest tests/trading/ -q` had 2 PRE-EXISTING failures in
`tests/trading/core/test_money.py` before any SUB-09 change (another squad's untracked
`src/trading/core/money.py` WIP). Not caused by and not fixed under SUB-09.
