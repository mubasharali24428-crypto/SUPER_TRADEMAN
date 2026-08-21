"""Tests for Shadow Mode Validation Reporter & Gate 1 Engine."""

from scripts.shadow_report import evaluate_gate_1
from trading.ops.deployment_metrics import DeploymentMetricRecord


def test_evaluate_gate_1_pass():
    record = DeploymentMetricRecord(
        metric_date="2026-08-18",
        execution_mode="shadow",
        symbols="BTC/USDT",
        shadow_pnl_pct=0.05,
        staleness_circuit_breaker_trips=2,
        liquidity_deficit_pct=0.01,
        reconciliation_mismatches=0,
    )
    passed, failures, details = evaluate_gate_1(record, backtest_expected_pnl_pct=0.05, backtest_pnl_std_dev=0.02)
    assert passed
    assert len(failures) == 0
    assert details["shadow_pnl_z_score"] == 0.0


def test_evaluate_gate_1_fail_missing_benchmark():
    record = DeploymentMetricRecord(metric_date="2026-08-18", execution_mode="shadow", symbols="BTC/USDT")
    passed, failures, details = evaluate_gate_1(record, backtest_expected_pnl_pct=None)
    assert not passed
    assert any("MISSING_BACKTEST_BENCHMARK" in f for f in failures)
