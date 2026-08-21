"""Tests for Market Impact and TCA."""

import pytest

from trading.execution.tca import calculate_market_impact, calibrate_sigma


def test_calibrate_sigma():
    prices = [100.0, 102.0, 101.0, 103.0, 105.0]
    sigma = calibrate_sigma(prices)
    assert 0.005 <= sigma <= 0.1


def test_market_impact_normal():
    # Order notional $10,000, ADV $1,000,000 -> participation = 1% (< 3% cap)
    impact_pct, exec_notional, was_capped = calculate_market_impact(
        order_notional=10000.0,
        adv_notional=1000000.0,
        sigma=0.02,
        max_participation_pct=0.03,
    )
    assert not was_capped
    assert exec_notional == 10000.0
    assert 0.0 < impact_pct < 0.01


def test_market_impact_participation_cap():
    # Order notional $50,000, ADV $1,000,000 -> participation = 5% (> 3% cap)
    impact_pct, exec_notional, was_capped = calculate_market_impact(
        order_notional=50000.0,
        adv_notional=1000000.0,
        sigma=0.02,
        max_participation_pct=0.03,
    )
    assert was_capped
    assert exec_notional == 30000.0  # capped at 3% of $1M
