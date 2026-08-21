"""Unit tests for Task 3: US Equities Regulatory & Fragmentation Shield."""

import pytest

from trading.synthetic.stocks_engine import (
    DarkPoolRouter,
    DirectFeedQuote,
    LULDStateMachine,
    RegNMSTradeThroughGuard,
    ShortSaleRestrictionTracker,
    SIPDirectFeedReconciler,
    SIPNBBO,
    StocksEngine,
)


def test_sip_direct_feed_discrepancy_detection():
    """Prove system flags when direct feed shows price violating SIP NBBO."""
    reconciler = SIPDirectFeedReconciler(symbol="AAPL")
    reconciler.update_sip(SIPNBBO("AAPL", bid_price=150.00, ask_price=150.05))

    # Direct feed has ask lower than SIP bid (crossed venue)
    crossed_direct = DirectFeedQuote("AAPL", "DIRECT_EDGX", bid_price=149.95, ask_price=149.98)
    is_discrepancy = reconciler.update_direct(crossed_direct)

    assert is_discrepancy
    assert len(reconciler.discrepancies_flagged) == 1


def test_dual_speed_ewma_regime_annealing():
    """Prove that a persistent shift in infrastructure latency triggers Dual-Speed regime annealing."""
    reconciler = SIPDirectFeedReconciler(symbol="AAPL", lambda_fast=0.30, lambda_slow=0.01, regime_shift_ticks=5)
    
    # 1. Warm up baseline at 1.0ms latency
    for i in range(20):
        t_sip = 1000.0 + i * 10.0
        reconciler.update_sip(SIPNBBO("AAPL", bid_price=150.00, ask_price=150.05, timestamp_ms=t_sip))
        reconciler.evaluate_feed_anomaly(DirectFeedQuote("AAPL", "DIRECT_NASDAQ", 150.00, 150.05, timestamp_ms=t_sip + 1.0))

    # 2. Sudden permanent jump to 50ms latency (infrastructure route shift)
    for i in range(20):
        t_sip = 2000.0 + i * 10.0
        reconciler.update_sip(SIPNBBO("AAPL", bid_price=150.00, ask_price=150.05, timestamp_ms=t_sip))
        reconciler.evaluate_feed_anomaly(DirectFeedQuote("AAPL", "DIRECT_NASDAQ", 150.00, 150.05, timestamp_ms=t_sip + 50.0))

    assert reconciler.regime_annealing_count > 0


def test_asymmetric_side_specific_defensive_pull():
    """Prove that a bid dislocation only triggers PULL_BIDS, protecting bids while preserving asks."""
    reconciler = SIPDirectFeedReconciler(symbol="AAPL", z_threshold=2.0)
    
    # Warm up baseline at 1.0ms
    for i in range(10):
        t = 1000.0 + i * 10.0
        reconciler.update_sip(SIPNBBO("AAPL", bid_price=150.00, ask_price=150.05, timestamp_ms=t))
        reconciler.evaluate_feed_anomaly(DirectFeedQuote("AAPL", "DIRECT_ARCA", 150.00, 150.05, timestamp_ms=t + 1.0))

    # Direct Bid is 150.15 (crossing SIP Ask 150.05 by 2x spread), Ask is normal at 150.20
    # Lag jumps to 20ms (elevated latency Z > 2.0)
    reconciler.update_sip(SIPNBBO("AAPL", bid_price=150.00, ask_price=150.05, timestamp_ms=2000.0))
    direct_bid_dislocated = DirectFeedQuote("AAPL", "DIRECT_ARCA", bid_price=150.15, ask_price=150.20, timestamp_ms=2020.0)
    state, action, z = reconciler.evaluate_feed_anomaly(direct_bid_dislocated)

    assert state == "STALE_SIP_DISLOCATION"
    assert action == "PULL_BIDS"


