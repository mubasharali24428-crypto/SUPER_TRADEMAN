"""Unit tests for Task 2: Frontier 3 Upgrades (Sniper Constraints, Ammunition, and Microstructure Exit)."""

import pytest

from trading.synthetic.ecology import LiquidityEvent
from trading.synthetic.strategy_defense import (
    MicroAlphaEngine,
    SniperMode,
    TradeSignal,
)


def test_temporal_persistence_filters_noise():
    """Prove a 1-tick drop does not trigger the Sniper, but a 50-tick sustained drop does."""
    sniper = SniperMode(required_persistence_ticks=10)
    sniper.is_armed = True

    # 1-tick drop signal (noise)
    noise_sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95, sustained_ticks=1)
    assert not sniper.evaluate_opportunity(noise_sig, stress_score=0.05, current_price=95.0)

    # 50-tick sustained drop signal (persistent trend breakdown)
    sustained_sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95, sustained_ticks=50)
    assert sniper.evaluate_opportunity(sustained_sig, stress_score=0.05, current_price=95.0)


def test_toxicity_filter_veto():
    """Prove the Sniper vetoes a 3% drop if the aggressive sell volume is low (hollow drop)."""
    sniper = SniperMode(min_toxicity_pct=0.95)
    sniper.is_armed = True

    # Hollow drop: toxicity is only 0.80 (< 0.95 95th percentile threshold)
    hollow_sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95, order_flow_toxicity_pct=0.80)
    assert not sniper.evaluate_opportunity(hollow_sig, stress_score=0.05, current_price=95.0)

    # Genuine toxic institutional capitulation: toxicity is 0.98
    toxic_sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95, order_flow_toxicity_pct=0.98)
    assert sniper.evaluate_opportunity(toxic_sig, stress_score=0.05, current_price=95.0)


def test_retracement_reload_hysteresis():
    """Prove the Sniper cannot fire a second time until the price bounces, preventing catching a falling knife."""
    sniper = SniperMode(retracement_bounce_pct=0.015, base_cooldown_sec=0.0)
    sniper.is_armed = True

    sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95)

    # First entry at 95.00
    assert sniper.evaluate_opportunity(sig, stress_score=0.05, current_price=95.0)
    order1 = sniper.execute_sniper_trade(sig, base_size=10.0, current_price=95.0)
    assert order1.qty == 1.0

    # Price falls further to 94.00 (falling knife, no bounce)
    assert not sniper.evaluate_opportunity(sig, stress_score=0.05, current_price=94.0)

    # Price bounces by > 1.5% to 96.50 (96.50 / 95.00 = +1.58%)
    assert sniper.evaluate_opportunity(sig, stress_score=0.05, current_price=96.50)


def test_tier0_ephemeral_stale_cancel():
    """Prove the passive exit order is canceled on a micro-drift of just 0.25 * sigma."""
    sniper = SniperMode()
    # Place passive exit at 100.00
    exit_order = sniper.post_passive_exit(price=100.00, qty=1.0, side="SELL")
    assert exit_order.tag == "Tier_0_Ephemeral"
    assert exit_order.is_active

    # sigma = 0.20 -> threshold = 0.25 * 0.20 = 0.05
    # Micro-price drifts against SELL exit to 99.90 (drift = 0.10 > 0.05)
    canceled = sniper.evaluate_microstructure_exit_drift(current_micro_price=99.90, sigma=0.20)
    assert canceled
    assert sniper.active_passive_exit is None


def test_predator_wake_killswitch():
    """Prove the passive exit is instantly canceled the microsecond a LIQUIDITY_WITHDRAWAL event is broadcast."""
    sniper = SniperMode()
    sniper.post_passive_exit(price=100.00, qty=1.0, side="SELL")
    assert sniper.active_passive_exit is not None

    # Ecology broadcasts severe quote withdrawal
    event = LiquidityEvent(
        event_type="QUOTE_WITHDRAWAL",
        withdrawn_qty=5.0,
        current_depth=2.0,
        baseline_depth=10.0,
        depletion_ratio=0.80,
    )

    killed = sniper.on_liquidity_event_killswitch(event)
    assert killed
    assert sniper.active_passive_exit is None


