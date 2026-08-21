# ANTIGRAVITY NEXT STEP — SUPER_TRADEMAN Phase 11: Deployment Readiness Harness

## 1. CONTEXT

The `SUPER_TRADEMAN` core engine is complete.

Completed and verified:

- Phase 0: Sovereign Invariant Protection
- Phase 1: Causal CPCV, PBO, Effective Trials
- Phase 2: Funding Rates, Liquidation, Economic Realism
- Phase 3: Data-Quality Engine
- Phase 4: Event-Sourced OMS and Reconciler
- Phase 5: Market Impact and TCA
- Phase 6: Observability and Execution Mode Hierarchy
- Phase 7: Integration Tests
- Phase 8: Shadow Mode and Staleness Sentinel
- Phase 9: Live Execution Resolution and Chase Logic
- Phase 10: Portfolio Risk Aggregation and Capital Allocation

Current known test state:

```text
144 tests total
142 passed
2 skipped
0 failed
```

The engineering question has been answered:

> Can we build a mathematically sound, architecturally safe, operationally resilient trading system?

The next question is different:

> Can we prove the system is ready for live capital without actually risking live capital prematurely?

Therefore, the next phase is **not** strategy research, and it is **not** live trading.

The next phase is:

# Phase 11: Deployment Readiness Harness

---

## 2. PRIME DIRECTIVE

Antigravity must build the operational tooling required to safely validate, drill, reconcile, and promote the system toward live deployment.

The immediate objective is:

> Prepare the system for a disciplined Shadow Mode validation campaign.

Antigravity must **not**:

- send real orders to production exchanges,
- promote the system to `LIVE_RESTRICTED` automatically,
- weaken any Sovereign Architectural Invariant,
- bypass `RiskEngine` order issuance,
- directly construct `ApprovedOrder` or `ApprovedExit`,
- modify passing tests to make them pass,
- add live trading shortcuts for convenience,
- store secrets in source control.

The system must remain safe by default.

---

## 3. SOVEREIGN CONSTRAINTS

All new work must preserve the following invariants:

### 3.1 Order Issuance

`ApprovedOrder` and `ApprovedExit` must only be constructed by `RiskEngine` using the private `_ISSUER` sentinel.

Any operational drill, flatten event, reconciliation process, or reporting tool must use existing sanctioned pathways.

If a drill requires an exit, it must trigger the appropriate risk/engine mechanism. It must not mint exit objects directly.

### 3.2 Exit Evaluation

`evaluate_exit_signal()` must never be blocked by:

- drawdown limits,
- kill-switches,
- reconciliation warnings,
- stale data blocks,
- capital allocator rejections,
- operational drill states.

Operational safety may block **new entries**, but never exit evaluation.

### 3.3 Position Sizing

Position sizing must remain strictly:

```text
size = (Equity * risk_pct) / abs(Entry - Stop)
```

The Capital Allocator may either approve the full formula-derived size or reject the signal.

It must never output a scaled, partial, approximate, or manually adjusted order size.

### 3.4 R-Multiple

R-multiple must always be calculated against the initial stop price.

Do not introduce trailing-stop logic into R-multiple accounting.

### 3.5 Stop-Fill Assumption

Backtest stop-fill assumptions must remain pessimistic.

Do not relax stop-fill modeling to improve performance metrics.

---

## 4. OBJECTIVE

Build the following operational modules, scripts, reports, and documentation:

1. Shadow Mode Validation Reporter
2. Deployment Preflight Checker
3. Kill-Switch Drill Harness
4. Reconciliation Report Generator
5. Mode Promotion Guard
6. Deployment Runbook Documentation
7. Deployment Metrics Persistence
8. Operational Test Coverage

The deliverable is a safe operational bridge from:

```text
BACKTEST / PAPER / SHADOW
```

to:

```text
LIVE_RESTRICTED
```

and eventually:

```text
LIVE_FULL
```

---

# 5. WORKSTREAM 1 — SHADOW MODE VALIDATION REPORTER

## 5.1 Purpose

Create a reporting tool that evaluates whether Shadow Mode behavior is consistent with backtest expectations.

This tool will be used during the 3-4 week Shadow Mode validation period.

