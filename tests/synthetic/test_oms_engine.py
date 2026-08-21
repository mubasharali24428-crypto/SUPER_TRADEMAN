"""Unit tests for Task 3: OMS Execution Engine, Actor Event Loop, Priority Queue, and Air-Gap Reconciler."""

from unittest import mock

import pytest

from trading.synthetic.oms_engine import (
    AtomicMailbox,
    ExchangeMessage,
    OMSActorEngine,
    OMSEventTier,
    OrderStatus,
)


def test_priority_queue_preempts_execution():
    """Prove a Tier 0 STALE_QUOTE_DETECTED event is processed before a Tier 2 SNIPER_ENTRY event."""
    engine = OMSActorEngine(symbol="BTC/USDT")
    engine.submit_order("ord_active", "BUY", 50000.0, 1.0)

    # 1. Enqueue Tier 2 Execution FIRST
    engine.enqueue_event(
        tier=OMSEventTier.TIER_2_EXECUTION,
        event_type="SNIPER_ENTRY",
        payload={"order_id": "ord_sniper", "side": "BUY", "price": 49000.0, "qty": 0.5},
    )

    # 2. Enqueue Tier 0 Safety LATER
    engine.enqueue_event(
        tier=OMSEventTier.TIER_0_SAFETY,
        event_type="STALE_QUOTE_DETECTED",
        payload={"order_id": "ord_active"},
    )

    # Run one step: Tier 0 must preempt Tier 2
    step1 = engine.run_event_step()
    assert step1 is not None
    assert step1.event_type == "STALE_QUOTE_DETECTED"
    assert engine.orders["ord_active"]["status"] == OrderStatus.PENDING_CANCEL

    # Second step runs Tier 2
    step2 = engine.run_event_step()
    assert step2 is not None
    assert step2.event_type == "SNIPER_ENTRY"
    assert "ord_sniper" in engine.orders


def test_pending_cancel_catches_late_fill():
    """Prove that if a FILL arrives after cancel request, OMS updates inventory and discards rejection."""
    engine = OMSActorEngine(symbol="BTC/USDT")
    engine.submit_order("ord_cancel_target", "BUY", 50000.0, 1.0)
    engine.request_cancel("ord_cancel_target")
    assert engine.orders["ord_cancel_target"]["status"] == OrderStatus.PENDING_CANCEL

    # Late fill arrives from exchange (seq_num=1)
    fill_msg = ExchangeMessage(seq_num=1, order_id="ord_cancel_target", msg_type="FILL", qty=1.0, price=50000.0)
    engine.on_exchange_message(fill_msg)

    assert engine.orders["ord_cancel_target"]["status"] == OrderStatus.FILLED
    assert engine.local_position == 1.0

    # Exchange then sends CANCEL_REJECTED (seq_num=2)
    rej_msg = ExchangeMessage(seq_num=2, order_id="ord_cancel_target", msg_type="CANCEL_REJECTED", qty=0.0, price=0.0)
    engine.on_exchange_message(rej_msg)

    # Position remains 1.0 and status remains FILLED
    assert engine.orders["ord_cancel_target"]["status"] == OrderStatus.FILLED
    assert engine.local_position == 1.0


def test_sequence_number_supremacy():
    """Prove that out-of-order messages are processed in strict logical sequence based on seq_num."""
    engine = OMSActorEngine(symbol="BTC/USDT")
    engine.submit_order("ord_seq", "BUY", 50000.0, 1.0)
    engine.request_cancel("ord_seq")

    # Message seq_num=2 arrives BEFORE seq_num=1
    msg2 = ExchangeMessage(seq_num=2, order_id="ord_seq", msg_type="CANCEL_REJECTED", qty=0.0, price=0.0)
    msg1 = ExchangeMessage(seq_num=1, order_id="ord_seq", msg_type="FILL", qty=1.0, price=50000.0)

    engine.on_exchange_message(msg2)
    # At this point, next expected is 1, so msg2 must be buffered and position remains 0
    assert engine.local_position == 0.0

    # Now msg1 arrives
    engine.on_exchange_message(msg1)
    # Both msg1 and msg2 are applied in order (Fill -> Cancel Reject)
    assert engine.local_position == 1.0
    assert engine.orders["ord_seq"]["status"] == OrderStatus.FILLED