# --- Category 3 spec suite additions ---
# Note: test_retracement_reload_hysteresis (spec #45) and test_ema_absorbs_minor_shocks
# already exist above / in test_regime_validator_v2.py and are not duplicated here.


def test_sniper_z_score_trigger():
    sniper = SniperMode()
    sniper.is_armed = True

    at_boundary = TradeSignal("BUY", edge_zscore=3.0, mean_reversion_direction="BUY", confidence=0.95)
    assert not sniper.evaluate_opportunity(at_boundary, stress_score=0.05)

    above_boundary = TradeSignal("BUY", edge_zscore=3.01, mean_reversion_direction="BUY", confidence=0.95)
    assert sniper.evaluate_opportunity(above_boundary, stress_score=0.05)


def test_sniper_micro_spread_trigger():
    """S_micro (stress_score) must be strictly below 0.10 for the Sniper to evaluate at all."""
    sniper = SniperMode()
    sniper.is_armed = True
    sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95)

    assert not sniper.evaluate_opportunity(sig, stress_score=0.10)
    assert sniper.evaluate_opportunity(sig, stress_score=0.09)


def test_temporal_persistence_1_tick_rejection():
    sniper = SniperMode(required_persistence_ticks=10)
    sniper.is_armed = True
    noise_sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95, sustained_ticks=1)
    assert not sniper.evaluate_opportunity(noise_sig, stress_score=0.05, current_price=95.0)


def test_temporal_persistence_10_tick_acceptance():
    sniper = SniperMode(required_persistence_ticks=10)
    sniper.is_armed = True
    sustained_sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95, sustained_ticks=10)
    assert sniper.evaluate_opportunity(sustained_sig, stress_score=0.05, current_price=95.0)


def test_toxicity_filter_hollow_drop_veto():
    sniper = SniperMode(min_toxicity_pct=0.95)
    sniper.is_armed = True
    hollow_sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95, order_flow_toxicity_pct=0.80)
    assert not sniper.evaluate_opportunity(hollow_sig, stress_score=0.05, current_price=95.0)


def test_toxicity_filter_high_volume_acceptance():
    sniper = SniperMode(min_toxicity_pct=0.95)
    sniper.is_armed = True
    toxic_sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95, order_flow_toxicity_pct=0.98)
    assert sniper.evaluate_opportunity(toxic_sig, stress_score=0.05, current_price=95.0)


def test_ammunition_quota_first_shot():
    sniper = SniperMode(max_size_multiplier=0.10, inventory_quota_max=0.50, base_cooldown_sec=0.0)
    sniper.is_armed = True
    sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95)

    assert sniper.evaluate_opportunity(sig, stress_score=0.05)
    order = sniper.execute_sniper_trade(sig, base_size=10.0)

    assert order.qty == pytest.approx(1.0)  # exactly 0.10x of base_size 10.0
    assert sniper.accumulated_inventory_pct == pytest.approx(0.10)


def test_ammunition_quota_hard_cap():
    sniper = SniperMode(max_size_multiplier=0.10, inventory_quota_max=0.50, base_cooldown_sec=0.0)
    sniper.is_armed = True
    sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95)

    for shot in range(5):
        assert sniper.evaluate_opportunity(sig, stress_score=0.05), f"shot {shot + 1} should be permitted"
        sniper.execute_sniper_trade(sig, base_size=10.0)

    assert sniper.accumulated_inventory_pct == pytest.approx(0.50)  # hard cap reached exactly on shot 5


def test_ammunition_quota_magazine_empty():
    sniper = SniperMode(max_size_multiplier=0.10, inventory_quota_max=0.50, base_cooldown_sec=0.0)
    sniper.is_armed = True
    sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95)

    for _ in range(5):
        sniper.evaluate_opportunity(sig, stress_score=0.05)
        sniper.execute_sniper_trade(sig, base_size=10.0)

    # 6th shot would push accumulated inventory past the 0.50x quota -- magazine empty.
    assert not sniper.evaluate_opportunity(sig, stress_score=0.05)