## 5.2 File to Create

```text
scripts/shadow_report.py
```

## 5.3 Requirements

The script must generate a daily and cumulative Shadow Mode validation report.

It must read from local persistence, logs, and/or metrics tables.

It must support:

```bash
python scripts/shadow_report.py --days 20
python scripts/shadow_report.py --symbol BTC/USDT:USDT
python scripts/shadow_report.py --format json
python scripts/shadow_report.py --format table
```

## 5.4 Metrics to Track

For each day and for the cumulative period, report:

### Signal Metrics

```text
signals_generated
signals_approved
signals_rejected_capital
signals_rejected_stale_data
signals_rejected_portfolio_risk
signals_rejected_liquidity
```

### Execution Metrics

```text
shadow_fills_generated
liquidity_deficit_events
liquidity_deficit_pct
avg_signal_to_fill_latency_ms
p95_signal_to_fill_latency_ms
p99_signal_to_fill_latency_ms
avg_shadow_slippage_bps
p95_shadow_slippage_bps
```

### Market Data Health

```text
staleness_circuit_breaker_trips
websocket_disconnect_events
out_of_order_tick_events
data_quality_failures
```

### Portfolio Metrics

```text
shadow_pnl
shadow_pnl_pct
max_shadow_drawdown_pct
portfolio_exposure_pct
effective_leverage
portfolio_volatility_annualized
portfolio_var_95
portfolio_var_99
funding_burn_pct_daily
```

### Reconciliation Metrics

```text
reconciliation_runs
reconciliation_mismatches
quarantine_events
unknown_order_events
position_mismatch_events
balance_mismatch_events
```

### Backtest Comparison

```text
backtest_expected_pnl_pct
backtest_pnl_std_dev
shadow_tracking_error
shadow_pnl_z_score
```

If no backtest benchmark exists, the report must mark the comparison as:

```text
MISSING_BACKTEST_BENCHMARK
```

and the Go/No-Go gate must fail.

## 5.5 Go/No-Go Gate 1

The report must compute Gate 1 automatically.

Gate 1 passes only if all of the following are true over the requested evaluation window:

```text
shadow_pnl_z_score >= -1.5
shadow_pnl_z_score <= +1.5
staleness_circuit_breaker_trips <= 3 per week
liquidity_deficit_pct <= 0.05
reconciliation_mismatches == 0
unknown_order_events == 0
position_mismatch_events == 0
balance_mismatch_events == 0
backtest_benchmark_present == true
```

Output:

```text
GATE_1_STATUS: PASS
```

or:

```text
GATE_1_STATUS: FAIL
```

The report must list every failed condition.

## 5.6 Acceptance Criteria

- The script runs without network access if local data is available.
- It supports JSON and table output.
- It does not mutate trading state.
- It does not submit orders.
- It is strictly typed.
- It includes pytest coverage.

---

# 6. WORKSTREAM 2 — DEPLOYMENT PREFLIGHT CHECKER

## 6.1 Purpose

Create a preflight checker that validates whether the system is allowed to enter Shadow Mode or remain in Shadow Mode.

This must run before every deployment and before every mode promotion.

## 6.2 File to Create

```text
scripts/preflight_check.py
```

## 6.3 Requirements

The preflight checker must validate:

### Configuration Checks

```text
execution_mode is valid
risk_pct is configured
max_concurrent_positions is configured
max_portfolio_exposure_pct is configured
max_single_asset_pct is configured
chase_timeout_ms is configured
staleness_threshold_ms is configured
funding_burn_threshold_pct is configured
```

### Security Checks

```text
API keys are not committed to source control
API keys are loaded from environment or secret manager
withdrawal permission is not detectable if exchange API supports permission introspection
IP allowlist is configured if exchange supports it
production mode is not enabled with test credentials
```

### Database Checks

```text
database connection is available
required tables exist
order_intent table exists
funding_rates table exists
deployment_metrics table exists or can be created
no failed migrations are present
```

### Market Data Checks

```text
required symbols are configured
OHLCV data is fresh
funding rate data is fresh
data quality checks pass
```

### Execution Checks

