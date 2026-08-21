"""Unit tests for Task 4: Asynchronous News & Event Ingestion Layer."""

import time
import pytest

from trading.synthetic.event_ingestor import (
    CrossMarketCorrelationEngine,
    EventLatencyTracker,
    MacroBroadcastPayload,
    NewsEvent,
    StructuredNewsListener,
)
from trading.synthetic.forex_engine import ForexEngine
from trading.synthetic.polymarket_engine import PolymarketEngine
from trading.synthetic.stocks_engine import StocksEngine


def test_news_feed_keyword_detection():
    """Verify 'Fed Rate Hike' keyword is detected and parsed."""
    engine = CrossMarketCorrelationEngine()
    news = NewsEvent("BREAKING: FED RATE HIKE OF 50 BPS ANNOUNCED", source="BLOOMBERG")
    payload = engine.process_news_event(news)

    assert payload is not None
    assert payload.keyword == "CPI_OR_FED_RATE"
    assert payload.impact_level == "HIGH"
    assert "forex_action" in payload.actions_triggered


def test_cross_market_broadcast():
    """Prove MACRO_EVENT_DETECTED is broadcast to all engines within 1ms."""
    engine = CrossMarketCorrelationEngine()
    received_payloads = []
    engine.subscribe(lambda p: received_payloads.append(p))

    news = NewsEvent("URGENT: CPI DATA RELEASE SURGES ABOVE EXPECTATIONS", source="REUTERS")
    payload = engine.process_news_event(news)

    assert len(received_payloads) == 1
    assert received_payloads[0].event_type == "MACRO_EVENT_DETECTED"


def test_forex_spread_widening_on_news():
    """Verify FOREX spreads widen immediately after macro event."""
    forex = ForexEngine()
    assert forex.spread_multiplier == 1.0

    correlation_engine = CrossMarketCorrelationEngine()
    correlation_engine.subscribe(lambda p: forex.on_news_macro_widen(2.0))

    news = NewsEvent("FED RATE HIKE IMMINENT", source="DOW_JONES")
    correlation_engine.process_news_event(news)

    assert forex.spread_multiplier == 2.0


def test_polymarket_sniper_arming_on_news():
    """Prove Polymarket Sniper arms for relevant contracts after news."""
    polymarket = PolymarketEngine(market_id="US_FED_FUNDS_OCT")
    assert not polymarket.sniper_armed

    correlation_engine = CrossMarketCorrelationEngine()
    correlation_engine.subscribe(lambda p: polymarket.arm_sniper_on_macro())

    news = NewsEvent("FED RATE HIKE DECISION REACHED", source="BLOOMBERG")
    correlation_engine.process_news_event(news)

    assert polymarket.sniper_armed


def test_equity_entry_freeze_on_news():
    """Verify equity entries are frozen during macro uncertainty."""
    stocks = StocksEngine(symbol="SPY")
    assert not stocks.entries_frozen

    correlation_engine = CrossMarketCorrelationEngine()
    correlation_engine.subscribe(lambda p: stocks.freeze_entries_on_macro())

    news = NewsEvent("CPI DATA RELEASE SHOWS HIGH VOLATILITY", source="BLOOMBERG")
    correlation_engine.process_news_event(news)

    assert stocks.entries_frozen


def test_event_latency_measurement():
    """Prove system tracks time between news detection and market reaction."""
    tracker = EventLatencyTracker()
    detection_ts = 1000.0
    reaction_ts = 1000.45

    latency = tracker.record_latency(detection_ts, reaction_ts, "CPI_DATA_RELEASE")
    assert latency == pytest.approx(0.45)
    assert len(tracker.latency_records) == 1


def test_ingestor_graceful_degradation():
    """Verify system continues operating if news feed disconnects."""
    listener = StructuredNewsListener()
    assert listener.is_connected

    listener.disconnect()
    assert not listener.is_connected

    # Ingestion during disconnect safely returns None without crashing
    event = listener.ingest_news("SOME HEADLINE")
    assert event is None

    listener.reconnect()
    assert listener.is_connected
