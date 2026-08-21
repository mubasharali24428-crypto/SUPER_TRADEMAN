"""Tests for Synthetic Ecology Reactive Agents and Liquidity Cascades."""

import time
from unittest import mock

import pytest

from trading.synthetic.ecology import (
    BaseEcologyAgent,
    LiquidityBaselineTracker,
    LiquidityEvent,
    ReactiveMarketMakerAgent,
    SyntheticAgentRegistry,
    ToxicFlowPredatorAgent,
)


def test_liquidity_baseline_tracker():
    tracker = LiquidityBaselineTracker(window_seconds=300)
    now_ms = time.time() * 1000.0

    for i in range(10):
        tracker.record_depth(now_ms - i * 1000, 5.0, 5.0)

    baseline = tracker.get_baseline_depth(now_ms)
    assert baseline == 10.0


def test_reactive_market_maker_widens_spread_on_depletion():
    mm = ReactiveMarketMakerAgent(base_spread_bps=5.0, herd_sensitivity=0.40)

    event = LiquidityEvent(
        event_type="QUOTE_WITHDRAWAL",
        withdrawn_qty=6.0,
        current_depth=4.0,
        baseline_depth=10.0,
        depletion_ratio=0.60,  # 60% depletion > 40% threshold
    )

    actions = mm.on_liquidity_change(event)
    assert len(actions) == 1
    assert actions[0]["action"] == "WIDEN_SPREAD"
    # widening factor: 1.0 + (0.6 * 2.0) = 2.2 -> 5.0 * 2.2 = 11.0 bps
    assert mm.current_spread_bps == pytest.approx(11.0)


def test_toxic_flow_predator_sweeps_thin_book():
    predator = ToxicFlowPredatorAgent(
        aggression_threshold=0.50,
        max_sweep_size=5.0,
        direction="SELL",
    )

    event = LiquidityEvent(
        event_type="QUOTE_WITHDRAWAL",
        withdrawn_qty=6.0,
        current_depth=4.0,
        baseline_depth=10.0,
        depletion_ratio=0.60,
    )

    actions = predator.on_liquidity_change(event)
    assert len(actions) == 1
    assert actions[0]["action"] == "IOC_SWEEP"
    assert actions[0]["side"] == "SELL"
    # 80% of current depth 4.0 = 3.2 units
    assert actions[0]["qty"] == 3.2


def test_synthetic_agent_registry_broadcast():
    registry = SyntheticAgentRegistry()
    mm = ReactiveMarketMakerAgent(base_spread_bps=5.0, herd_sensitivity=0.40)
    predator = ToxicFlowPredatorAgent(aggression_threshold=0.50, max_sweep_size=5.0)

    registry.register_agent("mm", mm)
    registry.register_agent("predator", predator)

    event = LiquidityEvent(
        event_type="QUOTE_WITHDRAWAL",
        withdrawn_qty=6.0,
        current_depth=4.0,
        baseline_depth=10.0,
        depletion_ratio=0.60,
    )

    actions = registry.broadcast_liquidity_event(event)
    assert len(actions) == 2


# --- Category 1 spec suite: baseline tracker, spread widening, predator, registry ---


def test_baseline_tracker_initialization():
    tracker = LiquidityBaselineTracker(window_seconds=300)
    assert tracker.window_ms == 300_000.0
    assert len(tracker.depth_samples) == 0
    assert tracker.depth_samples.maxlen == 3000


def test_baseline_tracker_rolling_update():
    tracker = LiquidityBaselineTracker(window_seconds=300)
    now_ms = time.time() * 1000.0

    for i in range(5):
        tracker.record_depth(now_ms - i * 1000, 4.0, 4.0)
    assert tracker.get_baseline_depth(now_ms) == pytest.approx(8.0)

    # Push well beyond capacity to prove the deque bounds memory (no leak).
    for i in range(3500):
        tracker.record_depth(now_ms + i, 1.0, 1.0)
    assert len(tracker.depth_samples) == 3000
    assert tracker.get_baseline_depth(now_ms + 3500) == pytest.approx(2.0)


