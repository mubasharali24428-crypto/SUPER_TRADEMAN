"""Tests for Frontier 3 Dual-Horizon Architecture and Sniper Mode."""

import pytest

from trading.synthetic.strategy_defense import (
    MicroAlphaEngine,
    SniperMode,
    TradeSignal,
)


def test_micro_alpha_engine_captures_post_crash_dislocation():
    alpha = MicroAlphaEngine(signal_horizon_ms=30000)
    alpha.on_halt_triggered(100.0)

    # Price dropped to 95.0 (-5% dislocation) and stress is low (0.05)
    sig = alpha.calculate_signal(current_micro_price=95.0, stress_score=0.05, sigma=0.01)
    assert sig.is_valid
    assert sig.direction == "BUY"
    assert sig.edge_zscore == 5.0  # 0.05 / 0.01 = 5.0


def test_sniper_mode_executes_opportunity():
    sniper = SniperMode(max_size_multiplier=0.10)
    sniper.is_armed = True

    sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95, is_valid=True)
    assert sniper.evaluate_opportunity(sig, stress_score=0.05)

    order = sniper.execute_sniper_trade(sig, base_size=10.0)
    assert order.side == "BUY"
    assert order.qty == 1.0  # 10% of 10.0
    assert order.tif == "IOC"


def test_sniper_mode_does_not_fire_during_stress():
    sniper = SniperMode(max_size_multiplier=0.10)
    sniper.is_armed = True

    sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95, is_valid=True)
    # Market stress is elevated (0.35 >= 0.10)
    assert not sniper.evaluate_opportunity(sig, stress_score=0.35)


def test_sniper_mode_disarmed_by_default():
    sniper = SniperMode(max_size_multiplier=0.10)
    assert not sniper.is_armed

    sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95, is_valid=True)
    assert not sniper.evaluate_opportunity(sig, stress_score=0.05)


# --- Category 3 spec suite additions ---


def test_micro_alpha_engine_halt_anchor():
    alpha = MicroAlphaEngine()
    assert alpha.pre_crash_micro_price is None

    alpha.on_halt_triggered(123.45)
    assert alpha.pre_crash_micro_price == pytest.approx(123.45)


def test_sniper_dislocation_trigger():
    alpha = MicroAlphaEngine()
    alpha.on_halt_triggered(100.0)

    at_boundary = alpha.calculate_signal(current_micro_price=97.0, stress_score=0.05, sigma=0.01)
    assert not at_boundary.is_valid  # exactly -3.0% dislocation does not trigger

    above_boundary = alpha.calculate_signal(current_micro_price=96.9, stress_score=0.05, sigma=0.01)
    assert above_boundary.is_valid
    assert above_boundary.direction == "BUY"
