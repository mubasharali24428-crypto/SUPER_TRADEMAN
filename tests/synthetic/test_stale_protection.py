"""Tests for Frontier 2 Stale Quote Protection Engine."""

import time
from unittest import mock

import pytest

from trading.synthetic.oms_engine import OMSActorEngine, OMSEventTier
from trading.synthetic.stale_protection import RestingOrder, StaleQuoteProtection
from trading.synthetic.strategy_defense import (
    MicrostructureStrategyDefender,
    SniperMode,
    StrategyDefenseState,
)


def test_stale_quote_canceled_before_match():
    # Order resting at 100.00
    stale_guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    order = RestingOrder("ord_01", "buy", 100.00, 1.0)
    stale_guard.register_order(order)

    # Micro-price moves to 100.15 (15 bps drift)
    # sigma = 0.05 (5 bps), threshold = 1.5 * 0.05 = 0.075 (7.5 bps)
    # Drift 0.15 > 0.075 -> order must be detected as stale
    stale = stale_guard.evaluate(current_micro_price=100.15, current_sigma=0.05)
    assert len(stale) == 1
    assert stale[0].order_id == "ord_01"


def test_healthy_order_not_canceled():
    stale_guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    order = RestingOrder("ord_02", "buy", 100.00, 1.0)
    stale_guard.register_order(order)

    # Micro-price at 100.02 (2 bps drift < 7.5 bps threshold)
    stale = stale_guard.evaluate(current_micro_price=100.02, current_sigma=0.05)
    assert len(stale) == 0


def test_minimum_floor_prevents_noise_cancellation():
    stale_guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    order = RestingOrder("ord_03", "buy", 100.00, 1.0)
    stale_guard.register_order(order)

    # Micro-price very stable (sigma = 0.001)
    # Floor: 5 bps of 100.00 = 0.05
    # Order at 100.04 (4 bps drift < 5 bps floor) -> NOT canceled
    stale = stale_guard.evaluate(current_micro_price=100.04, current_sigma=0.001)
    assert len(stale) == 0


# --- Category 2 spec suite: dynamic threshold, pre-matching, Tier 0, HALTED ---


def test_stale_threshold_volatility_component():
    # Isolate the volatility term: the bps floor is set far below it.
    guard = StaleQuoteProtection(sigma_multiplier=2.0, min_distance_bps=0.01)
    guard.register_order(RestingOrder("ord_vol", "buy", 100.00, 1.0))

    # volatility_threshold = 2.0 * 0.10 = 0.20
    assert guard.evaluate(current_micro_price=100.19, current_sigma=0.10) == []
    stale = guard.evaluate(current_micro_price=100.21, current_sigma=0.10)
    assert len(stale) == 1


def test_stale_threshold_micro_price_component():
    # Isolate the micro-price (bps) floor: sigma is effectively zero.
    guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=10.0)
    guard.register_order(RestingOrder("ord_bps", "buy", 100.00, 1.0))

    # min_threshold = (10.0 / 10000) * 100.00 = 0.10
    assert guard.evaluate(current_micro_price=100.09, current_sigma=0.0) == []
    stale = guard.evaluate(current_micro_price=100.11, current_sigma=0.0)
    assert len(stale) == 1


def test_stale_threshold_max_logic():
    # High sigma: the volatility term (0.15) dominates the bps floor (0.05).
    guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    guard.register_order(RestingOrder("ord_max_vol", "buy", 100.00, 1.0))
    stale = guard.evaluate(current_micro_price=100.20, current_sigma=0.10)
    assert len(stale) == 1

    # Low sigma: the bps floor (0.05) dominates the volatility term (0.0015).
    guard2 = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    guard2.register_order(RestingOrder("ord_max_bps", "buy", 100.00, 1.0))
    stale2 = guard2.evaluate(current_micro_price=100.06, current_sigma=0.001)
    assert len(stale2) == 1


def test_pre_matching_evaluation():
    defender = MicrostructureStrategyDefender()
    call_order = []
    defender.stale_protection = mock.Mock(spec=StaleQuoteProtection)
    defender.stale_protection.evaluate.side_effect = lambda *a, **k: call_order.append("stale_eval") or []

    def run_matching_cycle():
        call_order.append("match")

    defender.evaluate_stale_quotes(current_micro_price=100.0)
    run_matching_cycle()

    assert call_order == ["stale_eval", "match"]


def test_drifted_quote_auto_cancel():
    guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    guard.register_order(RestingOrder("ord_drift", "sell", 200.00, 2.0))

    # threshold = max(1.5*0.10=0.15, 0.0005*200=0.10) = 0.15; drift 0.30 > 0.15
    stale = guard.evaluate(current_micro_price=200.30, current_sigma=0.10)
    assert len(stale) == 1
    assert stale[0].order_id == "ord_drift"