def test_reactive_spread_widening_trigger():
    mm = ReactiveMarketMakerAgent(base_spread_bps=5.0, herd_sensitivity=0.40)

    at_boundary = LiquidityEvent("QUOTE_WITHDRAWAL", 4.0, 6.0, 10.0, depletion_ratio=0.40)
    assert mm.on_liquidity_change(at_boundary) == []
    assert mm.current_spread_bps == pytest.approx(5.0)

    above_boundary = LiquidityEvent("QUOTE_WITHDRAWAL", 4.1, 5.9, 10.0, depletion_ratio=0.41)
    actions = mm.on_liquidity_change(above_boundary)
    assert len(actions) == 1
    assert actions[0]["action"] == "WIDEN_SPREAD"


def test_reactive_spread_scaling():
    mm = ReactiveMarketMakerAgent(base_spread_bps=5.0, herd_sensitivity=0.40)

    for depletion_ratio in (0.5, 0.7, 0.9):
        event = LiquidityEvent("QUOTE_WITHDRAWAL", 0.0, 0.0, 10.0, depletion_ratio=depletion_ratio)
        mm.on_liquidity_change(event)
        expected_bps = 5.0 * (1.0 + depletion_ratio * 2.0)
        assert mm.current_spread_bps == pytest.approx(expected_bps)


def test_spread_normalization():
    mm = ReactiveMarketMakerAgent(base_spread_bps=5.0, herd_sensitivity=0.40)

    widen_event = LiquidityEvent("QUOTE_WITHDRAWAL", 6.0, 4.0, 10.0, depletion_ratio=0.60)
    mm.on_liquidity_change(widen_event)
    assert mm.current_spread_bps > mm.base_spread_bps

    # Depth recovers to 70% of baseline (i.e. above the 60% recovery line).
    recovery_event = LiquidityEvent("BOOK_RELOAD", 0.0, 7.0, 10.0, depletion_ratio=0.30)
    actions = mm.on_liquidity_change(recovery_event)
    assert actions == []
    assert mm.current_spread_bps == pytest.approx(mm.base_spread_bps)


def test_toxic_flow_predator_detection():
    predator = ToxicFlowPredatorAgent(aggression_threshold=0.50)

    at_boundary = LiquidityEvent("QUOTE_WITHDRAWAL", 5.0, 5.0, 10.0, depletion_ratio=0.50)
    predator.on_liquidity_change(at_boundary)
    assert predator.is_hunting is False

    above_boundary = LiquidityEvent("QUOTE_WITHDRAWAL", 5.1, 4.9, 10.0, depletion_ratio=0.51)
    predator.on_liquidity_change(above_boundary)
    assert predator.is_hunting is True


def test_predator_ioc_sweep_execution():
    predator = ToxicFlowPredatorAgent(aggression_threshold=0.50, max_sweep_size=5.0, direction="SELL")
    event = LiquidityEvent("QUOTE_WITHDRAWAL", 6.0, 4.0, 10.0, depletion_ratio=0.60)

    # Sweep is returned synchronously within the same call -- fires immediately, no scheduling.
    actions = predator.on_liquidity_change(event)
    assert len(actions) == 1
    assert actions[0]["action"] == "IOC_SWEEP"
    assert actions[0]["tif"] == "IOC"


def test_predator_size_limits():
    predator = ToxicFlowPredatorAgent(aggression_threshold=0.50, max_sweep_size=5.0, direction="SELL")
    # 80% of a deep 100-unit book would be 80 units -- must be capped to max_sweep_size.
    event = LiquidityEvent("AGGRESSIVE_SWEEP", 20.0, 100.0, 120.0, depletion_ratio=0.60)

    actions = predator.on_liquidity_change(event)
    assert actions[0]["qty"] == pytest.approx(5.0)


def test_registry_broadcast_liquidity_withdrawal():
    registry = SyntheticAgentRegistry()
    mock_agent = mock.Mock(spec=BaseEcologyAgent)
    mock_agent.on_liquidity_change.return_value = [{"action": "WIDEN_SPREAD"}]
    registry.register_agent("mock_mm", mock_agent)

    event = LiquidityEvent("QUOTE_WITHDRAWAL", 6.0, 4.0, 10.0, depletion_ratio=0.60)
    actions = registry.broadcast_liquidity_event(event)

    mock_agent.on_liquidity_change.assert_called_once_with(event)
    assert actions == [{"action": "WIDEN_SPREAD"}]


