"""Tests for Synthetic Limit Order Book (LOB) Matching Engine, Micro-Price, Event Log, and Friction."""

import pytest

from trading.synthetic.lob import (BookIntegrityViolation, LimitOrder,
                                   SyntheticLOB)


def test_lob_initialization_and_micro_price():
    lob = SyntheticLOB(symbol="BTC/USDT", initial_price=50000.0)
    best_b, best_a = lob.get_best_bid_ask()

    assert best_b < best_a
    assert lob.get_micro_price() > 0.0


def test_lob_matching_fills_and_event_log():
    # Set base_fill_probability=1.0 for deterministic fill
    lob = SyntheticLOB(
        symbol="BTC/USDT", initial_price=50000.0, base_fill_probability=1.0
    )
    best_b, best_a = lob.get_best_bid_ask()

    # Place aggressive buy order matching best ask
    buy_ord = LimitOrder("ord_test_buy", "buy", best_a + 5.0, 1.0, 1000.0, "agent_test")
    fills = lob.place_order(buy_ord)

    assert len(fills) > 0
    assert fills[0]["qty"] == 1.0
    assert fills[0]["price"] == best_a

    # Verify event_log recorded the trade event
    trade_events = [e for e in lob.event_log if e["event_type"] == "trade"]
    assert len(trade_events) > 0
    assert trade_events[-1]["price"] == best_a


def test_lob_cancel_order_and_event_log():
    lob = SyntheticLOB(symbol="BTC/USDT", initial_price=50000.0)
    # Cancel seed order 'b1'
    canceled = lob.cancel_order("b1")
    assert canceled

    # Verify cancel event in event_log
    cancel_events = [e for e in lob.event_log if e["event_type"] == "cancel"]
    assert len(cancel_events) == 1
    assert cancel_events[0]["side"] == "buy"
    assert cancel_events[0]["price"] == 49999.0


def test_lob_micro_price_depth_decay():
    lob = SyntheticLOB(symbol="BTC/USDT", initial_price=50000.0)
    mp = lob.get_micro_price()
    assert 49990.0 < mp < 50010.0


def test_lob_queue_priority_and_friction_penalty():
    # base_fill_probability=0.0 guarantees queue slip; SUB-07 semantics: a
    # slipped CROSSING order is rejected (BookIntegrityViolation), not rested.
    lob = SyntheticLOB(
        symbol="BTC/USDT", initial_price=50000.0, base_fill_probability=0.0
    )
    best_b, best_a = lob.get_best_bid_ask()

    buy_ord = LimitOrder("ord_slip", "buy", best_a + 5.0, 1.0, 1000.0, "agent_test")
    with pytest.raises(BookIntegrityViolation):
        lob.place_order(buy_ord, stress_score=0.80)

    # Order experienced queue slip: 0 fills, arrival unchanged, priority +75ms,
    # and the crossing residual was NOT rested onto the book.
    assert buy_ord.filled_qty == 0.0
    assert buy_ord.arrival_timestamp_ms == 1000.0
    assert buy_ord.priority_timestamp_ms == 1075.0
    assert all(b.order_id != "ord_slip" for b in lob.bids)
    assert lob.integrity_violation_count >= 1


def test_lob_passive_order_rests_after_friction_slip():
    # Non-crossing orders keep the legacy slip-then-rest behaviour.
    lob = SyntheticLOB(
        symbol="BTC/USDT", initial_price=50000.0, base_fill_probability=0.0
    )
    best_b, best_a = lob.get_best_bid_ask()

    passive = LimitOrder("ord_passive", "buy", best_a - 2.0, 1.0, 1000.0, "agent_test")
    fills = lob.place_order(passive)
    assert fills == []
    # No matching level reachable -> no friction roll; rests untouched at ask-2.
    assert any(
        b.order_id == "ord_passive" and b.priority_timestamp_ms == 1000.0
        for b in lob.bids
    )
    assert lob.integrity_violation_count == 0
