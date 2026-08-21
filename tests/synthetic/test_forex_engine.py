"""Unit tests for Task 1: FOREX Multi-Venue Aggregation, Last Look Defense, and Smart Order Routing."""

import pytest

from trading.synthetic.forex_engine import (
    ForexEngine,
    ForexSOR,
    LastLookFilter,
    LPQuote,
    LPReputationTracker,
    VirtualConsolidatedBook,
)


def test_virtual_consolidated_book_nbbo():
    """Prove the synthetic NBBO correctly aggregates across 5 LPs."""
    book = VirtualConsolidatedBook(symbol="EUR/USD", pip_value=0.0001)

    quotes = [
        LPQuote("LP1", "EUR/USD", 1.0850, 1.0, 1.0853, 1.0),
        LPQuote("LP2", "EUR/USD", 1.0851, 2.0, 1.0854, 2.0),
        LPQuote("LP3", "EUR/USD", 1.0852, 1.5, 1.0855, 1.5),  # Best Bid: 1.0852 (LP3)
        LPQuote("LP4", "EUR/USD", 1.0849, 3.0, 1.0852, 2.5),  # Best Ask: 1.0852 (LP4)
        LPQuote("LP5", "EUR/USD", 1.0850, 2.0, 1.0853, 2.0),
    ]

    for q in quotes:
        book.update_lp_quote(q)

    nbbo = book.get_consolidated_nbbo()
    assert nbbo.has_liquidity
    assert nbbo.best_bid == 1.0852
    assert nbbo.best_bid_lp == "LP3"
    assert nbbo.best_ask == 1.0852
    assert nbbo.best_ask_lp == "LP4"
    assert nbbo.spread_pips == 0.0  # 1.0852 - 1.0852 = 0 pips
    assert nbbo.total_depth_bid == 9.5
    assert nbbo.total_depth_ask == 9.0


def test_last_look_self_cancel():
    """Verify an order is self-canceled when micro-price drifts > 2 pips during simulated 50ms latency."""
    last_look = LastLookFilter(pip_value=0.0001, max_rejection_prob=0.70)

    # Initial order at 1.0850, micro-price drifts up to 1.0853 (3.0 pips adverse drift for BUY against LP)
    should_send, prob = last_look.evaluate_order(initial_price=1.0850, current_micro_price=1.0853, side="BUY")
    assert not should_send
    assert prob > 0.70


def test_last_look_acceptance():
    """Prove an order is sent when micro-price is stable."""
    last_look = LastLookFilter(pip_value=0.0001, max_rejection_prob=0.70)

    # Stable price (0.2 pips drift)
    should_send, prob = last_look.evaluate_order(initial_price=1.0850, current_micro_price=1.08502, side="BUY")
    assert should_send
    assert prob < 0.20


def test_sor_twap_splitting():
    """Verify a 10M EUR order is split across 3 LPs in equal time-weighted slices."""
    book = VirtualConsolidatedBook(symbol="EUR/USD")
    tracker = LPReputationTracker(initial_lps=["LP_A", "LP_B", "LP_C"])
    sor = ForexSOR(book, tracker)

    slices = sor.split_twap_order(total_qty=10_000_000.0, num_slices=4, side="BUY")
    assert len(slices) == 12  # 4 slices * 3 LPs

    # Check total sum across all slices equals 10M
    total_routed = sum(s["qty"] for s in slices)
    assert total_routed == pytest.approx(10_000_000.0, rel=1e-3)


def test_lp_reputation_routing():
    """Prove routing weights adjust dynamically based on LP rejection rates."""
    tracker = LPReputationTracker(initial_lps=["LP_GOOD", "LP_BAD"])
    # LP_GOOD has 0 rejections, LP_BAD has 50% rejections
    for _ in range(50):
        tracker.record_outcome("LP_GOOD", rejected=False)
        tracker.record_outcome("LP_BAD", rejected=True)

    weights = tracker.get_routing_weights()
    assert weights["LP_GOOD"] > weights["LP_BAD"]


def test_forex_zero_liquidity_edge_case():
    """Verify graceful handling when all LPs show zero depth."""
    book = VirtualConsolidatedBook(symbol="EUR/USD")
    book.update_lp_quote(LPQuote("LP1", "EUR/USD", 0.0, 0.0, 0.0, 0.0))

    nbbo = book.get_consolidated_nbbo()
    assert not nbbo.has_liquidity
    assert nbbo.best_bid == 0.0
    assert nbbo.best_ask == 0.0
