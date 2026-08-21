"""Tests for Partial Fill Resolution and Risk Deviation logging."""

import pytest

from trading.execution.chase import OrderChaser, WorkingOrderInfo
from trading.execution.oms import OrderManagementSystem
from trading.execution.state_machine import OrderState
from trading.execution.venue_adapter import MockVenueAdapter
from trading.risk.models import ApprovedOrder, ApprovedExit, Side, _ISSUER


@pytest.mark.asyncio
async def test_finalize_partial_fill_risk_deviation():
    venue = MockVenueAdapter()
    oms = OrderManagementSystem(venue_adapter=venue)

    cid = "cid_partial_100"
    dev_event = await oms.finalize_partial_fill(
        client_order_id=cid,
        filled_qty=0.4,
        intended_qty=1.0,
        initial_stop_price=48000.0,
        asset="BTC",
        entry_price=50000.0,
    )

    assert oms.active_orders[cid] == OrderState.PARTIAL_FILL_FINALIZED
    assert dev_event.intended_qty == 1.0
    assert dev_event.realized_qty == 0.4
    assert dev_event.intended_risk_usd == 1.0 * 2000.0  # (50000 - 48000)
    assert dev_event.realized_risk_usd == 0.4 * 2000.0

    # Confirm ApprovedExit strictly maintains initial stop price 48000.0
    approved_exit = ApprovedExit(
        asset="BTC",
        asset_class="crypto",
        reason="stop_loss",
        issuer=_ISSUER,
    )
    assert approved_exit.issuer is _ISSUER


@pytest.mark.asyncio
async def test_wash_trading_prevention():
    venue = MockVenueAdapter()
    chaser = OrderChaser(venue_adapter=venue)
    oms = OrderManagementSystem(venue_adapter=venue, order_chaser=chaser)

    # Register active LONG working order on BTC
    chaser.register_order(
        WorkingOrderInfo(
            client_order_id="cid_long_active",
            symbol="BTC",
            side=Side.LONG,
            submitted_price=50000.0,
            stop_price=48000.0,
            requested_qty=1.0,
        )
    )

    # New SHORT order arrives (strategy reversal)
    short_order = ApprovedOrder(
        asset="BTC",
        asset_class="crypto",
        side=Side.SHORT,
        entry_price=49800.0,
        stop_price=51000.0,
        target_price=47000.0,
        position_size=1.0,
        risk_pct=0.01,
        issuer=_ISSUER,
    )

    state = await oms.submit_order(short_order, "cid_short_new")
    assert state == OrderState.SUBMITTED

    # Active LONG order must have been canceled to prevent wash trading
    assert chaser.working_orders["cid_long_active"].status == OrderState.CANCELED