def test_healthy_quote_preservation():
    guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    guard.register_order(RestingOrder("ord_healthy", "sell", 200.00, 2.0))

    # threshold = max(0.15, 0.10) = 0.15; drift 0.05 < 0.15
    stale = guard.evaluate(current_micro_price=200.05, current_sigma=0.10)
    assert stale == []
    assert "ord_healthy" in guard.monitored_orders


def test_noise_floor_check():
    guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    guard.register_order(RestingOrder("ord_noise", "buy", 100.00, 1.0))

    # Micro-fluctuation of 1 bp -- well under the 5 bps noise floor.
    stale = guard.evaluate(current_micro_price=100.01, current_sigma=0.0001)
    assert stale == []


def test_stale_protection_missing_data_fallback():
    guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    guard.register_order(RestingOrder("ord_missing", "buy", 100.00, 1.0))

    stale = guard.evaluate(current_micro_price=None, current_sigma=0.10)

    assert stale == []
    assert "ord_missing" in guard.monitored_orders  # untouched, not spuriously canceled


def test_tier0_ephemeral_asymmetric_threshold():
    sniper = SniperMode()
    exit_order = sniper.post_passive_exit(price=100.00, qty=1.0, side="SELL")
    assert exit_order.tag == "Tier_0_Ephemeral"

    # Tier_0_Ephemeral threshold is 0.25*sigma = 0.025 -- far tighter than the
    # general stale_protection sigma_multiplier of 1.5.
    canceled = sniper.evaluate_microstructure_exit_drift(current_micro_price=99.97, sigma=0.10)
    assert canceled is True
    assert sniper.active_passive_exit is None


def test_stale_cancel_priority_tagging():
    guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    guard.register_order(RestingOrder("ord_tag", "buy", 100.00, 1.0))
    stale = guard.evaluate(current_micro_price=100.20, current_sigma=0.10)
    assert len(stale) == 1

    oms = OMSActorEngine()
    for order in stale:
        oms.enqueue_event(
            OMSEventTier.TIER_0_SAFETY,
            "STALE_QUOTE_DETECTED",
            {"order_id": order.order_id},
        )

    assert oms.event_pq[0].priority == OMSEventTier.TIER_0_SAFETY.value
    assert oms.event_pq[0].event_type == "STALE_QUOTE_DETECTED"


def test_stale_protection_disabled_in_halted():
    defender = MicrostructureStrategyDefender()
    defender.stale_protection = mock.Mock(spec=StaleQuoteProtection)
    defender.current_state = StrategyDefenseState.HALTED

    result = defender.evaluate_stale_quotes(current_micro_price=100.0)

    assert result == []
    defender.stale_protection.evaluate.assert_not_called()


def test_volatility_spike_threshold_expansion():
    guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    guard.register_order(RestingOrder("ord_spike", "buy", 100.00, 1.0))

    # Calm regime: 0.08 drift is stale against a tight threshold.
    stale_calm = guard.evaluate(current_micro_price=100.08, current_sigma=0.02)
    assert len(stale_calm) == 1

    # A sudden volatility spike instantly widens the threshold on the very
    # next evaluation, sparing the same order from a false cancel.
    stale_spike = guard.evaluate(current_micro_price=100.08, current_sigma=0.20)
    assert stale_spike == []


def test_stale_protection_multiple_child_orders():
    guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    parent_id = "parent_001"
    guard.register_order(RestingOrder("child_1", "buy", 100.00, 0.5, source="INTERNAL_STRATEGY", parent_order_id=parent_id))
    guard.register_order(RestingOrder("child_2", "buy", 100.30, 0.5, source="INTERNAL_STRATEGY", parent_order_id=parent_id))
    guard.register_order(RestingOrder("child_3", "buy", 100.00, 0.5, source="INTERNAL_STRATEGY", parent_order_id="other_parent"))

    # threshold = max(1.5*0.10=0.15, 0.05) = 0.15
    stale = guard.evaluate(current_micro_price=100.00, current_sigma=0.10)

    stale_ids = {order.order_id for order in stale}
    assert stale_ids == {"child_2"}
    assert stale[0].parent_order_id == parent_id


def test_stale_protection_latency_budget():
    guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    for i in range(500):
        guard.register_order(RestingOrder(f"ord_{i}", "buy", 100.00 + (i % 5) * 0.01, 1.0))

    start = time.perf_counter()
    guard.evaluate(current_micro_price=100.02, current_sigma=0.05)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    # ponytail: generous CI-safe bound (the real budget is microsecond-scale
    # on dedicated hardware); this just guards against pathological blowup.
    assert elapsed_ms < 50.0


def test_stale_protection_audit_logging():
    guard = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
    guard.register_order(RestingOrder("ord_audit", "buy", 100.00, 1.0))

    with mock.patch("trading.synthetic.stale_protection.logger") as mock_logger:
        stale = guard.evaluate(current_micro_price=100.20, current_sigma=0.10)

    assert len(stale) == 1
    mock_logger.info.assert_called_once()
    logged_message = mock_logger.info.call_args[0][0]
    assert "ord_audit" in logged_message
    assert "drifted" in logged_message
    assert "threshold" in logged_message