def test_retracement_reload_falling_knife():
    sniper = SniperMode(retracement_bounce_pct=0.015, base_cooldown_sec=0.0)
    sniper.is_armed = True
    sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95)

    assert sniper.evaluate_opportunity(sig, stress_score=0.05, current_price=95.0)
    sniper.execute_sniper_trade(sig, base_size=10.0, current_price=95.0)

    # Price keeps falling with no bounce -- Sniper must stay disarmed for re-entry.
    assert not sniper.evaluate_opportunity(sig, stress_score=0.05, current_price=94.0)
    assert not sniper.evaluate_opportunity(sig, stress_score=0.05, current_price=93.0)
    assert not sniper.evaluate_opportunity(sig, stress_score=0.05, current_price=90.0)


def test_volatility_scaled_cooldown_stretch():
    sniper = SniperMode(base_cooldown_sec=5.0)
    sniper.is_armed = True
    sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95)
    assert sniper.evaluate_opportunity(sig, stress_score=0.05, now_ts=100.0, vol_ratio=1.0)
    sniper.execute_sniper_trade(sig, base_size=10.0, now_ts=100.0)

    # High session volatility (vol_ratio=3.0) stretches the 5s cooldown to 15s;
    # 10s later is still inside the stretched window.
    assert not sniper.evaluate_opportunity(sig, stress_score=0.05, now_ts=110.0, vol_ratio=3.0)


def test_volatility_scaled_cooldown_shrink():
    sniper = SniperMode(base_cooldown_sec=5.0)
    sniper.is_armed = True
    sig = TradeSignal("BUY", edge_zscore=4.0, mean_reversion_direction="BUY", confidence=0.95)
    assert sniper.evaluate_opportunity(sig, stress_score=0.05, now_ts=100.0, vol_ratio=1.0)
    sniper.execute_sniper_trade(sig, base_size=10.0, now_ts=100.0)

    # Low session volatility (vol_ratio=0.2) shrinks the 5s cooldown to 1s;
    # 2s later is already past the shrunken window.
    assert sniper.evaluate_opportunity(sig, stress_score=0.05, now_ts=102.0, vol_ratio=0.2)


def test_microstructure_exit_tier0_tagging():
    sniper = SniperMode()
    exit_order = sniper.post_passive_exit(price=100.00, qty=1.0, side="SELL")

    assert exit_order.tag == "Tier_0_Ephemeral"
    assert sniper.active_passive_exit is exit_order


def test_microstructure_exit_asymmetric_cancel():
    sigma = 0.20

    sell_sniper = SniperMode()
    sell_sniper.post_passive_exit(price=100.00, qty=1.0, side="SELL")
    # For a SELL exit, adverse drift is downward beyond 0.25*sigma = 0.05.
    assert not sell_sniper.evaluate_microstructure_exit_drift(current_micro_price=99.96, sigma=sigma)  # 0.04 < 0.05
    assert sell_sniper.evaluate_microstructure_exit_drift(current_micro_price=99.90, sigma=sigma)  # 0.10 > 0.05
    assert sell_sniper.active_passive_exit is None

    buy_sniper = SniperMode()
    buy_sniper.post_passive_exit(price=100.00, qty=1.0, side="BUY")
    # For a BUY exit, adverse drift is upward beyond 0.25*sigma -- the opposite direction.
    assert not buy_sniper.evaluate_microstructure_exit_drift(current_micro_price=100.04, sigma=sigma)  # 0.04 < 0.05
    assert buy_sniper.evaluate_microstructure_exit_drift(current_micro_price=100.10, sigma=sigma)  # 0.10 > 0.05


def test_microstructure_exit_predator_killswitch():
    sniper = SniperMode()
    sniper.post_passive_exit(price=100.00, qty=1.0, side="SELL")

    event = LiquidityEvent("QUOTE_WITHDRAWAL", 5.0, 2.0, 10.0, depletion_ratio=0.80)
    killed = sniper.on_liquidity_event_killswitch(event)  # synchronous: canceled within this same call

    assert killed is True
    assert sniper.active_passive_exit is None