def test_sniper_iso_freshness_and_depth_gating():
    """Prove Sniper ISO aborts on stale direct quotes (>50us) or odd-lot depth (<100 shares)."""
    engine = StocksEngine(symbol="MSFT")
    reconciler = engine.sip_reconciler
    reconciler.update_sip(SIPNBBO("MSFT", bid_price=300.00, ask_price=300.05, timestamp_ms=1000.0))

    # 1. Fresh quote (20us old) with 500 shares depth -> ALLOWED
    q_fresh = DirectFeedQuote("MSFT", "DIRECT_BATS", bid_price=300.00, ask_price=300.04, ask_qty=500.0, timestamp_ms=1000.00)
    reconciler.direct_quotes["DIRECT_BATS"] = q_fresh

    ok, reason = engine.evaluate_sniper_iso("DIRECT_BATS", "BUY", 300.04, 500.0, current_time_ms=1000.02)
    assert ok
    assert reason == "DISPATCH_ISO_IOC"

    # 2. Stale quote (100us old > 50us) -> ABORTED_STALE_QUOTE
    ok_stale, reason_stale = engine.evaluate_sniper_iso("DIRECT_BATS", "BUY", 300.04, 500.0, current_time_ms=1000.10)
    assert not ok_stale
    assert reason_stale == "ABORTED_STALE_QUOTE"

    # 3. Odd-lot quote (40 shares < 100 min) -> ABORTED_INSUFFICIENT_DEPTH
    q_oddlot = DirectFeedQuote("MSFT", "DIRECT_BATS", bid_price=300.00, ask_price=300.04, ask_qty=40.0, timestamp_ms=1000.00)
    reconciler.direct_quotes["DIRECT_BATS"] = q_oddlot
    ok_depth, reason_depth = engine.evaluate_sniper_iso("DIRECT_BATS", "BUY", 300.04, 40.0, current_time_ms=1000.01)
    assert not ok_depth
    assert reason_depth == "ABORTED_INSUFFICIENT_DEPTH"


def test_reg_nms_trade_through_prevention():
    """Verify order is canceled if priced below NBBO without non-displayed allowance."""
    guard = RegNMSTradeThroughGuard()
    nbbo = SIPNBBO("AAPL", bid_price=150.00, ask_price=150.05)

    # Buy order at 150.02 (below protected ask 150.05)
    allowed, action = guard.evaluate_order("BUY", price=150.02, nbbo=nbbo, allow_non_displayed=False)
    assert not allowed
    assert action == "TRADE_THROUGH_VIOLATION_CANCELLED"


def test_reg_nms_non_displayed_routing():
    """Prove order is routed as non-displayed when below NBBO but allowed."""
    guard = RegNMSTradeThroughGuard()
    nbbo = SIPNBBO("AAPL", bid_price=150.00, ask_price=150.05)

    allowed, action = guard.evaluate_order("BUY", price=150.02, nbbo=nbbo, allow_non_displayed=True)
    assert allowed
    assert action == "ROUTED_NON_DISPLAYED"


def test_luld_proximity_quote_pull():
    """Verify all quotes are pulled when stock is within 0.5% of lower LULD band."""
    luld = LULDStateMachine(reference_price=100.0, band_pct=0.05, proximity_threshold=0.005)
    # Lower band = 95.00. 0.5% proximity = 95.50
    pull_quotes, is_halted = luld.update_price(95.40)
    assert pull_quotes
    assert not is_halted  # Not halted yet, but quotes pulled proactively


def test_luld_halted_state_transition():
    """Prove state transitions to HALTED when LULD band is breached."""
    luld = LULDStateMachine(reference_price=100.0, band_pct=0.05)
    # Breaches lower band 95.00
    pull_quotes, is_halted = luld.update_price(94.80)
    assert pull_quotes
    assert is_halted
    assert luld.is_halted


def test_short_sale_restriction_uptick_rule():
    """Verify short sells are blocked when Alternative Uptick Rule (Rule 201) is active."""
    ssr = ShortSaleRestrictionTracker(previous_close=100.0)

    # Stock drops 11% to 89.00 -> SSR triggers
    assert ssr.update_price(89.00)

    # Short order priced at best bid 89.00 -> BLOCKED
    allowed, reason = ssr.validate_short_order(order_price=89.00, best_bid=89.00)
    assert not allowed
    assert reason == "BLOCKED_RULE_201"

    # Short order priced above best bid 89.05 -> ALLOWED
    allowed_uptick, reason_uptick = ssr.validate_short_order(order_price=89.05, best_bid=89.00)
    assert allowed_uptick
    assert reason_uptick == "ALLOWED_UPTICK"


def test_stocks_dark_pool_routing():
    """Prove large orders are routed to dark pools to minimize market impact."""
    router = DarkPoolRouter(dark_pool_threshold=10000.0)

    small_order = router.route_order("AAPL", "BUY", qty=500.0, limit_price=150.0)
    assert not small_order["is_dark_pool"]
    assert small_order["destination"] == "LIT_EXCHANGE_NASDAQ"

    large_order = router.route_order("AAPL", "BUY", qty=25000.0, limit_price=150.0)
    assert large_order["is_dark_pool"]
    assert large_order["destination"] == "DARK_POOL_ATS"