```text
reconciler is healthy
no unresolved quarantine state exists
daemon singleton lock is available or held by current process
OMS state machine is initialized
venue adapter is initialized in read-only or appropriate mode
```

### Test Checks

```text
full pytest suite passes
invariant tests pass
Phase 8 tests pass
Phase 9 tests pass
Phase 10 tests pass
```

## 6.4 Output

The script must output:

```text
PREFLIGHT_STATUS: PASS
```

or:

```text
PREFLIGHT_STATUS: FAIL
```

It must list every failed check with severity:

```text
BLOCKING
WARNING
```

Only `BLOCKING` failures prevent mode promotion.

## 6.5 Acceptance Criteria

- Must be safe to run repeatedly.
- Must not send orders.
- Must not mutate critical trading state except creating missing reporting tables if explicitly allowed.
- Must support `--mode SHADOW` and `--mode LIVE_RESTRICTED` validation.
- Must be strictly typed.
- Must include pytest coverage.

---

# 7. WORKSTREAM 3 — KILL-SWITCH DRILL HARNESS

## 7.1 Purpose

Create a controlled drill harness that validates safety mechanisms without risking capital.

This harness must operate only in safe modes:

```text
BACKTEST
PAPER
SHADOW
DRILL
```

It must never run destructive drills in `LIVE_RESTRICTED` or `LIVE_FULL` unless explicitly protected by a separate human-confirmed dry-run flag and no real order submission occurs.

## 7.2 Files to Create

```text
src/trading/ops/drills.py
scripts/kill_switch_drill.py
```

## 7.3 Required Drills

### Drill 1: Stale Data Rejection

Simulate a market data stall.

Expected behavior:

```text
StalenessSentinel trips
OMS rejects new entries with STALE_DATA_REJECTION
evaluate_exit_signal remains callable
```

### Drill 2: Portfolio Drawdown Tier 2

Simulate portfolio drawdown exceeding Tier 2 threshold.

Expected behavior:

```text
PortfolioCircuitBreaker emits PORTFOLIO_DRAWDOWN_WARNING
New entries are blocked
Existing exit evaluation remains active
```

### Drill 3: Portfolio Flatten Tier 3

Simulate portfolio drawdown exceeding Tier 3 threshold.

Expected behavior:

```text
PortfolioCircuitBreaker emits PORTFOLIO_FLATTEN
RiskEngine-sanctioned exit pathway is triggered for all reconciled positions
No direct construction of ApprovedExit occurs outside RiskEngine
```

In Shadow Mode, flatten must produce simulated exits only.

In Paper Mode, flatten must produce paper exits only.

In Live Mode, this drill must default to `DRY_RUN` and must not send real orders unless explicitly enabled by a protected flag.

### Drill 4: Duplicate Daemon Prevention

Attempt to start a second heartbeat process.

Expected behavior:

```text
second process is blocked
Postgres advisory lock or equivalent singleton mechanism prevents duplicate execution
```

### Drill 5: Reconciliation Quarantine

Insert a phantom local position or orphan local order into a test schema.

Expected behavior:

```text
reconciler detects mismatch
system enters quarantine or flags mismatch
new entries are blocked
exit evaluation for reconciled positions remains available
```

### Drill 6: Partial Fill Finalization

Simulate a partial fill followed by cancellation of remainder.

Expected behavior:

```text
PARTIAL_FILL_FINALIZED event is emitted
RiskDeviationEvent is logged
stop-loss remains attached to filled quantity
initial stop price is unchanged
```

### Drill 7: Wash Trade Prevention

Create a resting buy order, then simulate an opposite sell signal.

Expected behavior:

```text
opposite resting order is canceled or finalized before new order dispatch
no simultaneous opposite resting orders remain
```

## 7.4 Drill Report Output

The drill harness must output:

```json
{
  "drill_name": "portfolio_flatten_tier_3",
  "status": "PASS",
  "events_observed": [],
  "invariant_violations": [],
  "duration_ms": 1234,
  "notes": []
}
```

It must also persist results to:

```text
drill_results
```

or an equivalent table.

## 7.5 Acceptance Criteria

