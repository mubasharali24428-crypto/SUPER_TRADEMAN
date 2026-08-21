"""Tests for Shadow Campaign Orchestrator and Gate 1 Aggregation."""

import pytest

from trading.ops.deployment_metrics import DeploymentMetricRecord, DeploymentMetricsStore
from trading.ops.shadow_campaign import ShadowCampaign


def test_shadow_campaign_gate1_pass():
    store = DeploymentMetricsStore()
    campaign = ShadowCampaign(store=store)

    for i in range(1, 21):
        rec = DeploymentMetricRecord(
            metric_date=f"2026-08-{i:02d}",
            execution_mode="shadow",
            symbols="BTC/USDT",
            signals_generated=10,
            shadow_fills_generated=10,
            p99_signal_to_fill_latency_ms=100.0,
            avg_shadow_slippage_bps=2.0,
            shadow_pnl_pct=0.003,
        )
        campaign.record_daily_metrics(rec)

    summary = campaign.evaluate_campaign_status(backtest_expected_pnl_pct=0.05, backtest_std_dev=0.02)
    assert summary.days_evaluated == 20
    assert summary.campaign_status == "GATE_1_PASS"
    assert summary.consecutive_breaches == 0


def test_shadow_campaign_consecutive_breach_hard_stop():
    store = DeploymentMetricsStore()
    campaign = ShadowCampaign(store=store, max_slippage_bps_threshold=10.0)

    # Record 3 consecutive days with excessive slippage (> 10 bps)
    for i in range(1, 4):
        rec = DeploymentMetricRecord(
            metric_date=f"2026-08-{i:02d}",
            execution_mode="shadow",
            symbols="BTC/USDT",
            avg_shadow_slippage_bps=35.0,  # Breach
            shadow_pnl_pct=-0.02,
        )
        campaign.record_daily_metrics(rec)

    summary = campaign.evaluate_campaign_status()
    assert summary.campaign_status == "GATE_1_FAIL"
    assert summary.consecutive_breaches == 3
    assert len(summary.failure_reasons) > 0