def test_safe_boundary_drain_preserves_atomicity():
    """Prove that a FORCE_STATE_SYNC in the mailbox is processed at the safe boundary between events."""
    engine = OMSActorEngine(symbol="BTC/USDT")
    engine.local_position = 5.0

    # Background thread deposits sync snapshot
    engine.mailbox.put_state_sync({"exchange_position": 5.2})

    # Run step: Safe boundary drains mailbox and applies DELTA_SYNC
    engine.run_event_step()
    assert engine.local_position == 5.2
    assert "DELTA_SYNC" in engine.processed_events


def test_delta_sync_and_circuit_breaker():
    """Prove that small discrepancy adjusts via DELTA_SYNC, but >10% trips circuit breaker and halts."""
    engine = OMSActorEngine(symbol="BTC/USDT", max_discrepancy_pct=0.10)
    engine.local_position = 10.0

    # 1. Small 2% discrepancy (10.0 -> 10.2)
    engine.mailbox.put_state_sync({"exchange_position": 10.2})
    engine.run_event_step()
    assert engine.local_position == 10.2
    assert not engine.circuit_breaker_tripped
    assert not engine.is_halted

    # 2. Massive 30% discrepancy (10.2 -> 14.0)
    engine.mailbox.put_state_sync({"exchange_position": 14.0})
    engine.run_event_step()
    assert engine.circuit_breaker_tripped
    assert engine.is_halted


# --- Category 5 spec suite: OMS Execution Engine & Actor Model ---


def test_actor_sequential_processing():
    engine = OMSActorEngine()
    assert engine.is_processing_event is False

    engine.enqueue_event(OMSEventTier.TIER_2_EXECUTION, "SNIPER_ENTRY", {"order_id": "o1", "side": "BUY", "price": 100.0, "qty": 1.0})
    engine.enqueue_event(OMSEventTier.TIER_2_EXECUTION, "SNIPER_ENTRY", {"order_id": "o2", "side": "BUY", "price": 100.0, "qty": 1.0})

    step1 = engine.run_event_step()
    # is_processing_event is always back to False once a step returns -- no event is ever
    # left "in flight" between steps, proving strictly sequential, non-overlapping execution.
    assert engine.is_processing_event is False
    assert step1.event_type == "SNIPER_ENTRY"
    assert len(engine.event_pq) == 1  # exactly one event consumed per step

    engine.run_event_step()
    assert engine.is_processing_event is False
    assert len(engine.event_pq) == 0


def test_priority_queue_ordering():
    engine = OMSActorEngine()
    engine.enqueue_event(OMSEventTier.TIER_2_EXECUTION, "SNIPER_ENTRY", {"order_id": "t2", "side": "BUY", "price": 100.0, "qty": 1.0})
    engine.enqueue_event(OMSEventTier.TIER_1_STATE, "REGIME_SHIFT", {})
    engine.enqueue_event(OMSEventTier.TIER_0_SAFETY, "KILL_SWITCH", {"order_id": "nonexistent"})

    order = [engine.run_event_step().event_type for _ in range(3)]
    assert order == ["KILL_SWITCH", "REGIME_SHIFT", "SNIPER_ENTRY"]


def test_tier0_preempts_tier2():
    engine = OMSActorEngine()
    engine.enqueue_event(OMSEventTier.TIER_2_EXECUTION, "SNIPER_ENTRY", {"order_id": "sniper1", "side": "BUY", "price": 100.0, "qty": 1.0})
    engine.enqueue_event(OMSEventTier.TIER_0_SAFETY, "STALE_QUOTE_DETECTED", {"order_id": "stale1"})

    first = engine.run_event_step()
    assert first.event_type == "STALE_QUOTE_DETECTED"  # Tier 0, queued second, still processed first


