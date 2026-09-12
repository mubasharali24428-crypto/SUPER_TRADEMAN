"""Tests for Event-Sourced OMS, Outbox, and Reconciler."""

from datetime import datetime, timezone

import pytest

from trading.execution.oms import OrderManagementSystem
from trading.execution.outbox import OrderIntent, generate_client_order_id
from trading.execution.reconciler import QuarantineReason, StateReconciler
from trading.execution.state_machine import (OrderEventType, OrderState,
                                             transition_order_state)
from trading.execution.venue_adapter import MockVenueAdapter
from trading.risk.models import _ISSUER, ApprovedOrder, Side


def test_order_state_transitions():
    assert (
        transition_order_state(OrderState.CREATED, OrderEventType.SUBMITTED)
        == OrderState.SUBMITTED
    )
    assert (
        transition_order_state(OrderState.SUBMITTED, OrderEventType.ACKED)
        == OrderState.ACKED
    )
    assert (
        transition_order_state(OrderState.ACKED, OrderEventType.FILL)
        == OrderState.FILLED
    )
    assert (
        transition_order_state(OrderState.CREATED, OrderEventType.QUARANTINED)
        == OrderState.QUARANTINED
    )


def test_client_order_id_generation():
    cid = generate_client_order_id("strat_1", "sig_99")
    assert cid.startswith("strat_1:sig_99:")


@pytest.mark.asyncio
async def test_oms_idempotent_submission():
    venue = MockVenueAdapter()
    oms = OrderManagementSystem(venue_adapter=venue)

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
    cid = "strat_1:sig_1:abc12345"

    # First submission -> SUBMITTED
    s1 = await oms.submit_order(order, cid)
    assert s1 == OrderState.SUBMITTED

    # Second submission with SAME client_order_id -> Idempotent check returns ACKED (or existing state) without exception
    s2 = await oms.submit_order(order, cid)
    assert s2 in (OrderState.ACKED, OrderState.FILLED)


def _approved(size: float = 1.0) -> ApprovedOrder:
    return ApprovedOrder(
        asset="BTC",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=50000.0,
        stop_price=48000.0,
        target_price=55000.0,
        position_size=size,
        risk_pct=0.01,
        issuer=_ISSUER,
    )


@pytest.mark.asyncio
async def test_oms_quantizes_size_to_venue_grid_va066():
    """VA-066: with an instruments step map, submit_order must quantize the
    size DOWN onto the venue grid before submission (defense in depth)."""
    venue = MockVenueAdapter()
    oms = OrderManagementSystem(venue_adapter=venue, instruments={"BTC": "0.001"})

    order = _approved(size=0.12345678)
    cid = "strat_1:sig_grid:abc12345"

    state = await oms.submit_order(order, cid)
    assert state == OrderState.SUBMITTED

    # The recorded intended qty (what we asked the venue for) is on-grid.
    intended = oms.order_records[cid]["intended_qty"]
    assert intended == pytest.approx(0.123)
    # Decimal-exact: multiple of the step.
    from decimal import Decimal

    d = Decimal(str(intended)) / Decimal("0.001")
    assert d == d.to_integral_value()

    # And the venue actually received the quantized size.
    assert venue.orders[cid]["amount"] == pytest.approx(0.123)


@pytest.mark.asyncio
async def test_oms_rejects_sub_step_size_va066():
    """VA-066: a size that quantizes to zero must be REJECTED, not sent."""
    venue = MockVenueAdapter()
    oms = OrderManagementSystem(venue_adapter=venue, instruments={"BTC": "1"})

    order = _approved(size=0.4)  # below one lot on a 1.0-step grid
    cid = "strat_1:sig_substep:abc12345"

    state = await oms.submit_order(order, cid)
    assert state == OrderState.REJECTED
    assert cid not in venue.orders


@pytest.mark.asyncio
async def test_reconciler_quarantine_on_mismatch():
    venue = MockVenueAdapter()
    reconciler = StateReconciler(venue_adapter=venue)

    # Local position states BTC=1.0, but venue reports 0 positions
    local_pos = {"BTC": 1.0}

    quarantine_items = await reconciler.reconcile_once(local_pos)
    assert reconciler.has_quarantine()
    assert len(quarantine_items) == 1
    assert quarantine_items[0].reason == QuarantineReason.POSITION_MISMATCH
