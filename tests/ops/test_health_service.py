"""Tests for Health Check Service."""

import pytest

from trading.data.staleness import StalenessSentinel
from trading.execution.venue_adapter import MockVenueAdapter
from trading.ops.health_service import HealthService, HealthStatus


def test_health_service_healthy():
    venue = MockVenueAdapter()
    sentinel = StalenessSentinel()
    # Record tick to make sentinel fresh
    sentinel.record_tick("BTC", timestamp_ms=__import__("time").time() * 1000.0)

    service = HealthService(venue_adapter=venue, staleness_sentinel=sentinel)
    aggregate, components, resp = service.evaluate_system_health("BTC")

    assert aggregate == HealthStatus.HEALTHY
    assert resp["status"] == HealthStatus.HEALTHY
    assert len(components) == 4


def test_health_service_degraded_stale_data():
    service = HealthService()
    # Symbol "BTC" not in sentinel -> stale -> DEGRADED
    aggregate, components, resp = service.evaluate_system_health("BTC")

    assert aggregate == HealthStatus.DEGRADED
    assert resp["status"] == HealthStatus.DEGRADED