def test_pending_cancel_state_transition():
    engine = OMSActorEngine()
    engine.submit_order("ord1", "BUY", 100.0, 1.0)
    assert engine.orders["ord1"]["status"] == OrderStatus.ACTIVE

    result = engine.request_cancel("ord1")

    assert result is True
    assert engine.orders["ord1"]["status"] == OrderStatus.PENDING_CANCEL  # immediate, no queueing needed


def test_pending_cancel_memory_retention():
    engine = OMSActorEngine()
    engine.submit_order("ord1", "BUY", 100.0, 1.0)
    engine.request_cancel("ord1")

    assert "ord1" in engine.orders  # not deleted/evicted while PENDING_CANCEL
    assert engine.orders["ord1"]["status"] == OrderStatus.PENDING_CANCEL

    # A late fill must still find the order in memory to apply against.
    fill = ExchangeMessage(seq_num=1, order_id="ord1", msg_type="FILL", qty=1.0, price=100.0)
    engine.on_exchange_message(fill)
    assert engine.orders["ord1"]["status"] == OrderStatus.FILLED


def test_sequence_number_supremacy_fill_first():
    engine = OMSActorEngine()
    engine.submit_order("ord1", "BUY", 100.0, 1.0)
    engine.request_cancel("ord1")
    engine.next_expected_seq_num = 101  # simulate 100 prior messages already processed

    reject_msg = ExchangeMessage(seq_num=102, order_id="ord1", msg_type="CANCEL_REJECTED", qty=0.0, price=0.0)
    fill_msg = ExchangeMessage(seq_num=101, order_id="ord1", msg_type="FILL", qty=1.0, price=100.0)

    # The higher-seq CANCEL_REJECTED physically arrives first (network jitter);
    # sequence supremacy must hold it back until the lower-seq FILL arrives.
    engine.on_exchange_message(reject_msg)
    assert engine.local_position == 0.0  # buffered, not yet applied
    assert 102 in engine.seq_buffer

    engine.on_exchange_message(fill_msg)
    # Both apply now, strictly in seq order: FILL(101) updates inventory and flips
    # PENDING_CANCEL -> FILLED, then CANCEL_REJECTED(102) is a safe no-op/discard.
    assert engine.local_position == 1.0
    assert engine.orders["ord1"]["status"] == OrderStatus.FILLED
    assert engine.next_expected_seq_num == 103


def test_sequence_number_supremacy_reject_first():
    engine = OMSActorEngine()
    engine.submit_order("ord2", "BUY", 100.0, 1.0)
    engine.request_cancel("ord2")
    engine.next_expected_seq_num = 101

    fill_msg = ExchangeMessage(seq_num=102, order_id="ord2", msg_type="FILL", qty=1.0, price=100.0)
    reject_msg = ExchangeMessage(seq_num=101, order_id="ord2", msg_type="CANCEL_REJECTED", qty=0.0, price=0.0)

    # The higher-seq FILL physically arrives first; sequence supremacy holds it back
    # until the lower-seq CANCEL_REJECTED (which happened first, chronologically) arrives.
    engine.on_exchange_message(fill_msg)
    assert engine.local_position == 0.0  # buffered, not yet applied
    assert 102 in engine.seq_buffer

    engine.on_exchange_message(reject_msg)
    # CANCEL_REJECTED(101) applies first as a no-op (order not yet filled), then
    # FILL(102) applies and correctly flips PENDING_CANCEL -> FILLED.
    assert engine.local_position == 1.0
    assert engine.orders["ord2"]["status"] == OrderStatus.FILLED
    assert engine.next_expected_seq_num == 103


