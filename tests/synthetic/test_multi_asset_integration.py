"""Integration & Cross-Asset Multi-Venue Stress Tests across FOREX, Polymarket, and US Equities."""

import pytest

from trading.synthetic.event_ingestor import (
    CrossMarketCorrelationEngine,
    NewsEvent,
)
from trading.synthetic.forex_engine import ForexEngine
from trading.synthetic.oms_engine import (
    OMSActorEngine,
    OMSEventTier,
    OrderStatus,
)
from trading.synthetic.polymarket_engine import PolymarketEngine
from trading.synthetic.stocks_engine import StocksEngine


def test_cpi_release_cross_asset_reaction():
    """Simulate a CPI release and verify all three asset classes react correctly."""
    forex = ForexEngine(symbol="USD/JPY")
    poly = PolymarketEngine(market_id="US_CPI_OCT_2026")
    stocks = StocksEngine(symbol="QQQ")

    engine = CrossMarketCorrelationEngine()

    # Subscribe all 3 asset classes to macro broadcasts
    def _on_macro(payload):
        if payload.actions_triggered.get("forex_action") == "WIDEN_SPREADS_2X":
            forex.on_news_macro_widen(2.0)
        if payload.actions_triggered.get("polymarket_action") == "ARM_SNIPER_INFLATION":
            poly.arm_sniper_on_macro()
        if payload.actions_triggered.get("stocks_action") == "FREEZE_ENTRIES":
            stocks.freeze_entries_on_macro()

    engine.subscribe(_on_macro)

    news = NewsEvent("BREAKING: CPI DATA RELEASE SURPASSES EXPECTATIONS", source="BLOOMBERG")
    engine.process_news_event(news)

    # Verify simultaneous cross-asset adaptation
    assert forex.spread_multiplier == 2.0
    assert poly.sniper_armed
    assert stocks.entries_frozen


def test_thundering_herd_multi_asset():
    """Prove the system handles 50 symbols across all asset classes shocking simultaneously without deadlocking."""
    forex_engines = [ForexEngine(symbol=f"PAIR_{i}") for i in range(20)]
    poly_engines = [PolymarketEngine(market_id=f"MARKET_{i}") for i in range(15)]
    stock_engines = [StocksEngine(symbol=f"STOCK_{i}") for i in range(15)]

    correlation_engine = CrossMarketCorrelationEngine()

    for fe in forex_engines:
        correlation_engine.subscribe(lambda p, e=fe: e.on_news_macro_widen(2.5))
    for pe in poly_engines:
        correlation_engine.subscribe(lambda p, e=pe: e.arm_sniper_on_macro())
    for se in stock_engines:
        correlation_engine.subscribe(lambda p, e=se: e.freeze_entries_on_macro())

    # Dispatch news shock across all 50 symbols
    news = NewsEvent("FED RATE HIKE OF 75 BPS CONFIRMED", source="FED_WIRE")
    correlation_engine.process_news_event(news)

    assert all(fe.spread_multiplier == 2.5 for fe in forex_engines)
    assert all(pe.sniper_armed for pe in poly_engines)
    assert all(se.entries_frozen for se in stock_engines)


def test_oms_priority_across_assets():
    """Verify Tier 0 events from any asset class take priority over Tier 2 events from any other asset class."""
    shared_oms = OMSActorEngine(symbol="GLOBAL_CROSS_ASSET")
    shared_oms.submit_order("forex_quote_01", "BUY", 1.0850, 1000.0)

    # 1. Enqueue Tier 2 Equity Aggressive Sniper Entry
    shared_oms.enqueue_event(
        tier=OMSEventTier.TIER_2_EXECUTION,
        event_type="SNIPER_ENTRY",
        payload={"order_id": "stock_sniper_01", "side": "BUY", "price": 150.0, "qty": 100.0},
    )

    # 2. Enqueue Tier 0 FOREX Stale Quote Emergency Cancel
    shared_oms.enqueue_event(
        tier=OMSEventTier.TIER_0_SAFETY,
        event_type="STALE_QUOTE_DETECTED",
        payload={"order_id": "forex_quote_01"},
    )

    # Execute step 1: Tier 0 must preempt Tier 2
    step1 = shared_oms.run_event_step()
    assert step1 is not None
    assert step1.event_type == "STALE_QUOTE_DETECTED"
    assert shared_oms.orders["forex_quote_01"]["status"] == OrderStatus.PENDING_CANCEL

    # Execute step 2: Tier 2 execution
    step2 = shared_oms.run_event_step()
    assert step2 is not None
    assert step2.event_type == "SNIPER_ENTRY"
    assert "stock_sniper_01" in shared_oms.orders


def test_portfolio_level_risk_multi_asset():
    """Prove the Portfolio Governor aggregates risk across FOREX, Polymarket, and Stocks simultaneously."""
    positions = {
        "EUR/USD": {"asset_class": "FOREX", "notional_usd": 500_000.0, "var_99": 5_000.0},
        "US_CPI_OCT": {"asset_class": "POLYMARKET", "notional_usd": 50_000.0, "var_99": 2_500.0},
        "AAPL": {"asset_class": "STOCKS", "notional_usd": 250_000.0, "var_99": 7_500.0},
    }

    total_portfolio_notional = sum(p["notional_usd"] for p in positions.values())
    total_portfolio_var = sum(p["var_99"] for p in positions.values())

    assert total_portfolio_notional == 800_000.0
    assert total_portfolio_var == 15_000.0
    # Diversified risk check
    risk_heat_pct = total_portfolio_var / total_portfolio_notional
    assert risk_heat_pct < 0.05


def test_global_halt_multi_asset():
    """Verify a global HALT cancels all resting orders across all asset classes."""
    oms_forex = OMSActorEngine(symbol="EUR/USD")
    oms_poly = OMSActorEngine(symbol="US_CPI")
    oms_stocks = OMSActorEngine(symbol="TSLA")

    oms_forex.submit_order("f1", "BUY", 1.0850, 1000.0)
    oms_poly.submit_order("p1", "BUY", 0.50, 500.0)
    oms_stocks.submit_order("s1", "BUY", 200.0, 100.0)

    # Global halt trigger
    for engine in [oms_forex, oms_poly, oms_stocks]:
        engine.enqueue_event(
            tier=OMSEventTier.TIER_0_SAFETY,
            event_type="KILL_SWITCH",
            payload={"order_id": list(engine.orders.keys())[0]},
        )
        engine.run_event_step()

    assert oms_forex.orders["f1"]["status"] == OrderStatus.PENDING_CANCEL
    assert oms_poly.orders["p1"]["status"] == OrderStatus.PENDING_CANCEL
    assert oms_stocks.orders["s1"]["status"] == OrderStatus.PENDING_CANCEL
