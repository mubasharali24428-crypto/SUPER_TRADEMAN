"""Tests for Centralized Metrics Collector: Prometheus export, alerts, counter hook."""

import uuid

import pytest

from trading.ops.deployment_metrics import (
    AlertRecord,
    DeploymentMetricRecord,
    DeploymentMetricsStore,
)
from trading.ops.metrics_collector import (
    MetricsCollector,
    super_trademan_reconciliation_mismatches,
)


class FakePool:
    """Minimal asyncpg.Pool stand-in recording executed statements."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.executed: list[tuple[str, str, tuple]] = []

    async def execute(self, sql, *args):
        if self.fail:
            raise ConnectionError("postgres unavailable")
        self.executed.append(("execute", sql, args))


def test_metrics_collector_prometheus_output():
    store = DeploymentMetricsStore()
    record = DeploymentMetricRecord(
        metric_date="2026-08-18",
        execution_mode="shadow",
        symbols="BTC/USDT",
        signals_generated=50,
        shadow_fills_generated=48,
        p95_signal_to_fill_latency_ms=120.0,
        shadow_pnl_pct=0.04,
    )
    store.record_metrics(record)

    collector = MetricsCollector(store=store)
    prom_text = collector.generate_prometheus_metrics()

    assert "super_trademan_signals_total" in prom_text
    assert "super_trademan_fills_total" in prom_text
    assert "super_trademan_latency_p95_ms" in prom_text
    assert "50" in prom_text
    assert "48" in prom_text


def test_prometheus_exports_reconciliation_mismatch_counter():
    collector = MetricsCollector(store=DeploymentMetricsStore())
    prom_text = collector.generate_prometheus_metrics()

    assert "# TYPE super_trademan_reconciliation_mismatches_total counter" in prom_text
    assert f"super_trademan_reconciliation_mismatches_total{{mode=" in prom_text


def test_reconciliation_mismatch_counter_hook_increments():
    start = super_trademan_reconciliation_mismatches.value
    new_value = super_trademan_reconciliation_mismatches.increment(3)
    assert new_value == start + 3
    assert super_trademan_reconciliation_mismatches.value == new_value
    with pytest.raises(ValueError):
        super_trademan_reconciliation_mismatches.increment(-1)


async def test_record_alert_persists_to_alerts_table():
    pool = FakePool()
    store = DeploymentMetricsStore(pool=pool)
    collector = MetricsCollector(store=store)

    alert = await collector.record_alert(
        alert_name="stale_data_breach",
        severity="CRITICAL",
        message="staleness > threshold",
        channel="pagerduty",
    )

    assert isinstance(alert, AlertRecord)
    assert alert.alert_name == "stale_data_breach"
    kind, sql, args = pool.executed[0]
    assert kind == "execute"
    assert "INSERT INTO alerts" in sql
    assert args[0] == alert.alert_id
    assert args[2] == "CRITICAL"
    assert args[4] == "pagerduty"


async def test_record_alert_without_pool_stays_memory_only():
    collector = MetricsCollector(store=DeploymentMetricsStore())

    alert = await collector.record_alert(
        alert_name="no_db", severity="INFO", message="no pool attached"
    )

    assert isinstance(alert, AlertRecord)
    assert alert.alert_id.startswith("alert-")
    uuid.UUID(alert.alert_id.split("alert-", 1)[1])  # auto-generated unique id parses


async def test_record_alert_survives_db_failure():
    collector = MetricsCollector(store=DeploymentMetricsStore(pool=FakePool(fail=True)))

    # Graceful degradation: the alert is returned even though persistence failed.
    alert = await collector.record_alert(
        alert_name="db_down", severity="HIGH", message="postgres unreachable"
    )
    assert isinstance(alert, AlertRecord)
    assert alert.severity == "HIGH"
