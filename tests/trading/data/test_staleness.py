"""Tests for StalenessSentinel and OMS pre-flight staleness protection."""

import pytest
import time
from unittest.mock import AsyncMock, MagicMock

from trading.data.staleness import StalenessSentinel
from trading.execution.oms import OrderManagementSystem
from trading.execution.state_machine import OrderState
from trading.execution.venue_adapter import MockVenueAdapter
from trading.risk.models import ApprovedOrder, Side, _ISSUER


def test_staleness_sentinel_fresh_data():
    sentinel = StalenessSentinel(default_max_staleness_ms=3000.0)
    now_ms = time.time() * 1000.0

    # Record a fresh tick at current timestamp
    sentinel.record_tick("BTC", timestamp_ms=now_ms, received_at_ms=now_ms)
    assert not sentinel.is_stale("BTC", current_time_ms=now_ms)

    stats = sentinel.get_latency_stats("BTC")
    assert stats is not None
    assert stats.tick_count == 1


def test_staleness_sentinel_trips_on_stale_data():
    sentinel = StalenessSentinel(default_max_staleness_ms=3000.0)
    t0_ms = 1000000.0

    sentinel.record_tick("BTC", timestamp_ms=t0_ms, received_at_ms=t0_ms)

    # 4 seconds later (4000ms > 3000ms threshold)
    t1_ms = t0_ms + 4000.0
    assert sentinel.is_stale("BTC", current_time_ms=t1_ms)


def test_out_of_order_replayed_ticks_ignored():
    sentinel = StalenessSentinel(default_max_staleness_ms=3000.0)
    t0_ms = 1000000.0

    # Fresh tick at t0 (timestamp 100,000)
    sentinel.record_tick("BTC", timestamp_ms=100_000.0, received_at_ms=t0_ms)
    assert sentinel.get_latency_stats("BTC").last_event_hwm_ms == 100_000.0

    # Trip the circuit breaker manually
    sentinel.trip_circuit_breaker("BTC")
    assert sentinel.is_stale("BTC", current_time_ms=t0_ms)

    # Replayed out-of-order tick with OLDER timestamp (90,000 < 100,000)
    sentinel.record_tick("BTC", timestamp_ms=90_000.0, received_at_ms=t0_ms + 1000.0)

    # Monotonic HWM must stay 100,000 and circuit breaker MUST REMAIN TRIPPED
    assert sentinel.get_latency_stats("BTC").last_event_hwm_ms == 100_000.0
    assert sentinel.is_stale("BTC", current_time_ms=t0_ms + 1000.0)


@pytest.mark.asyncio
async def test_oms_preflight_stale_data_rejection():
    venue = MockVenueAdapter()
    sentinel = StalenessSentinel(default_max_staleness_ms=3000.0)
    sentinel.trip_circuit_breaker("BTC")

    oms = OrderManagementSystem(venue_adapter=venue, staleness_sentinel=sentinel)

    order = ApprovedOrder(
        asset="BTC",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=50000.0,
        stop_price=48000.0,
        target_price=55000.0,
        position_size=1.0,
        risk_pct=0.01,
        issuer=_ISSUER,
    )

    state = await oms.submit_order(order, "cid_stale_1")
    assert state == OrderState.REJECTED
    assert len(venue.orders) == 0  # 0 calls to venue adapter
