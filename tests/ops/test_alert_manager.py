"""Tests for Alert Manager, Deduplication, Escalation, and Multi-Channel Routing."""

import pytest

from trading.ops.alert_manager import AlertManager, AlertSeverity
from trading.ops.deployment_metrics import DeploymentMetricsStore


def test_alert_manager_evaluation_and_deduplication():
    store = DeploymentMetricsStore()
    mgr = AlertManager(store=store, cooldown_sec=300.0)

    # High latency > 500ms triggers WARNING alert
    rec1 = mgr.evaluate_metric("latency_p95_ms", 650.0)
    assert rec1 is not None
    assert rec1.alert_name == "High Latency"
    assert rec1.severity == AlertSeverity.WARNING

    # Immediate second call within cooldown period (300s) is suppressed
    rec2 = mgr.evaluate_metric("latency_p95_ms", 700.0)
    assert rec2 is None


def test_alert_manager_escalation():
    mgr = AlertManager(cooldown_sec=0.0)  # Disable cooldown for escalation test

    # 1st and 2nd trigger: WARNING
    mgr.evaluate_metric("latency_p95_ms", 600.0)
    mgr.evaluate_metric("latency_p95_ms", 600.0)

    # 3rd trigger: Escalated to CRITICAL
    rec3 = mgr.evaluate_metric("latency_p95_ms", 600.0)
    assert rec3 is not None
    assert rec3.severity == AlertSeverity.CRITICAL


def test_alert_manager_emergency_reconciliation():
    mgr = AlertManager(cooldown_sec=0.0)
    rec = mgr.evaluate_metric("reconciliation_mismatch", 1.0)

    assert rec is not None
    assert rec.severity == AlertSeverity.EMERGENCY
