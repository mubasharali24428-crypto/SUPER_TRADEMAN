"""Tests for the Portfolio-Level Governor: aggregate rate limiting and systemic risk halting."""

import pytest

from trading.synthetic.oms_engine import OMSActorEngine, OMSEventTier, OrderStatus
from trading.synthetic.portfolio_governor import PortfolioGovernor


def test_governor_aggregate_message_tracking():
    gov = PortfolioGovernor()
    for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        gov.register_actor(symbol, OMSActorEngine(symbol=symbol))

    gov.record_message("BTC/USDT", now_ts=0.0)
    gov.record_message("BTC/USDT", now_ts=0.1)
    gov.record_message("ETH/USDT", now_ts=0.2)
    gov.record_message("SOL/USDT", now_ts=0.3)

    assert gov.message_counts["BTC/USDT"] == 2
    assert gov.message_counts["ETH/USDT"] == 1
    assert gov.aggregate_message_rate == 4  # tracked across ALL symbol Actors combined


def test_governor_global_rate_limit_enforcement():
    gov = PortfolioGovernor(max_messages_per_sec=10.0, throttle_threshold_pct=1.0)
    gov.register_actor("BTC/USDT", OMSActorEngine(symbol="BTC/USDT"))

    for i in range(10):
        gov.record_message("BTC/USDT", now_ts=float(i) * 0.01)

    assert gov.aggregate_message_rate == 10
    assert gov.is_throttled is True  # hard cap of 10/sec reached
    assert gov.allow_event(OMSEventTier.TIER_2_EXECUTION) is False


def test_governor_suspends_tier2_during_throttle():
    gov = PortfolioGovernor(max_messages_per_sec=100.0, throttle_threshold_pct=0.80)
    gov.register_actor("BTC/USDT", OMSActorEngine(symbol="BTC/USDT"))

    for i in range(79):
        gov.record_message("BTC/USDT", now_ts=float(i) * 0.001)
    assert gov.is_throttled is False
    assert gov.allow_event(OMSEventTier.TIER_2_EXECUTION) is True  # below the 80/sec throttle line

    gov.record_message("BTC/USDT", now_ts=0.08)  # 80th message -> crosses the 80% throttle threshold
    assert gov.is_throttled is True
    assert gov.allow_event(OMSEventTier.TIER_2_EXECUTION) is False  # ALL Tier 2 suspended globally


def test_governor_tier0_bypass():
    gov = PortfolioGovernor(max_messages_per_sec=10.0, throttle_threshold_pct=0.5)
    gov.register_actor("BTC/USDT", OMSActorEngine(symbol="BTC/USDT"))
    for i in range(10):
        gov.record_message("BTC/USDT", now_ts=float(i) * 0.01)
    assert gov.is_throttled is True

    # Even fully saturated -- or halted -- Tier 0 safety/cancel events must never be dropped.
    assert gov.allow_event(OMSEventTier.TIER_0_SAFETY) is True

    gov.trigger_global_halt(now_ts=1.0)
    assert gov.allow_event(OMSEventTier.TIER_0_SAFETY) is True
    assert gov.allow_event(OMSEventTier.TIER_2_EXECUTION) is False


def test_governor_realtime_portfolio_risk():
    gov = PortfolioGovernor(max_portfolio_risk_pct=0.50)
    for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        gov.register_actor(symbol, OMSActorEngine(symbol=symbol))

    gov.update_portfolio_risk("BTC/USDT", 0.05)
    assert gov.portfolio_risk_exposure_pct == pytest.approx(0.05)

    gov.update_portfolio_risk("ETH/USDT", 0.03)
    assert gov.portfolio_risk_exposure_pct == pytest.approx(0.08)  # aggregates in real time

    gov.update_portfolio_risk("BTC/USDT", 0.10)  # BTC's exposure changes -> re-aggregates, not accumulates
    assert gov.portfolio_risk_exposure_pct == pytest.approx(0.13)


def test_governor_global_halt_on_risk_breach():
    gov = PortfolioGovernor(max_portfolio_risk_pct=0.15)
    actors = {s: OMSActorEngine(symbol=s) for s in ["BTC/USDT", "ETH/USDT"]}
    for symbol, actor in actors.items():
        gov.register_actor(symbol, actor)

    breached = gov.update_portfolio_risk("BTC/USDT", 0.10)
    assert breached is False
    assert not gov.is_globally_halted

    breached = gov.update_portfolio_risk("ETH/USDT", 0.10)  # total 0.20 >= 0.15 threshold
    assert breached is True
    assert gov.is_globally_halted is True
    for actor in actors.values():
        assert actor.is_halted is True


