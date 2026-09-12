"""Health Check Service categorizing system state as HEALTHY, DEGRADED, CRITICAL, or UNKNOWN.

Truthfulness contract (audit lane SUB-10):
- ``check_database_health`` performs a REAL ``asyncpg`` ``SELECT 1`` against
  ``POSTGRES_URL`` and reports the measured round-trip latency. When no DSN is
  configured it reports ``UNKNOWN`` — never a fabricated HEALTHY/1.2ms ping.
- ``HealthService`` requires a caller-supplied :class:`VenueAdapter`.
  ``MockVenueAdapter`` is only auto-selected when the effective execution mode
  is simulation (see ``_effective_simulation_mode``).
- ``check_daemon_health`` verifies the heartbeat tier-state file's mtime is
  fresher than two daemon intervals. Unknown/unreadable paths yield ``UNKNOWN``.
"""

import asyncio
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from trading.config import ExecutionMode
from trading.data.staleness import StalenessSentinel
from trading.execution.venue_adapter import MockVenueAdapter, VenueAdapter
from trading.observability.logger import get_logger

__all__ = [
    "HealthStatus",
    "ComponentHealth",
    "HealthService",
]

logger = get_logger("trading.ops.health_service")

DB_PROBE_TIMEOUT_SEC = 3.0


class HealthStatus:
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class ComponentHealth:
    name: str
    status: str
    latency_ms: float = 0.0
    details: str = ""


def _effective_simulation_mode(execution_mode: Optional[ExecutionMode]) -> bool:
    """True when the configured mode represents simulation (no live venue).

    ``trading.config.ExecutionMode`` models BACKTEST < PAPER < SHADOW < LIVE_*;
    a dedicated ``SIMULATION`` member does not exist upstream, so BACKTEST is
    treated as the simulation mode. A ``SIMULATION`` attribute added later is
    honored automatically.
    """
    sim = getattr(ExecutionMode, "SIMULATION", ExecutionMode.BACKTEST)
    return execution_mode is sim


