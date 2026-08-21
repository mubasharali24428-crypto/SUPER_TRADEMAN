"""Asynchronous News & Macro Event Ingestion, Keyword Classification, and Cross-Market Broadcasting."""

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from trading.observability.logger import get_logger

__all__ = [
    "NewsEvent",
    "MacroBroadcastPayload",
    "StructuredNewsListener",
    "EventLatencyTracker",
    "CrossMarketCorrelationEngine",
]

logger = get_logger("trading.synthetic.event_ingestor")


@dataclass
class NewsEvent:
    headline: str
    source: str
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000.0)
    symbol: Optional[str] = None


@dataclass
class MacroBroadcastPayload:
    event_type: str  # "MACRO_EVENT_DETECTED"
    keyword: str
    impact_level: str  # "HIGH", "MEDIUM", "LOW"
    actions_triggered: Dict[str, Any]
    detection_ts_ms: float
    broadcast_ts_ms: float = field(default_factory=lambda: time.time() * 1000.0)


class EventLatencyTracker:
    """Measures latency between news event detection and market reaction execution."""

    def __init__(self):
        self.latency_records: List[Dict[str, float]] = []

    def record_latency(self, detection_ts_ms: float, reaction_ts_ms: float, event_name: str) -> float:
        latency_ms = max(0.0, reaction_ts_ms - detection_ts_ms)
        self.latency_records.append({
            "event_name": event_name,
            "latency_ms": latency_ms,
        })
        logger.info(f"[EVENT_LATENCY_TRACKED] Event '{event_name}' executed in {latency_ms:.2f}ms.")
        return latency_ms


class StructuredNewsListener:
    """Simulates listening to real-time financial news stream with graceful degradation on disconnect."""

    def __init__(self):
        self.is_connected: bool = True
        self.ingested_events: List[NewsEvent] = []

    def disconnect(self) -> None:
        self.is_connected = False
        logger.warning("[NEWS_FEED_DISCONNECTED] News WebSocket disconnected. Operating in degraded fallback mode.")

    def reconnect(self) -> None:
        self.is_connected = True
        logger.info("[NEWS_FEED_RECONNECTED] News WebSocket restored.")

    def ingest_news(self, headline: str, source: str = "BLOOMBERG", symbol: Optional[str] = None) -> Optional[NewsEvent]:
        if not self.is_connected:
            logger.debug(f"[NEWS_IGNORED_DISCONNECTED] Feed is disconnected; skipping '{headline}'.")
            return None

        event = NewsEvent(headline=headline, source=source, symbol=symbol)
        self.ingested_events.append(event)
        logger.info(f"[NEWS_INGESTED] '{headline}' from {source}.")
        return event


class CrossMarketCorrelationEngine:
    """Analyzes ingested news keywords and broadcasts MACRO_EVENT_DETECTED across all asset classes."""

    def __init__(self):
        self.subscribers: List[Callable[[MacroBroadcastPayload], None]] = []
        self.latency_tracker = EventLatencyTracker()
        self.news_listener = StructuredNewsListener()

    def subscribe(self, callback: Callable[[MacroBroadcastPayload], None]) -> None:
        self.subscribers.append(callback)

    def process_news_event(self, event: NewsEvent) -> Optional[MacroBroadcastPayload]:
        """Classifies headline and immediately dispatches broadcast to all asset engines."""
        headline = event.headline.upper()
        keyword = "UNKNOWN"
        impact = "LOW"
        actions = {}

        if "FED RATE HIKE" in headline or "CPI DATA RELEASE" in headline or "INFLATION SURGE" in headline:
            keyword = "CPI_OR_FED_RATE" if "FED" in headline else "CPI_RELEASE"
            impact = "HIGH"
            actions = {
                "forex_action": "WIDEN_SPREADS_2X",
                "polymarket_action": "ARM_SNIPER_INFLATION",
                "stocks_action": "FREEZE_ENTRIES",
            }
        elif "EARNINGS BEAT" in headline:
            keyword = "EARNINGS_BEAT"
            impact = "MEDIUM"
            actions = {
                "stocks_action": "ARM_SNIPER_SYMBOL",
                "target_symbol": event.symbol or "AAPL",
            }
        else:
            return None

        detection_ts = event.timestamp_ms
        payload = MacroBroadcastPayload(
            event_type="MACRO_EVENT_DETECTED",
            keyword=keyword,
            impact_level=impact,
            actions_triggered=actions,
            detection_ts_ms=detection_ts,
            broadcast_ts_ms=time.time() * 1000.0,
        )

        # Sub-millisecond broadcast dispatch to all asset engines
        start_broadcast = time.time() * 1000.0
        for sub in self.subscribers:
            sub(payload)
        end_broadcast = time.time() * 1000.0

        self.latency_tracker.record_latency(start_broadcast, end_broadcast, keyword)
        return payload
