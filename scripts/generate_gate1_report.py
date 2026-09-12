#!/usr/bin/env python3
"""Automated Gate 1 Report Generator CLI Script.

FAIL-CLOSED: renders a promotion report only when the DeploymentMetricsStore
holds at least MIN_DAYS_REQUIRED real persisted daily records. This script
NEVER fabricates, hardcodes, or backfills campaign records (audit finding
F-0007/G-027) — a report recommending live promotion must be earned by data.

Exit codes:
    0 — report rendered; Gate 1 PASS (PROCEED recommendation possible)
    1 — report rendered; Gate 1 FAIL / IN_PROGRESS evaluation outcome
    2 — insufficient data (< MIN_DAYS_REQUIRED persisted daily records)
    3 — evaluation error (report could not be evaluated/rendered)
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from trading.ops.deployment_metrics import DeploymentMetricsStore
from trading.ops.shadow_campaign import ShadowCampaign

MIN_DAYS_REQUIRED = 20

EXIT_REPORT_PASS = 0
EXIT_REPORT_FAIL = 1
EXIT_INSUFFICIENT_DATA = 2
EXIT_EVAL_FAIL = 3


class InsufficientDataError(RuntimeError):
    """Raised when the persisted store cannot support a Gate 1 evaluation."""


def _load_persisted_records(store: DeploymentMetricsStore) -> list:
    """Return all real persisted daily records from the store (no synthesis)."""
    return list(store.metrics_history)


def generate_gate1_markdown_report(
    days: int = 30,
    store: "DeploymentMetricsStore | None" = None,
    backtest_expected_pnl_pct: float | None = None,
    backtest_std_dev: float | None = None,
) -> str:
    """Render the Gate 1 promotion report strictly from persisted records.

    Raises InsufficientDataError if fewer than MIN_DAYS_REQUIRED distinct daily
    records exist. Callers (CLI main) map that to exit code 2.
    """
    store = store or DeploymentMetricsStore()
    records = _load_persisted_records(store)

    distinct_dates = {r.metric_date for r in records}
    if len(distinct_dates) < MIN_DAYS_REQUIRED:
        raise InsufficientDataError(
            f"INSUFFICIENT_DATA: {len(distinct_dates)} persisted daily record(s) found, "
            f"{MIN_DAYS_REQUIRED} required to render a Gate 1 promotion report. "
            "This generator never fabricates fallback data."
        )

    # Evaluate over the most recent `days` records, mirroring the store's windowing.
    campaign = ShadowCampaign(store=store)
    for rec in records[-days:]:
        # Re-evaluate without re-persisting: build an evaluator-side view.
        campaign.daily_records.append(rec)

    # VA-063: load expected pnl/stddev from args when provided, not magic numbers.
    summary = campaign.evaluate_campaign_status(
        backtest_expected_pnl_pct=backtest_expected_pnl_pct if backtest_expected_pnl_pct is not None else 0.06,
        backtest_std_dev=backtest_std_dev if backtest_std_dev is not None else 0.02,
    )

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    raw_sig_data = f"{summary.campaign_status}|{summary.days_evaluated}|{summary.shadow_pnl_pct}|{now_str}"
    # VA-033: self-reported digest, prefix to make provenance explicit
    digital_signature = "sha256-self:" + hashlib.sha256(raw_sig_data.encode("utf-8")).hexdigest()

    md = f"""# Gate 1 Validation & Mode Promotion Report — SUPER_TRADEMAN

**Generated Timestamp (UTC):** {now_str}  
**Evaluation Campaign Window:** {summary.days_evaluated} Days  
**Persisted Daily Records:** {len(records)}  
**Executive Summary:** **[{summary.campaign_status}]**

---

## 1. Quantitative Performance & Statistical Breakdown
- **Cumulative Shadow PnL:** `{summary.shadow_pnl_pct * 100:.2f}%`
- **Tracking Error Z-Score:** `{summary.tracking_error_z_score:.4f}`
- **Average Shadow Slippage:** `{summary.avg_slippage_bps:.2f} bps`
- **99th Percentile Latency (p99):** `{summary.latency_p99_ms:.2f} ms`
- **Consecutive Threshold Breaches:** `{summary.consecutive_breaches}`

---

## 2. Risk Metrics & Value at Risk (VaR)
- **Portfolio 95% 1-Day VaR:** `{records[-1].portfolio_var_95:.2%}`
- **Portfolio 99% 1-Day VaR:** `{records[-1].portfolio_var_99:.2%}`
- **Maximum Observed Drawdown:** `{max(r.max_shadow_drawdown_pct for r in records):.2%}`
- **Reconciliation Mismatch Count:** `{sum(r.reconciliation_mismatches for r in records)}`

*All risk metrics above are computed from persisted campaign records.*

---

## 3. Cryptographic Recommendation & Audit Signature
**Promotion Recommendation:** {'PROCEED TO LIVE_RESTRICTED' if summary.campaign_status == 'GATE_1_PASS' else 'RETAIN IN SHADOW MODE'}  
**Cryptographic Approval Signature:**  
`SHA256:{digital_signature}`

---
*Report generated automatically by `scripts/generate_gate1_report.py` from persisted DeploymentMetricsStore records only.*
"""
    return md


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate SUPER_TRADEMAN Gate 1 Markdown Report")
    parser.add_argument("--days", type=int, default=30, help="Campaign evaluation days")
    parser.add_argument("--output", type=str, default="", help="Optional output file path")
    parser.add_argument("--expected-pnl-pct", type=float, default=None,
                        help="Backtest expected PnL %% for Gate-1 tracking-error z-score")
    parser.add_argument("--expected-std-dev", type=float, default=None,
                        help="Backtest std dev for Gate-1 tracking-error z-score")
    args = parser.parse_args()

    try:
        report_md = generate_gate1_markdown_report(
            days=args.days,
            backtest_expected_pnl_pct=args.expected_pnl_pct,
            backtest_std_dev=args.expected_std_dev,
        )
    except InsufficientDataError as e:
        print(f"GATE1_REPORT_STATUS: NOT_RENDERED\nReason: {e}")
        sys.exit(EXIT_INSUFFICIENT_DATA)
    except Exception as e:  # noqa: BLE001 — fail-closed on any evaluation error
        print(f"GATE1_REPORT_STATUS: EVAL_ERROR\nReason: {e}")
        sys.exit(EXIT_EVAL_FAIL)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report_md)
        print(f"Report written to {args.output}")
    else:
        print(report_md)

    # Exit code reflects evaluation outcome, not just render success.
    if "[GATE_1_PASS]" in report_md:
        sys.exit(EXIT_REPORT_PASS)
    elif "[GATE_1_FAIL]" in report_md:
        sys.exit(EXIT_REPORT_FAIL)
    else:
        # e.g. IN_PROGRESS — not enough signal to promote; treated as eval-fail for gating.
        print("Note: campaign status is not GATE_1_PASS.", file=sys.stderr)
        sys.exit(EXIT_EVAL_FAIL)


if __name__ == "__main__":
    main()
