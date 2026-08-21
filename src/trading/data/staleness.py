"""Data Staleness Sentinel & WebSocket Health Monitoring Module.

SOCRATIC GUARDRAIL ANSWER 1:
If the WebSocket reconnects and replays missed messages out-of-order, the StalenessSentinel
maintains a monotonic high-water mark timestamp (last_event_hwm_ms) per symbol. Incoming ticks
with timestamp_ms < last_event_hwm_ms are identified as historical backfill/replayed messages.
They are logged for latency statistics but strictly ignored when determining market fresh-tick
status, preventing out-of-order replayed data from clearing a tripped circuit breaker.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = [
    "TickUpdate",
    "SymbolLatencyStats",
    "StalenessSentinel",
]


@dataclass(frozen=True)
class TickUpdate:
    symbol: str
    timestamp_ms: float
    received_at_ms: float


@dataclass
class SymbolLatencyStats:
    symbol: str
    last_event_hwm_ms: float = 0.0
    last_received_at_ms: float = 0.0
    latencies_ms: List[float] = field(default_factory=list)
    circuit_tripped: bool = False
    tick_count: int = 0

    @property
    def max_latency_ms(self) -> float:
        return max(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0


class StalenessSentinel:
    """Monitors live data stream freshness per symbol and enforces circuit breaker protection."""

    def __init__(self, default_max_staleness_ms: float = 3000.0, max_latency_history: int = 100):
        self.default_max_staleness_ms = default_max_staleness_ms
        self.max_latency_history = max_latency_history
        self.stats: Dict[str, SymbolLatencyStats] = {}

    def record_tick(
        self,
        symbol: str,
        timestamp_ms: float,
        received_at_ms: Optional[float] = None,
    ) -> None:
        """Records a new market tick for symbol.

        Maintains monotonic high-water mark timestamp to ignore out-of-order replayed ticks.
        """
        now_ms = received_at_ms if received_at_ms is not None else (time.time() * 1000.0)
        if symbol not in self.stats:
            self.stats[symbol] = SymbolLatencyStats(symbol=symbol)

        stat = self.stats[symbol]
        stat.tick_count += 1

        # Calculate network latency for this tick
        latency = max(0.0, now_ms - timestamp_ms)
        stat.latencies_ms.append(latency)
        if len(stat.latencies_ms) > self.max_latency_history:
            stat.latencies_ms.pop(0)

        # Monotonic high-water mark check
        if timestamp_ms > stat.last_event_hwm_ms:
            stat.last_event_hwm_ms = timestamp_ms
            stat.last_received_at_ms = now_ms
            # Fresh tick received; reset circuit breaker if previously tripped
            stat.circuit_tripped = False
        else:
            # Out-of-order or replayed tick: log latency but do not update high-water mark
            pass

    def time_since_last_tick_ms(self, symbol: str, current_time_ms: Optional[float] = None) -> float:
        """Returns time elapsed in milliseconds since the last monotonic tick."""
        if symbol not in self.stats or self.stats[symbol].last_received_at_ms == 0.0:
            return float("inf")

        now_ms = current_time_ms if current_time_ms is not None else (time.time() * 1000.0)
        return max(0.0, now_ms - self.stats[symbol].last_received_at_ms)

    def is_stale(
        self,
        symbol: str,
        max_allowed_staleness_ms: Optional[float] = None,
        current_time_ms: Optional[float] = None,
    ) -> bool:
        """Checks if data for symbol is stale or circuit breaker is tripped."""
        threshold = (
            max_allowed_staleness_ms
            if max_allowed_staleness_ms is not None
            else self.default_max_staleness_ms
        )

        if symbol not in self.stats:
            return True  # No data received yet -> stale

        stat = self.stats[symbol]
        if stat.circuit_tripped:
            return True

        elapsed = self.time_since_last_tick_ms(symbol, current_time_ms=current_time_ms)
        if elapsed > threshold:
            stat.circuit_tripped = True
            return True

        return False

    def trip_circuit_breaker(self, symbol: str) -> None:
        """Manually trips circuit breaker for symbol."""
        if symbol not in self.stats:
            self.stats[symbol] = SymbolLatencyStats(symbol=symbol)
        self.stats[symbol].circuit_tripped = True

    def reset_circuit_breaker(self, symbol: str) -> None:
        """Manually resets circuit breaker for symbol."""
        if symbol in self.stats:
            self.stats[symbol].circuit_tripped = False

    def get_latency_stats(self, symbol: str) -> Optional[SymbolLatencyStats]:
        return self.stats.get(symbol)