- All drills are deterministic where possible.
- Drills do not require production exchange connectivity.
- Drills never send real orders by default.
- Any live-mode drill must require explicit human confirmation and default to dry-run.
- Drill results are stored with UTC timestamps.
- Pytest coverage exists for each drill.

---

# 8. WORKSTREAM 4 — RECONCILIATION REPORT GENERATOR

## 8.1 Purpose

Create a report that compares local system state with exchange state and produces a clear operational health result.

## 8.2 File to Create

```text
scripts/reconcile_report.py
```

## 8.3 Requirements

The report must compare:

```text
local positions vs exchange positions
local balances vs exchange balances
local open orders vs exchange open orders
recent local fills vs exchange fills
order intent states vs exchange order states
```

It must support:

```bash
python scripts/reconcile_report.py
python scripts/reconcile_report.py --format json
python scripts/reconcile_report.py --lookback-hours 24
```

## 8.4 Output Fields

```text
reconciliation_id
timestamp_utc
execution_mode
positions_match
balances_match
open_orders_match
fills_match
quarantine_count
unknown_order_count
orphan_fill_count
position_mismatch_count
balance_mismatch_count
status
```

Status must be one of:

```text
CLEAN
WARNING
QUARANTINE
MISMATCH
FAILED
```

## 8.5 Safety Rule

The reconciliation report must be read-only by default.

It may propose corrective actions, but it must not automatically cancel live orders, modify positions, or alter balances unless explicitly invoked through a separate protected remediation workflow.

## 8.6 Acceptance Criteria

- Safe to run repeatedly.
- Does not submit orders.
- Does not mutate exchange state.
- Strictly typed.
- Pytest coverage included.

---

# 9. WORKSTREAM 5 — MODE PROMOTION GUARD

## 9.1 Purpose

Create a controlled mode promotion utility that prevents accidental promotion into dangerous execution modes.

## 9.2 File to Create

```text
scripts/promote_mode.py
```

## 9.3 Supported Modes

```text
BACKTEST
PAPER
SHADOW
LIVE_RESTRICTED
LIVE_FULL
```

## 9.4 Promotion Rules

### From BACKTEST to PAPER

Required:

```text
preflight_check passes
full test suite passes
```

### From PAPER to SHADOW

Required:

```text
preflight_check passes
reconcile_report is CLEAN or WARNING only
no unresolved quarantine
```

### From SHADOW to LIVE_RESTRICTED

Required:

```text
Shadow Mode has run for at least 20 trading days
Gate 1 status is PASS
all kill-switch drills pass
reconcile_report is CLEAN
no unresolved quarantine
no invariant violations in logs
human confirmation token is provided
```

### From LIVE_RESTRICTED to LIVE_FULL

Required:

```text
LIVE_RESTRICTED has run for at least 30 trading days
zero reconciliation failures
zero unhandled execution incidents
risk parameters remain within approved limits
human confirmation token is provided
separate written operator approval exists
```

## 9.5 Human Confirmation

For any promotion into `LIVE_RESTRICTED` or `LIVE_FULL`, require:

```bash
python scripts/promote_mode.py --to LIVE_RESTRICTED --confirm I_UNDERSTAND_THE_RISK
```

Without the confirmation token, promotion must fail.

## 9.6 Safety Rule

The promotion script must never modify strategy logic, risk limits, or invariants.

It may only update deployment state/configuration if such updates are explicit, logged, and reversible.

## 9.7 Acceptance Criteria

- Prevents invalid mode jumps.
- Logs every promotion attempt.
- Blocks promotion when gates fail.
- Strictly typed.
- Pytest coverage included.

---

# 10. WORKSTREAM 6 — DEPLOYMENT METRICS PERSISTENCE

## 10.1 Purpose

Persist operational metrics so Shadow Mode validation can be audited over time.

## 10.2 Tables to Create or Verify

### deployment_metrics

