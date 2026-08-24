"""Shadow-campaign observability truthfulness tests (SUB-10).

Covers:
- One-sided LOWER-tail breach detection: an outperforming day is NOT a breach.
- Consistent per-day scaling for the z-test: expectation and std BOTH scale to
  per-day units (std via sqrt(days)) — golden values pinned here.
"""

import math

from trading.ops.deployment_metrics import DeploymentMetricRecord
from trading.observability.shadow_metrics import (
    daily_z_score,
    lower_tail_breach,
    per_day_expectation,
    per_day_std_dev,
)
from trading.ops.shadow_campaign import ShadowCampaign


def _record(date, pnl_pct, slippage_bps=2.0):
    return DeploymentMetricRecord(
        metric_date=date,
        execution_mode="shadow",
        symbols="BTC/USDT",
        shadow_pnl_pct=pnl_pct,
        avg_shadow_slippage_bps=slippage_bps,
    )


# --------------------------------------------------------------------------
# Primitive-level tests
# --------------------------------------------------------------------------

def test_lower_tail_breach_underperformance_is_breach():
    assert lower_tail_breach(-3.5, 3.0) is True


def test_outperforming_day_is_not_a_breach():
    # Old code used abs(): +5.0 counted as a breach. It must not.
    assert lower_tail_breach(5.0, 3.0) is False
    assert daily_z_score(10.0, 1.0, 1.0, 4) > 0
    assert lower_tail_breach(daily_z_score(10.0, 1.0, 1.0, 4), 3.0) is False


def test_per_day_scaling_consistency_golden():
    """Golden: both expectation and std use per-day scale; ratio law holds."""
    exp = per_day_expectation(0.05, 25)   # 0.05 / 25
    std = per_day_std_dev(0.02, 25)       # 0.02 / sqrt(25)
    assert math.isclose(exp, 0.002, rel_tol=1e-12)
    assert math.isclose(std, 0.004, rel_tol=1e-12)

    # Consistency invariant: z computed from per-day primitives must equal
    # z computed from campaign-scale numerator over sqrt-scaled denominator.
    day_pnl, camp_exp, camp_std, days = 0.001, 0.05, 0.02, 25
    z_primitives = daily_z_score(day_pnl, camp_exp, camp_std, days)
    z_manual = (day_pnl - camp_exp / days) / (camp_std / math.sqrt(days))
    assert math.isclose(z_primitives, z_manual, rel_tol=1e-12)


# --------------------------------------------------------------------------
# Campaign-level behavior
# --------------------------------------------------------------------------

def test_campaign_outperforming_days_are_not_breaches():
    """A monster outperforming day must NOT trigger hard stop under one-sided test."""
    campaign = ShadowCampaign(max_z_score_threshold=3.0)
    for i in range(1, 6):
        campaign.daily_records.append(_record(f"2026-08-{i:02d}", pnl_pct=0.003))
    # Massive OUTPERFORMANCE on day 6 (old abs() logic flagged this as breach).
    campaign.daily_records.append(_record("2026-08-06", pnl_pct=0.50))

    summary = campaign.evaluate_campaign_status(
        backtest_expected_pnl_pct=0.05, backtest_std_dev=0.02
    )
    assert summary.consecutive_breaches == 0
    assert summary.campaign_status != "GATE_1_FAIL"


def test_campaign_underperforming_days_still_breach():
    campaign = ShadowCampaign(max_z_score_threshold=3.0)
    for i in range(1, 4):
        campaign.daily_records.append(_record(f"2026-08-{i:02d}", pnl_pct=-0.20))

    summary = campaign.evaluate_campaign_status(
        backtest_expected_pnl_pct=0.05, backtest_std_dev=0.02
    )
    assert summary.consecutive_breaches >= 3
    assert summary.campaign_status == "GATE_1_FAIL"


def test_campaign_gate1_pass_path_unchanged():
    campaign = ShadowCampaign()
    for i in range(1, 21):
        campaign.daily_records.append(_record(f"2026-08-{i:02d}", pnl_pct=0.003))

    summary = campaign.evaluate_campaign_status(
        backtest_expected_pnl_pct=0.05, backtest_std_dev=0.02
    )
    assert summary.days_evaluated == 20
    assert summary.campaign_status == "GATE_1_PASS"
    assert summary.consecutive_breaches == 0
