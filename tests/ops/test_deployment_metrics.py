"""Tests for Deployment Metrics Store and Schema."""

import pytest

from trading.ops.deployment_metrics import (
    DeploymentMetricRecord,
    DeploymentMetricsStore,
    DrillResultRecord,
    ReconciliationReportRecord,
)


def test_deployment_metrics_store():
    store = DeploymentMetricsStore()

    rec1 = DeploymentMetricRecord(
        metric_date="2026-08-17",
        execution_mode="shadow",
        symbols="BTC/USDT",
        signals_generated=10,
        signals_approved=8,
        shadow_pnl_pct=0.02,
    )
    rec2 = DeploymentMetricRecord(
        metric_date="2026-08-18",
        execution_mode="shadow",
        symbols="BTC/USDT",
        signals_generated=15,
        signals_approved=12,
        shadow_pnl_pct=0.03,
    )

    store.record_metrics(rec1)
    store.record_metrics(rec2)

    cum = store.get_cumulative_metrics(days=2)
    assert cum is not None
    assert cum.signals_generated == 25
    assert cum.signals_approved == 20
    assert cum.shadow_pnl_pct == pytest.approx(0.05)


def test_drill_and_reconciliation_records():
    store = DeploymentMetricsStore()

    drill_rec = DrillResultRecord(drill_name="stale_data", execution_mode="shadow", status="PASS")
    store.record_drill_result(drill_rec)
    assert len(store.drill_history) == 1

    rec_report = ReconciliationReportRecord(reconciliation_id="rec_1", timestamp_utc=__import__("datetime").datetime.now(__import__("datetime").timezone.utc), execution_mode="shadow", status="CLEAN")
    store.record_reconciliation_report(rec_report)
    assert len(store.reconciliation_history) == 1