def test_late_fill_on_pending_cancel():
    engine = OMSActorEngine()
    engine.submit_order("ord1", "SELL", 100.0, 2.0)
    engine.request_cancel("ord1")

    late_fill = ExchangeMessage(seq_num=1, order_id="ord1", msg_type="FILL", qty=2.0, price=100.0)
    engine.on_exchange_message(late_fill)

    assert engine.orders["ord1"]["status"] == OrderStatus.FILLED
    assert engine.local_position == pytest.approx(-2.0)  # SELL fill decreases position


def test_late_rejection_on_filled_order():
    engine = OMSActorEngine()
    engine.submit_order("ord1", "BUY", 100.0, 1.0)

    fill = ExchangeMessage(seq_num=1, order_id="ord1", msg_type="FILL", qty=1.0, price=100.0)
    engine.on_exchange_message(fill)
    assert engine.orders["ord1"]["status"] == OrderStatus.FILLED

    late_reject = ExchangeMessage(seq_num=2, order_id="ord1", msg_type="CANCEL_REJECTED", qty=0.0, price=0.0)
    engine.on_exchange_message(late_reject)  # must not raise, must not change state

    assert engine.orders["ord1"]["status"] == OrderStatus.FILLED
    assert engine.local_position == pytest.approx(1.0)


def test_actor_rejects_direct_mutation():
    engine = OMSActorEngine()
    engine.submit_order("ord1", "BUY", 100.0, 1.0)

    with pytest.raises(TypeError):
        engine.orders["ord1"] = {"status": "HACKED"}

    with pytest.raises(TypeError):
        engine.orders["evil_order"] = {"status": OrderStatus.ACTIVE}

    # Legitimate mutation, routed through the Actor's own methods, still works.
    engine.request_cancel("ord1")
    assert engine.orders["ord1"]["status"] == OrderStatus.PENDING_CANCEL


def test_ring_buffer_backpressure():
    engine = OMSActorEngine()
    # Simulate load: a burst of low-priority Tier 2 events...
    for i in range(200):
        engine.enqueue_event(OMSEventTier.TIER_2_EXECUTION, "SNIPER_ENTRY", {"order_id": f"t2_{i}", "side": "BUY", "price": 100.0, "qty": 0.1})
    # ...interspersed with a handful of critical Tier 0 events.
    tier0_order_ids = [f"stale_{i}" for i in range(5)]
    for order_id in tier0_order_ids:
        engine.submit_order(order_id, "BUY", 100.0, 1.0)
        engine.enqueue_event(OMSEventTier.TIER_0_SAFETY, "STALE_QUOTE_DETECTED", {"order_id": order_id})

    assert len(engine.event_pq) == 205  # nothing dropped on enqueue, unbounded by design

    processed_first_five = [engine.run_event_step().event_type for _ in range(5)]
    # All 5 Tier 0 events drain first, none lost under the Tier 2 backlog.
    assert processed_first_five == ["STALE_QUOTE_DETECTED"] * 5
    for order_id in tier0_order_ids:
        assert engine.orders[order_id]["status"] == OrderStatus.PENDING_CANCEL


def test_actor_symbol_isolation():
    btc_engine = OMSActorEngine(symbol="BTC/USDT")
    eth_engine = OMSActorEngine(symbol="ETH/USDT")

    btc_engine.submit_order("btc_ord", "BUY", 50000.0, 1.0)
    btc_engine.local_position = 1.0

    assert btc_engine.symbol == "BTC/USDT"
    assert eth_engine.symbol == "ETH/USDT"
    assert "btc_ord" not in eth_engine.orders
    assert eth_engine.local_position == 0.0
    assert dict(eth_engine.orders) == {}


def test_oms_message_formatting():
    engine = OMSActorEngine(symbol="BTC/USDT")
    engine.submit_order("ord1", "BUY", 50000.0, 1.0)

    msg = engine.format_outgoing_order_message("ord1")

    assert msg["symbol"] == "BTC/USDT"
    assert msg["order_id"] == "ord1"
    assert msg["side"] == "BUY"
    assert msg["price"] == 50000.0
    assert msg["qty"] == 1.0
    assert msg["msg_type"] == "NEW_ORDER"
    assert msg["timestamp_ms"] > 0


