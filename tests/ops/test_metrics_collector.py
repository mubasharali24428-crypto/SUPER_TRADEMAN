"""Tests for Centralized Metrics Collector and Prometheus Export."""

import pytest

from trading.ops.deployment_metrics import DeploymentMetricRecord, DeploymentMetricsStore
from trading.ops.metrics_collector import MetricsCollector


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
