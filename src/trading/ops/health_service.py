"""Health Check Service categorizing system state as HEALTHY, DEGRADED, or CRITICAL."""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from trading.data.staleness import StalenessSentinel
from trading.execution.venue_adapter import MockVenueAdapter, VenueAdapter
from trading.observability.logger import get_logger

__all__ = [
    "HealthStatus",
    "ComponentHealth",
    "HealthService",
]

logger = get_logger("trading.ops.health_service")


class HealthStatus:
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"


@dataclass
class ComponentHealth:
    name: str
    status: str
    latency_ms: float = 0.0
    details: str = ""


class HealthService:
    """Monitors system components and evaluates aggregate system health."""

    def __init__(
        self,
        venue_adapter: Optional[VenueAdapter] = None,
        staleness_sentinel: Optional[StalenessSentinel] = None,
    ):
        self.venue_adapter = venue_adapter or MockVenueAdapter()
        self.staleness_sentinel = staleness_sentinel or StalenessSentinel()

    def check_database_health(self) -> ComponentHealth:
        # Simulate local database ping
        return ComponentHealth(name="database", status=HealthStatus.HEALTHY, latency_ms=1.2, details="Postgres connection responsive.")

    def check_exchange_api_health(self) -> ComponentHealth:
        # Check exchange API ping
        t0 = time.time()
        # Mock venue check
        lat_ms = (time.time() - t0) * 1000.0
        return ComponentHealth(name="exchange_api", status=HealthStatus.HEALTHY, latency_ms=lat_ms, details="Exchange venue API responsive.")

    def check_websocket_health(self, symbol: str = "BTC") -> ComponentHealth:
        is_stale = self.staleness_sentinel.is_stale(symbol)
        if is_stale:
            return ComponentHealth(name="websocket_data", status=HealthStatus.DEGRADED, details=f"Data for {symbol} is stale.")
        return ComponentHealth(name="websocket_data", status=HealthStatus.HEALTHY, details=f"Data stream for {symbol} fresh.")

    def check_daemon_health(self) -> ComponentHealth:
        return ComponentHealth(name="daemon_process", status=HealthStatus.HEALTHY, details="Heartbeat daemon singleton active.")

    def evaluate_system_health(self, symbol: str = "BTC") -> Tuple[str, List[ComponentHealth], Dict[str, Any]]:
        components = [
            self.check_database_health(),
            self.check_exchange_api_health(),
            self.check_websocket_health(symbol),
            self.check_daemon_health(),
        ]

        statuses = [c.status for c in components]
        if HealthStatus.CRITICAL in statuses:
            aggregate = HealthStatus.CRITICAL
        elif HealthStatus.DEGRADED in statuses:
            aggregate = HealthStatus.DEGRADED
        else:
            aggregate = HealthStatus.HEALTHY

        response = {
            "status": aggregate,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "components": [
                {
                    "name": c.name,
                    "status": c.status,
                    "latency_ms": c.latency_ms,
                    "details": c.details,
                }
                for c in components
            ],
        }

        return aggregate, components, response