def test_oms_exchange_reject_handling():
    engine = OMSActorEngine()
    engine.submit_order("ord1", "BUY", -1.0, 1.0)  # invalid price, simulating what the exchange would reject

    reject_msg = ExchangeMessage(seq_num=1, order_id="ord1", msg_type="REJECT", qty=0.0, price=0.0)

    with mock.patch("trading.synthetic.oms_engine.logger") as mock_logger:
        engine.on_exchange_message(reject_msg)

    assert engine.orders["ord1"]["status"] == OrderStatus.REJECTED
    mock_logger.error.assert_called_once()


def test_oms_graceful_shutdown():
    engine = OMSActorEngine()
    for i in range(10):
        engine.enqueue_event(OMSEventTier.TIER_2_EXECUTION, "SNIPER_ENTRY", {"order_id": f"ord_{i}", "side": "BUY", "price": 100.0, "qty": 1.0})

    flushed = engine.shutdown()

    assert flushed == 10
    assert len(engine.event_pq) == 0  # nothing left queued, no data loss
    assert len(engine.orders) == 10  # every SNIPER_ENTRY was actually applied, not skipped


# --- Category 6 spec suite: Asynchronous Air-Gap Reconciler ---


def test_background_thread_atomic_mailbox():
    mailbox = AtomicMailbox()
    mailbox.put_state_sync({"exchange_position": 5.0, "event": "FORCE_STATE_SYNC"})

    snapshot = mailbox.drain()

    assert snapshot == {"exchange_position": 5.0, "event": "FORCE_STATE_SYNC"}
    assert mailbox.drain() is None  # box is empty after the atomic take


def test_background_thread_no_direct_mutation():
    engine = OMSActorEngine()
    engine.local_position = 5.0

    # Background thread's only touchpoint is the mailbox -- never engine.local_position directly.
    engine.mailbox.put_state_sync({"exchange_position": 999.0})

    # Depositing into the mailbox must NOT immediately mutate live actor state -- it only
    # takes effect once the actor drains it at its own safe boundary.
    assert engine.local_position == 5.0


def test_safe_boundary_drain():
    engine = OMSActorEngine()
    engine.local_position = 1.0
    engine.enqueue_event(OMSEventTier.TIER_2_EXECUTION, "SNIPER_ENTRY", {"order_id": "o1", "side": "BUY", "price": 100.0, "qty": 1.0})

    engine.mailbox.put_state_sync({"exchange_position": 1.02})  # small 2% discrepancy -> DELTA_SYNC, not the breaker
    # Not yet drained -- no event cycle has completed since the deposit.
    assert engine.local_position == 1.0

    engine.run_event_step()  # processes SNIPER_ENTRY, THEN drains at the boundary after
    assert engine.local_position == pytest.approx(1.02)
    assert "DELTA_SYNC" in engine.processed_events


def test_safe_boundary_preserves_atomicity():
    engine = OMSActorEngine()
    engine.submit_order("ord1", "BUY", 100.0, 1.0)
    engine.enqueue_event(OMSEventTier.TIER_0_SAFETY, "TOXIC_FLOW", {"order_id": "ord1"})
    engine.local_position = 2.0

    # Background thread deposits a FORCE_STATE_SYNC snapshot mid-cycle (small 1% discrepancy).
    engine.mailbox.put_state_sync({"exchange_position": 2.02})

    step = engine.run_event_step()

    # The queued TOXIC_FLOW event completed fully and uninterrupted...
    assert step.event_type == "TOXIC_FLOW"
    assert engine.orders["ord1"]["status"] == OrderStatus.PENDING_CANCEL
    # ...and only THEN, at the safe boundary after, did the mailbox drain apply.
    assert engine.local_position == pytest.approx(2.02)


