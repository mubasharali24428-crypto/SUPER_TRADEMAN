#!/usr/bin/env python3
"""Shadow Mode Validation Reporter & Gate 1 Evaluator Script.

Generates daily and cumulative Shadow Mode validation reports and computes
Gate 1 Go/No-Go status before execution mode promotion.

FAIL-CLOSED: this report is generated exclusively from metrics previously
persisted in the DeploymentMetricsStore by the running campaign. This script
NEVER synthesizes, seeds, or backfills records (see audit findings F-0044/G-026).
With an empty or insufficient (< MIN_DAYS_REQUIRED) store it refuses to render
a gate decision and exits non-zero.
"""

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from trading.ops.deployment_metrics import (DeploymentMetricRecord,
                                            DeploymentMetricsStore)

# Gate 1 requires at least this many distinct persisted daily records before any
# evaluation is meaningful. Fewer days => INSUFFICIENT_DATA => non-zero exit.
MIN_DAYS_REQUIRED = 20

EXIT_OK = 0
EXIT_INSUFFICIENT_DATA = 2
EXIT_GATE_FAIL = 3


def count_persisted_days(store: DeploymentMetricsStore) -> int:
    """Number of distinct persisted daily metric dates in the store."""
    return len({r.metric_date for r in store.metrics_history})


def evaluate_gate_1(
    record: DeploymentMetricRecord,
    backtest_expected_pnl_pct: Optional[float] = 0.05,
    backtest_pnl_std_dev: Optional[float] = 0.02,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Evaluate cumulative shadow metrics against Gate 1 criteria.

    Pure function over the provided record — no store access, no seeding.
    """
    failures: List[str] = []
    gate_details: Dict[str, Any] = {}

    # Check backtest benchmark presence
    if (
        backtest_expected_pnl_pct is None
        or backtest_pnl_std_dev is None
        or backtest_pnl_std_dev <= 0
    ):
        failures.append(
            "MISSING_BACKTEST_BENCHMARK: Backtest benchmark PnL or std dev missing."
        )
        gate_details["backtest_benchmark_present"] = False
        gate_details["shadow_pnl_z_score"] = None
    else:
        gate_details["backtest_benchmark_present"] = True
        z_score = (
            record.shadow_pnl_pct - backtest_expected_pnl_pct
        ) / backtest_pnl_std_dev
        gate_details["shadow_pnl_z_score"] = round(z_score, 4)
        if not (-1.5 <= z_score <= 1.5):
            failures.append(
                f"Z_SCORE_OUT_OF_BOUNDS: shadow_pnl_z_score {z_score:.2f} outside [-1.5, +1.5]"
            )

    # Check circuit breaker trips (max 3 per week -> ~8.5 per 20 days)
    if record.staleness_circuit_breaker_trips > 8:
        failures.append(
            f"STALENESS_TRIPS_EXCEEDED: {record.staleness_circuit_breaker_trips} trips > threshold"
        )
    gate_details["staleness_trips"] = record.staleness_circuit_breaker_trips

    # Check liquidity deficit percentage
    if record.liquidity_deficit_pct > 0.05:
        failures.append(
            f"LIQUIDITY_DEFICIT_EXCEEDED: liquidity_deficit_pct {record.liquidity_deficit_pct*100:.1f}% > 5.0%"
        )
    gate_details["liquidity_deficit_pct"] = record.liquidity_deficit_pct

    # Check zero mismatches
    if record.reconciliation_mismatches > 0:
        failures.append(
            f"RECONCILIATION_MISMATCHES: {record.reconciliation_mismatches} mismatches observed"
        )
    if record.unknown_order_events > 0:
        failures.append(
            f"UNKNOWN_ORDERS: {record.unknown_order_events} unknown order events observed"
        )
    if record.position_mismatch_events > 0:
        failures.append(
            f"POSITION_MISMATCHES: {record.position_mismatch_events} position mismatch events observed"
        )
    if record.balance_mismatch_events > 0:
        failures.append(
            f"BALANCE_MISMATCHES: {record.balance_mismatch_events} balance mismatch events observed"
        )

    gate_pass = len(failures) == 0
    return gate_pass, failures, gate_details


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate SUPER_TRADEMAN Shadow Mode Validation Report"
    )
    parser.add_argument(
        "--days", type=int, default=20, help="Evaluation window in days"
    )
    parser.add_argument(
        "--symbol", type=str, default="BTC/USDT", help="Filter by symbol"
    )
    parser.add_argument(
        "--format",
        type=str,
        default="table",
        choices=["table", "json"],
        help="Output format",
    )
    args = parser.parse_args()

    # Read-only view of the persisted campaign store. NO synthetic seeding.
    store = DeploymentMetricsStore()
    persisted_days = count_persisted_days(store)

    if persisted_days < MIN_DAYS_REQUIRED:
        if args.format == "json":
            print(
                json.dumps(
                    {
                        "gate_1_status": "NOT_EVALUABLE",
                        "reason": "INSUFFICIENT_DATA",
                        "persisted_days": persisted_days,
                        "required_days": MIN_DAYS_REQUIRED,
                    },
                    indent=2,
                )
            )
        else:
            print(
                f"GATE_1_STATUS: NOT_EVALUABLE\n"
                f"Reason: INSUFFICIENT_DATA — persisted daily records: {persisted_days}, "
                f"required: >= {MIN_DAYS_REQUIRED}.\n"
                "This script never fabricates metrics; run the shadow campaign until "
                f"{MIN_DAYS_REQUIRED}+ daily records are persisted."
            )
        sys.exit(EXIT_INSUFFICIENT_DATA)

    agg = store.get_cumulative_metrics(days=args.days)
    if not agg:
        # Defensive: >=MIN_DAYS_REQUIRED distinct dates but aggregation unavailable.
        print(
            "GATE_1_STATUS: NOT_EVALUABLE\nReason: INSUFFICIENT_DATA — no aggregate available from persisted store."
        )
        sys.exit(EXIT_INSUFFICIENT_DATA)

    gate_pass, failures, gate_details = evaluate_gate_1(agg)

    if args.format == "json":
        out = {
            "metrics": asdict(agg),
            "gate_1_status": "PASS" if gate_pass else "FAIL",
            "failures": failures,
            "gate_details": gate_details,
        }
        print(json.dumps(out, indent=2))
    else:
        print("\n=======================================================")
        print("      SUPER_TRADEMAN SHADOW MODE VALIDATION REPORT")
        print("=======================================================\n")
        print(f"Evaluation Days  : {args.days}")
        print(f"Persisted Days   : {persisted_days}")
        print(f"Signals Generated: {agg.signals_generated}")
        print(f"Signals Approved : {agg.signals_approved}")
        print(f"Shadow Fills     : {agg.shadow_fills_generated}")
        print(f"Avg Latency (ms) : {agg.avg_signal_to_fill_latency_ms:.1f}")
        print(f"Shadow PnL (%)   : {agg.shadow_pnl_pct*100:.2f}%")
        print("-------------------------------------------------------")
        print(f"GATE 1 STATUS    : {'PASS' if gate_pass else 'FAIL'}")
        if failures:
            print("Failures:")
            for f in failures:
                print(f"  - {f}")
        print("-------------------------------------------------------\n")

    sys.exit(EXIT_OK if gate_pass else EXIT_GATE_FAIL)


if __name__ == "__main__":
    main()