def test_registry_broadcast_latency():
    registry = SyntheticAgentRegistry()
    agents = [mock.Mock(spec=BaseEcologyAgent) for _ in range(5)]
    for agent in agents:
        agent.on_liquidity_change.return_value = []
    for i, agent in enumerate(agents):
        registry.register_agent(f"agent_{i}", agent)

    event = LiquidityEvent("QUOTE_WITHDRAWAL", 6.0, 4.0, 10.0, depletion_ratio=0.60)
    registry.broadcast_liquidity_event(event)

    # Broadcast is synchronous: every subscriber has already been invoked by
    # the time broadcast_liquidity_event returns -- i.e. within one tick.
    for agent in agents:
        agent.on_liquidity_change.assert_called_once_with(event)


def test_registry_multi_subscriber():
    registry = SyntheticAgentRegistry()
    mm = ReactiveMarketMakerAgent()
    predator = ToxicFlowPredatorAgent()
    duplicate_mm = ReactiveMarketMakerAgent(agent_id="mm")

    registry.register_agent("mm", mm)
    registry.register_agent("predator", predator)
    registry.register_agent("mm", duplicate_mm)  # re-registering same id replaces, not duplicates

    assert len(registry.agents) == 2
    assert registry.agents["mm"] is duplicate_mm

    event = LiquidityEvent("QUOTE_WITHDRAWAL", 6.0, 4.0, 10.0, depletion_ratio=0.60)
    actions = registry.broadcast_liquidity_event(event)
    assert len(actions) == 2  # one widen + one sweep, no duplicate action from "mm"


def test_ecology_zero_depth_edge_case():
    tracker = LiquidityBaselineTracker()
    now_ms = time.time() * 1000.0
    for i in range(5):
        tracker.record_depth(now_ms - i * 100, 0.0, 0.0)
    assert tracker.get_baseline_depth(now_ms) == 0.0

    predator = ToxicFlowPredatorAgent(aggression_threshold=0.50, max_sweep_size=5.0)
    zero_depth_event = LiquidityEvent("QUOTE_WITHDRAWAL", 0.0, 0.0, 10.0, depletion_ratio=1.0)
    actions = predator.on_liquidity_change(zero_depth_event)
    assert actions == []  # nothing left to sweep, but no crash
    assert predator.is_hunting is True


def test_ecology_survives_90_percent_drop():
    tracker = LiquidityBaselineTracker()
    now_ms = time.time() * 1000.0
    for i in range(10):
        tracker.record_depth(now_ms - i * 100, 5.0, 5.0)
    assert tracker.get_baseline_depth(now_ms) == pytest.approx(10.0)

    tracker.record_depth(now_ms + 1, 0.5, 0.5)  # sudden 90% drop
    assert tracker.get_baseline_depth(now_ms + 1) > 0  # median stays robust to one spike


def test_predator_disabled_in_halted():
    predator = ToxicFlowPredatorAgent(aggression_threshold=0.50, max_sweep_size=5.0)
    predator.is_halted = True

    event = LiquidityEvent("AGGRESSIVE_SWEEP", 6.0, 4.0, 10.0, depletion_ratio=0.60)
    actions = predator.on_liquidity_change(event)

    assert actions == []
    assert predator.is_hunting is False


def test_ecology_metrics_session_reset():
    registry = SyntheticAgentRegistry()
    mm = ReactiveMarketMakerAgent(base_spread_bps=5.0, herd_sensitivity=0.40)
    predator = ToxicFlowPredatorAgent(aggression_threshold=0.50)
    registry.register_agent("mm", mm)
    registry.register_agent("predator", predator)

    now_ms = time.time() * 1000.0
    registry.baseline_tracker.record_depth(now_ms, 5.0, 5.0)
    event = LiquidityEvent("AGGRESSIVE_SWEEP", 6.0, 4.0, 10.0, depletion_ratio=0.60)
    registry.broadcast_liquidity_event(event)

    assert mm.current_spread_bps != mm.base_spread_bps
    assert predator.is_hunting is True
    assert len(registry.baseline_tracker.depth_samples) == 1

    registry.reset_session()

    assert mm.current_spread_bps == mm.base_spread_bps
    assert predator.is_hunting is False
    assert len(registry.baseline_tracker.depth_samples) == 0