def test_delta_calculation_accuracy():
    engine = OMSActorEngine(max_discrepancy_pct=0.50)  # wide enough to isolate the delta math from the breaker
    engine.local_position = 400.0

    engine.mailbox.put_state_sync({"exchange_position": 500.0})  # +100 share discrepancy
    engine.run_event_step()

    assert engine.local_position == pytest.approx(500.0)
    assert "DELTA_SYNC" in engine.processed_events


def test_delta_sync_small_discrepancy():
    engine = OMSActorEngine(max_discrepancy_pct=0.10)
    engine.local_position = 100.0

    engine.mailbox.put_state_sync({"exchange_position": 105.0})  # 5% discrepancy < 10%
    engine.run_event_step()

    assert "DELTA_SYNC" in engine.processed_events
    assert not engine.circuit_breaker_tripped
    assert not engine.is_halted


def test_delta_sync_successful_reconciliation():
    engine = OMSActorEngine(max_discrepancy_pct=0.10)
    engine.local_position = 50.0

    engine.mailbox.put_state_sync({"exchange_position": 52.0})
    engine.run_event_step()

    assert engine.local_position == pytest.approx(52.0)  # local now matches exchange exactly


def test_circuit_breaker_severe_discrepancy():
    engine = OMSActorEngine(max_discrepancy_pct=0.10)
    engine.local_position = 100.0

    engine.mailbox.put_state_sync({"exchange_position": 115.0})  # 15% >= 10% threshold
    engine.run_event_step()

    assert engine.circuit_breaker_tripped is True
    assert engine.is_halted is True


def test_halt_circuit_breaker_stops_generation():
    engine = OMSActorEngine(max_discrepancy_pct=0.10)
    engine.submit_order("existing_ord", "BUY", 100.0, 1.0)
    engine.local_position = 100.0
    engine.mailbox.put_state_sync({"exchange_position": 150.0})  # 50% discrepancy -> trips breaker
    engine.run_event_step()  # empty queue -> hits safe boundary immediately -> halts
    assert engine.is_halted is True

    engine.enqueue_event(OMSEventTier.TIER_2_EXECUTION, "SNIPER_ENTRY", {"order_id": "blocked_ord", "side": "BUY", "price": 100.0, "qty": 1.0})
    engine.run_event_step()
    assert "blocked_ord" not in engine.orders  # new order generation refused while halted

    # Tier 0 safety (cancels) must still function during a halt -- HALT blocks new
    # generation, not the ability to defensively cancel existing exposure.
    engine.enqueue_event(OMSEventTier.TIER_0_SAFETY, "EMERGENCY_CANCEL", {"order_id": "existing_ord"})
    engine.run_event_step()
    assert engine.orders["existing_ord"]["status"] == OrderStatus.PENDING_CANCEL


def test_reconciler_api_latency_tolerance():
    engine = OMSActorEngine(max_discrepancy_pct=0.10)
    engine.submit_order("ord1", "BUY", 100.0, 1.0)

    # Background thread fetches the exchange snapshot (simulating a 500ms-old read: at that
    # moment, exchange position already reflected the fill about to be locally applied).
    engine.mailbox.put_state_sync({"exchange_position": 1.0})

    # Meanwhile, before the actor drains the mailbox, the exchange fill message itself
    # arrives and is applied locally first -- local_position catches up to the same 1.0.
    fill = ExchangeMessage(seq_num=1, order_id="ord1", msg_type="FILL", qty=1.0, price=100.0)
    engine.on_exchange_message(fill)
    assert engine.local_position == 1.0

    # Now the delayed reconciler snapshot drains -- despite the 500ms round-trip, it agrees
    # with reality and must NOT trip a false-positive circuit breaker.
    engine.enqueue_event(OMSEventTier.TIER_2_EXECUTION, "SNIPER_ENTRY", {"order_id": "noop", "side": "BUY", "price": 100.0, "qty": 0.0})
    engine.run_event_step()

    assert not engine.circuit_breaker_tripped
    assert not engine.is_halted
    assert engine.local_position == pytest.approx(1.0)
