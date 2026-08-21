"""Tests for Event-Sourced OMS, Outbox, and Reconciler."""

import pytest
from datetime import datetime, timezone

from trading.execution.outbox import generate_client_order_id, OrderIntent
from trading.execution.oms import OrderManagementSystem
from trading.execution.reconciler import StateReconciler, QuarantineReason
from trading.execution.state_machine import OrderState, OrderEventType, transition_order_state
from trading.execution.venue_adapter import MockVenueAdapter
from trading.risk.models import ApprovedOrder, Side, _ISSUER


def test_order_state_transitions():
    assert transition_order_state(OrderState.CREATED, OrderEventType.SUBMITTED) == OrderState.SUBMITTED
    assert transition_order_state(OrderState.SUBMITTED, OrderEventType.ACKED) == OrderState.ACKED
    assert transition_order_state(OrderState.ACKED, OrderEventType.FILL) == OrderState.FILLED
    assert transition_order_state(OrderState.CREATED, OrderEventType.QUARANTINED) == OrderState.QUARANTINED


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