```sql
CREATE TABLE IF NOT EXISTS deployment_metrics (
    id BIGSERIAL PRIMARY KEY,
    metric_date DATE NOT NULL,
    execution_mode TEXT NOT NULL,
    symbols TEXT NOT NULL,
    signals_generated INTEGER,
    signals_approved INTEGER,
    signals_rejected_capital INTEGER,
    signals_rejected_stale_data INTEGER,
    signals_rejected_portfolio_risk INTEGER,
    signals_rejected_liquidity INTEGER,
    shadow_fills_generated INTEGER,
    liquidity_deficit_events INTEGER,
    liquidity_deficit_pct NUMERIC,
    avg_signal_to_fill_latency_ms NUMERIC,
    p95_signal_to_fill_latency_ms NUMERIC,
    p99_signal_to_fill_latency_ms NUMERIC,
    avg_shadow_slippage_bps NUMERIC,
    p95_shadow_slippage_bps NUMERIC,
    staleness_circuit_breaker_trips INTEGER,
    websocket_disconnect_events INTEGER,
    out_of_order_tick_events INTEGER,
    data_quality_failures INTEGER,
    shadow_pnl NUMERIC,
    shadow_pnl_pct NUMERIC,
    max_shadow_drawdown_pct NUMERIC,
    portfolio_exposure_pct NUMERIC,
    effective_leverage NUMERIC,
    portfolio_volatility_annualized NUMERIC,
    portfolio_var_95 NUMERIC,
    portfolio_var_99 NUMERIC,
    funding_burn_pct_daily NUMERIC,
    reconciliation_runs INTEGER,
    reconciliation_mismatches INTEGER,
    quarantine_events INTEGER,
    unknown_order_events INTEGER,
    position_mismatch_events INTEGER,
    balance_mismatch_events INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (metric_date, execution_mode, symbols)
);
```

### drill_results

