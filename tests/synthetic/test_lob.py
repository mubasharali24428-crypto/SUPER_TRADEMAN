"""Tests for Synthetic Limit Order Book (LOB) Matching Engine, Micro-Price, Event Log, and Friction."""

import pytest

from trading.synthetic.lob import LimitOrder, SyntheticLOB


def test_lob_initialization_and_micro_price():
    lob = SyntheticLOB(symbol="BTC/USDT", initial_price=50000.0)
    best_b, best_a = lob.get_best_bid_ask()

    assert best_b < best_a
    assert lob.get_micro_price() > 0.0


def test_lob_matching_fills_and_event_log():
    # Set base_fill_probability=1.0 for deterministic fill
    lob = SyntheticLOB(symbol="BTC/USDT", initial_price=50000.0, base_fill_probability=1.0)
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
    # Set base_fill_probability=0.0 to guarantee queue slip friction trigger
    lob = SyntheticLOB(symbol="BTC/USDT", initial_price=50000.0, base_fill_probability=0.0)
    best_b, best_a = lob.get_best_bid_ask()

    buy_ord = LimitOrder("ord_slip", "buy", best_a + 5.0, 1.0, 1000.0, "agent_test")
    fills = lob.place_order(buy_ord, stress_score=0.80)

    # Order should experience queue slip: 0 fills, arrival_timestamp unchanged (1000.0), priority penalized (+75ms)
    assert len(fills) == 0
    assert buy_ord.arrival_timestamp_ms == 1000.0
    assert buy_ord.priority_timestamp_ms == 1075.0
    assert any(b.order_id == "ord_slip" for b in lob.bids)