def _run_coro_in_fresh_loop(coro, timeout_sec: float):
    """Run an async coroutine to completion even if called from a sync context.

    Uses a short-lived thread with its own event loop so this remains callable
    while another loop is running in the current thread. Returns the coroutine
    result or re-raises the first exception.
    """
    box: Dict[str, Any] = {}

    def _target():
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - propagated to caller
            box["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout_sec)
    if thread.is_alive():
        raise TimeoutError(f"async probe did not finish within {timeout_sec}s")
    if "error" in box:
        raise box["error"]
    return box.get("result")


class HealthService:
    """Monitors system components and evaluates aggregate system health."""

    def __init__(
        self,
        venue_adapter: VenueAdapter,
        staleness_sentinel: Optional[StalenessSentinel] = None,
        execution_mode: Optional[ExecutionMode] = None,
        postgres_url: Optional[str] = None,
        heartbeat_state_path: Optional[str] = None,
        heartbeat_interval_seconds: float = 60.0,
    ):
        if venue_adapter is None:
            if _effective_simulation_mode(execution_mode):
                # Simulation-only convenience: a mock venue stands in for a
                # real one. Never applied outside simulation modes.
                venue_adapter = MockVenueAdapter()
            else:
                raise ValueError(
                    "venue_adapter is required: refusing to silently fall back to "
                    "MockVenueAdapter outside simulation mode (would fabricate "
                    "exchange health)."
                )
        self.venue_adapter = venue_adapter
        self.staleness_sentinel = staleness_sentinel or StalenessSentinel()
        self.postgres_url = postgres_url or os.getenv("POSTGRES_URL") or ""
        self.heartbeat_state_path = heartbeat_state_path
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)

    def check_database_health(self) -> ComponentHealth:
        """Real asyncpg SELECT 1 with measured latency, or UNKNOWN without a DSN."""
        dsn = self.postgres_url or os.getenv("POSTGRES_URL") or ""
        if not dsn:
            return ComponentHealth(
                name="database",
                status=HealthStatus.UNKNOWN,
                latency_ms=0.0,
                details="POSTGRES_URL not set; database liveness unknown (no synthetic ping).",
            )

        async def _probe():
            import asyncpg

            t0 = time.perf_counter()
            conn = await asyncpg.connect(dsn, timeout=int(DB_PROBE_TIMEOUT_SEC))
            try:
                row = await conn.fetchval("SELECT 1")
                assert row is not None
            finally:
                await conn.close()
            return (time.perf_counter() - t0) * 1000.0

        try:
            latency_ms = float(_run_coro_in_fresh_loop(_probe(), DB_PROBE_TIMEOUT_SEC))
        except Exception as exc:  # noqa: BLE001 - health must classify, not crash
            logger.warning("Database health probe failed: %s", exc)
            return ComponentHealth(
                name="database",
                status=HealthStatus.CRITICAL,
                latency_ms=0.0,
                details=f"Postgres SELECT 1 failed: {exc}",
            )
        return ComponentHealth(
            name="database",
            status=HealthStatus.HEALTHY,
            latency_ms=round(latency_ms, 3),
            details="Postgres SELECT 1 succeeded.",
        )

    def check_exchange_api_health(self) -> ComponentHealth:
        """Probe the injected venue adapter; simulation mocks report as such."""
        t0 = time.perf_counter()

        if isinstance(self.venue_adapter, MockVenueAdapter):
            lat_ms = (time.perf_counter() - t0) * 1000.0
            return ComponentHealth(
                name="exchange_api",
                status=HealthStatus.HEALTHY,
                latency_ms=round(lat_ms, 3),
                details="Simulation venue (MockVenueAdapter); no live API probed.",
            )

        async def _probe():
            # Read-only venue call: cheapest honest liveness signal available.
            await self.venue_adapter.fetch_open_orders(None)

        try:
            _run_coro_in_fresh_loop(_probe(), DB_PROBE_TIMEOUT_SEC)
            lat_ms = (time.perf_counter() - t0) * 1000.0
            return ComponentHealth(
                name="exchange_api",
                status=HealthStatus.HEALTHY,
                latency_ms=round(lat_ms, 3),
                details="Exchange venue API responsive.",
            )
        except Exception as exc:  # noqa: BLE001
            lat_ms = (time.perf_counter() - t0) * 1000.0
            return ComponentHealth(
                name="exchange_api",
                status=HealthStatus.CRITICAL,
                latency_ms=round(lat_ms, 3),
                details=f"Exchange venue probe failed: {exc}",
            )

    def check_websocket_health(self, symbol: str = "BTC") -> ComponentHealth:
        is_stale = self.staleness_sentinel.is_stale(symbol)
        if is_stale:
            return ComponentHealth(
                name="websocket_data",
                status=HealthStatus.DEGRADED,
                details=f"Data for {symbol} is stale.",
            )
        return ComponentHealth(
            name="websocket_data",
            status=HealthStatus.HEALTHY,
            details=f"Data stream for {symbol} fresh.",
        )

    def check_daemon_health(self) -> ComponentHealth:
        """Heartbeat freshness via tier-state file mtime (< 2 * interval), else UNKNOWN."""
        path = self.heartbeat_state_path or os.getenv("RISK_TIER_STATE_FILE") or ""
        if not path:
            return ComponentHealth(
                name="daemon_process",
                status=HealthStatus.UNKNOWN,
                details="No heartbeat state file path known; daemon liveness unknown.",
            )
        try:
            age_sec = time.time() - os.path.getmtime(path)
        except OSError:
            return ComponentHealth(
                name="daemon_process",
                status=HealthStatus.UNKNOWN,
                details=f"Heartbeat state file not found at {path}; daemon liveness unknown.",
            )
        max_age = 2.0 * self.heartbeat_interval_seconds
        if age_sec <= max_age:
            return ComponentHealth(
                name="daemon_process",
                status=HealthStatus.HEALTHY,
                latency_ms=round(age_sec * 1000.0, 1),
                details=f"Heartbeat {age_sec:.1f}s old (< {max_age:.1f}s budget).",
            )
        return ComponentHealth(
            name="daemon_process",
            status=HealthStatus.CRITICAL,
            latency_ms=round(age_sec * 1000.0, 1),
            details=f"Heartbeat stale: {age_sec:.1f}s old (>= {max_age:.1f}s budget).",
        )

    def evaluate_system_health(
        self, symbol: str = "BTC"
    ) -> Tuple[str, List[ComponentHealth], Dict[str, Any]]:
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
