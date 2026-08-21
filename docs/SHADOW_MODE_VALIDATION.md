# Shadow Mode Validation Guide — SUPER_TRADEMAN

## 1. Objective
Shadow Mode runs the complete live data pipeline, signal generation, risk engine, OMS, and synthetic execution engine against real-time L2 order books without placing live orders on exchange venues.

The objective of the 3-4 week Shadow Mode campaign is to prove tracking error consistency against backtest expectations and verify system health under live market conditions.

## 2. Gate 1 Criteria (Go/No-Go Gate)
For promotion from `SHADOW` to `LIVE_RESTRICTED`, the system must run for a minimum of 20 trading days and achieve `GATE_1_STATUS: PASS`.

### Threshold Rules:
1. `shadow_pnl_z_score`: Must be within $[-1.5, +1.5]$ relative to backtest benchmark expectation.
2. `staleness_circuit_breaker_trips`: $\le 3$ trips per week.
3. `liquidity_deficit_pct`: $\le 5.0\%$ of total fills.
4. `reconciliation_mismatches`: Strictly $0$.
5. `unknown_order_events`: Strictly $0$.
6. `position_mismatch_events`: Strictly $0$.
7. `balance_mismatch_events`: Strictly $0$.
8. `backtest_benchmark_present`: Must be `true`.

## 3. Evaluation Procedure
Run `shadow_report.py` daily:
```bash
python scripts/shadow_report.py --days 20 --format table
```
If `GATE_1_STATUS` prints `FAIL`, inspect itemized failures and continue Shadow Mode observation until all criteria are satisfied.