```sql
CREATE TABLE IF NOT EXISTS drill_results (
    id BIGSERIAL PRIMARY KEY,
    drill_name TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    events_observed JSONB,
    invariant_violations JSONB,
    notes JSONB,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### reconciliation_reports

```sql
CREATE TABLE IF NOT EXISTS reconciliation_reports (
    id BIGSERIAL PRIMARY KEY,
    reconciliation_id TEXT NOT NULL,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    execution_mode TEXT NOT NULL,
    positions_match BOOLEAN,
    balances_match BOOLEAN,
    open_orders_match BOOLEAN,
    fills_match BOOLEAN,
    quarantine_count INTEGER,
    unknown_order_count INTEGER,
    orphan_fill_count INTEGER,
    position_mismatch_count INTEGER,
    balance_mismatch_count INTEGER,
    status TEXT NOT NULL,
    report_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## 10.3 Requirements

- Use UTC timestamps everywhere.
- Make migrations idempotent.
- Do not store API secrets.
- Do not store full credentials.
- Ensure reports can be regenerated safely.

---

# 11. WORKSTREAM 7 — DOCUMENTATION

## 11.1 Files to Create

```text
docs/DEPLOYMENT_RUNBOOK.md
docs/DAY0_CHECKLIST.md
docs/INCIDENT_RESPONSE.md
docs/SHADOW_MODE_VALIDATION.md
```

## 11.2 DEPLOYMENT_RUNBOOK.md

Must include:

1. Current system state.
2. Required environment variables.
3. How to run preflight.
4. How to run Shadow Mode.
5. How to generate shadow reports.
6. How to run reconciliation.
7. How to run kill-switch drills.
8. How to promote modes.
9. Go/No-Go gates.
10. Rollback procedure.

## 11.3 DAY0_CHECKLIST.md

Must include a checklist for the first live deployment day:

```text
- Preflight passes
- Reconciliation is CLEAN
- Kill-switch drills pass
- Shadow Mode Gate 1 passes
- Capital amount is micro-sized
- risk_pct is set to 0.001 or lower
- max_concurrent_positions is 2
- max_portfolio_exposure_pct is 0.10 or lower
- exchange API keys have no withdrawal permission
- IP allowlist is configured
- monitoring alerts are active
- human operator is available
- kill-switch command is known and tested
```

## 11.4 INCIDENT_RESPONSE.md

Must include procedures for:

```text
exchange downtime
websocket disconnect
stale data
partial fill divergence
position mismatch
balance mismatch
duplicate daemon
order rejected repeatedly
funding rate anomaly
database failure
kill-switch activation
```

Each incident must include:

```text
Detection
Immediate action
Diagnosis
Recovery
Post-incident review
```

---

# 12. WORKSTREAM 8 — TESTING REQUIREMENTS

Antigravity must add tests for all new modules.

## 12.1 Required Test Files

```text
tests/ops/test_shadow_report.py
tests/ops/test_preflight_check.py
tests/ops/test_kill_switch_drill.py
tests/ops/test_reconcile_report.py
tests/ops/test_promote_mode.py
tests/ops/test_deployment_metrics.py
```

## 12.2 Required Test Cases

### Shadow Report

```text
passes when all Gate 1 conditions are satisfied
fails when backtest benchmark is missing
fails when reconciliation mismatches exist
fails when staleness trips exceed threshold
handles empty data gracefully
```

### Preflight

```text
passes with valid config
fails when risk_pct missing
fails when database unavailable
fails when unresolved quarantine exists
warns when optional observability config missing
```

### Kill-Switch Drill

```text
stale data drill blocks entries but not exits
Tier 2 drill blocks entries
Tier 3 drill triggers sanctioned flatten pathway
duplicate daemon drill fails safely
partial fill drill preserves initial stop price
wash trade drill cancels opposite resting order
```

### Reconciliation

```text
clean state returns CLEAN
position mismatch returns MISMATCH
unknown order returns WARNING or QUARANTINE depending on severity
script does not submit orders
```

### Mode Promotion

```text
cannot jump from BACKTEST to LIVE_RESTRICTED
cannot promote to LIVE_RESTRICTED without confirmation token
cannot promote if Gate 1 fails
cannot promote if reconciliation is not CLEAN
```

---

# 13. IMPLEMENTATION ORDER

Antigravity should implement in this order:

1. Deployment metrics tables/migrations.
2. `scripts/preflight_check.py`.
3. `scripts/reconcile_report.py`.
4. `scripts/shadow_report.py`.
5. `src/trading/ops/drills.py`.
6. `scripts/kill_switch_drill.py`.
7. `scripts/promote_mode.py`.
8. Documentation.
9. Full test suite verification.

Do not begin Shadow Mode operational tooling before preflight and reconciliation are stable.

---

# 14. DEFINITION OF DONE

Phase 11 is complete when all of the following are true:

```text
All new scripts exist and are runnable.
All new tests pass.
Full test suite remains green.
No Sovereign Invariant is violated.
No code path sends real orders by default.
Shadow report can compute Gate 1.
Preflight report can block unsafe deployment.
Kill-switch drills validate safety mechanisms.
Reconciliation report can detect state divergence.
Mode promotion requires explicit human confirmation.
Documentation is complete and accurate.
```

Expected final test count should exceed the current 144 tests.

Do not reduce test coverage.

---

# 15. SOCRATIC GUARDRAILS FOR ANTIGRAVITY

Before completing Phase 11, Antigravity must answer these questions inside module docstrings or documentation:

1. If Shadow Mode metrics look excellent but reconciliation reports show one unexplained mismatch, should the system promote to live trading? Why or why not?

2. If a kill-switch drill passes in Paper Mode but behaves differently in Shadow Mode against live market data, what does that reveal about the environment?

3. If the system generates five simultaneous signals but capital only supports two, what is the cost of rejecting the other three? Is that cost acceptable?

4. If a human operator wants to bypass a failed preflight check because “we know it is fine,” what architectural mechanism prevents that bypass from becoming a silent failure?

5. If the system cannot prove that it is safe, should it be allowed to trade?

The correct default answer is:

> If certainty is missing, the system must not proceed.

---

# 16. IMMEDIATE FIRST ACTION

Antigravity must begin with:

```text
scripts/preflight_check.py
```

and:

```text
scripts/reconcile_report.py
```

These are the foundation of the Deployment Readiness Harness.

Do not build live-capital tooling before the system can prove its own preflight health and reconciliation cleanliness.

---

# 17. FINAL CONSTRAINT

The next milestone is not profitability.

The next milestone is:

> Prove the system can observe, reconcile, report, and protect itself safely in a live market environment before it is trusted with capital.

Build the harness first.

Capital follows proof.
