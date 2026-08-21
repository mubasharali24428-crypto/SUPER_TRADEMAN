"""Tests for Shadow Mode Interceptor (The Ghost Protocol)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from trading.config import ExecutionMode
from trading.execution.oms import OrderManagementSystem
from trading.execution.shadow import L2OrderBookSnapshot, ShadowInterceptor
from trading.execution.state_machine import OrderState
from trading.execution.venue_adapter import MockVenueAdapter
from trading.observability.shadow_metrics import ShadowMetricsTracker
from trading.risk.models import ApprovedOrder, Side, _ISSUER


@pytest.mark.asyncio
async def test_shadow_mode_never_calls_venue_adapter():
    venue = MockVenueAdapter()
    shadow_interceptor = ShadowInterceptor()
    oms = OrderManagementSystem(
        venue_adapter=venue,
        shadow_interceptor=shadow_interceptor,
        execution_mode=ExecutionMode.SHADOW,
    )

    # L2 Order Book with 2 BTC liquidity
    book = L2OrderBookSnapshot(
        symbol="BTC",
        bids=[(49900.0, 5.0)],
        asks=[(50100.0, 1.0), (50200.0, 2.0)],
        timestamp_ms=100000.0,
    )

    order = ApprovedOrder(
        asset="BTC",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=50000.0,
        stop_price=48000.0,
        target_price=55000.0,
        position_size=1.5,  # 1.5 BTC
        risk_pct=0.01,
        issuer=_ISSUER,
    )

    cid = "shadow_cid_001"
    state = await oms.submit_order(order, cid, order_book=book)

    assert state == OrderState.FILLED
    # CRITICAL INVARIANT: 0 calls to venue adapter
    assert len(venue.orders) == 0

    assert len(shadow_interceptor.shadow_fills) == 1
    fill = shadow_interceptor.shadow_fills[0]
    assert fill.filled_qty == 1.5
    assert fill.shadow_fill_price > 50000.0


@pytest.mark.asyncio
async def test_shadow_mode_liquidity_deficit_flash_crash():
    shadow_interceptor = ShadowInterceptor()

    # Shallow L2 Order Book with only 0.5 BTC available, while order requests 2.0 BTC
    shallow_book = L2OrderBookSnapshot(
        symbol="BTC",
        bids=[],
        asks=[(50000.0, 0.5)],
        timestamp_ms=100000.0,
    )

    order = ApprovedOrder(
        asset="BTC",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=50000.0,
        stop_price=48000.0,
        target_price=55000.0,
        position_size=2.0,
        risk_pct=0.01,
        issuer=_ISSUER,
    )

    res = await shadow_interceptor.execute_shadow_fill(order, "cid_shallow", shallow_book)
    assert res.filled_qty == 0.5
    assert res.was_capped
    assert res.reason == "LIQUIDITY_DEFICIT_SHADOW_FILL"


def test_shadow_metrics_tracker():
    tracker = ShadowMetricsTracker()
    tracker.record_shadow_trade(signal_price=50000.0, shadow_fill_price=50100.0, latency_ms=15.0)
    tracker.record_shadow_trade(signal_price=50000.0, shadow_fill_price=50200.0, latency_ms=25.0)

    summary = tracker.get_summary()
    assert summary["total_shadow_trades"] == 2
    assert summary["avg_latency_ms"] == 20.0
    assert summary["max_slippage_pct"] > 0.003