def test_global_halt_cancels_resting_orders():
    gov = PortfolioGovernor(max_portfolio_risk_pct=0.15)
    btc = OMSActorEngine(symbol="BTC/USDT")
    eth = OMSActorEngine(symbol="ETH/USDT")
    btc.submit_order("btc_ord", "BUY", 50000.0, 1.0)
    eth.submit_order("eth_ord", "SELL", 3000.0, 2.0)
    gov.register_actor("BTC/USDT", btc)
    gov.register_actor("ETH/USDT", eth)

    gov.trigger_global_halt(now_ts=0.0)

    assert btc.orders["btc_ord"]["status"] == OrderStatus.PENDING_CANCEL
    assert eth.orders["eth_ord"]["status"] == OrderStatus.PENDING_CANCEL
    assert gov.actor_states["BTC/USDT"] == "DEFENSIVE"
    assert gov.actor_states["ETH/USDT"] == "DEFENSIVE"


def test_governor_circuit_breaker_cooldown():
    gov = PortfolioGovernor(halt_cooldown_sec=60.0)
    gov.register_actor("BTC/USDT", OMSActorEngine(symbol="BTC/USDT"))
    gov.trigger_global_halt(now_ts=0.0)

    assert gov.can_resume(now_ts=30.0) is False  # still within cooldown
    assert gov.reset(now_ts=30.0) is False  # refuses to resume early
    assert gov.is_globally_halted is True  # still halted

    assert gov.can_resume(now_ts=61.0) is True
    assert gov.reset(now_ts=61.0) is True
    assert gov.is_globally_halted is False


def test_thundering_herd_no_deadlock():
    """50 symbols shock simultaneously. This is a single-threaded, synchronous simulation
    with no real locks to deadlock on -- the honest proof is deterministic completion with
    every symbol's state landing correctly, nothing silently dropped or left half-processed."""
    gov = PortfolioGovernor(max_messages_per_sec=1000.0, throttle_threshold_pct=0.80, max_portfolio_risk_pct=1.0)
    actors = []
    for i in range(50):
        symbol = f"SYM{i}/USDT"
        actor = OMSActorEngine(symbol=symbol)
        order_id = f"ord_{i}"
        actor.submit_order(order_id, "BUY", 100.0, 1.0)
        gov.register_actor(symbol, actor)
        actors.append((symbol, actor, order_id))

    # All 50 symbols shock at effectively the same instant (now_ts held constant): each
    # fires a burst of messages plus a Tier 0 safety event.
    for symbol, actor, order_id in actors:
        for _ in range(5):
            gov.record_message(symbol, now_ts=0.0)
        gov.update_portfolio_risk(symbol, 0.005)  # each contributes a small slice
        actor.enqueue_event(OMSEventTier.TIER_0_SAFETY, "STALE_QUOTE_DETECTED", {"order_id": order_id})

    for _, actor, _ in actors:
        while actor.event_pq:
            actor.run_event_step()

    assert len(gov.actors) == 50
    assert gov.aggregate_message_rate == 250  # 50 symbols * 5 messages each, none lost
    assert gov.portfolio_risk_exposure_pct == pytest.approx(0.25)  # 50 * 0.005
    assert not gov.is_globally_halted  # 0.25 < max_portfolio_risk_pct=1.0, no false trip
    for symbol, actor, order_id in actors:
        assert actor.orders[order_id]["status"] == OrderStatus.PENDING_CANCEL  # every symbol's cancel processed


def test_system_recovery_from_global_halt():
    gov = PortfolioGovernor(max_portfolio_risk_pct=0.15, halt_cooldown_sec=60.0)
    btc = OMSActorEngine(symbol="BTC/USDT")
    btc.submit_order("btc_ord", "BUY", 50000.0, 1.0)
    gov.register_actor("BTC/USDT", btc)

    gov.update_portfolio_risk("BTC/USDT", 0.20)  # breaches -> global halt
    assert gov.is_globally_halted is True
    assert btc.is_halted is True
    assert gov.allow_event(OMSEventTier.TIER_2_EXECUTION) is False

    # Macro volatility normalizes...
    gov.symbol_risk_pct["BTC/USDT"] = 0.02
    # ...and once the cooldown has elapsed, the governor resets and every Actor resumes.
    resumed = gov.reset(now_ts=gov.halt_triggered_ts + 61.0)

    assert resumed is True
    assert gov.is_globally_halted is False
    assert btc.is_halted is False
    assert gov.actor_states["BTC/USDT"] == "ACTIVE"
    assert gov.allow_event(OMSEventTier.TIER_2_EXECUTION) is True
