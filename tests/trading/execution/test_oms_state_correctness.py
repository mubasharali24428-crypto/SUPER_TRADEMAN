"""EX5 tests: OMS order-state correctness.

- Unknown client_order_id fills are REJECTED, never direction-defaulted (F-0030)
- Partial-fill-then-cancel lands CANCELED with filled_qty preserved (F-0031)
- generate_client_order_id keeps full uuid entropy (F-0138)
"""

import pytest

from trading.execution.oms import (
    OrderManagementSystem,
    UnknownOrderFillError,
)
from trading.execution.outbox import generate_client_order_id
from trading.execution.state_machine import IllegalTransitionError, OrderState
from trading.execution.venue_adapter import MockVenueAdapter
from trading.risk.models import ApprovedOrder, Side, _ISSUER


def _make_order(side=Side.LONG, size=10.0):
    return ApprovedOrder(
        asset="BTC",
        asset_class="crypto",
        side=side,
        entry_price=50_000.0,
        stop_price=48_000.0,
        target_price=55_000.0,
        position_size=size,
        risk_pct=0.01,
        issuer=_ISSUER,
    )


async def _submit_long(oms, cid, size=10.0):
    return await oms.submit_order(_make_order(size=size), cid)


# --- F-0030: unknown-id fill rejection ---------------------------------------

@pytest.mark.asyncio
async def test_fill_for_unknown_client_order_id_raises_and_never_moves_position():
    oms = OrderManagementSystem(venue_adapter=MockVenueAdapter())

    with pytest.raises(UnknownOrderFillError):
        await oms.apply_fill("never_submitted_cid", fill_qty=7.0, fill_price=50_000.0)

    # THE invariant: no position delta may exist for an untracked fill.
    assert oms.positions.get("BTC", 0.0) == 0.0
    assert "never_submitted_cid" not in oms.active_orders
    assert "never_submitted_cid" not in oms.order_fills


@pytest.mark.asyncio
async def test_unknown_fill_direction_is_never_defaulted_to_short():
    """The old bug: missing order dict defaulted direction to SELL (-1). A
    rejected orphan fill must leave positions untouched in EITHER direction."""
    oms = OrderManagementSystem(venue_adapter=MockVenueAdapter())
    with pytest.raises(UnknownOrderFillError):
        await oms.apply_fill("orphan_1", fill_qty=2.0, fill_price=50_000.0)
    assert oms.positions == {}


@pytest.mark.asyncio
async def test_known_id_partial_then_full_fill_tracks_position_and_states():
    oms = OrderManagementSystem(venue_adapter=MockVenueAdapter())
    cid = "strat_x:sig_y:known"
    await _submit_long(oms, cid, size=10.0)

    partial = await oms.apply_fill(cid, fill_qty=3.0, fill_price=50_100.0)
    assert oms.active_orders[cid] == OrderState.PARTIALLY_FILLED
    assert partial["net_position"] == pytest.approx(3.0)  # LONG => positive delta

    final = await oms.apply_fill(cid, fill_qty=7.0, fill_price=50_200.0)
    assert oms.active_orders[cid] == OrderState.FILLED
    assert oms.get_filled_qty(cid) == pytest.approx(10.0)
    assert final["net_position"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_short_side_fill_delta_is_negative():
    oms = OrderManagementSystem(venue_adapter=MockVenueAdapter())
    cid = "strat_x:sig_y:short"
    await oms.submit_order(_make_order(side=Side.SHORT, size=4.0), cid)
    res = await oms.apply_fill(cid, fill_qty=4.0, fill_price=50_000.0)
    assert res["position_delta"] == pytest.approx(-4.0)
    assert oms.positions["BTC"] == pytest.approx(-4.0)


# --- F-0031: partial-fill-then-cancel ----------------------------------------

@pytest.mark.asyncio
async def test_cancel_after_partial_fill_is_canceled_with_filled_qty_preserved():
    oms = OrderManagementSystem(venue_adapter=MockVenueAdapter())
    cid = "strat_x:sig_y:partial_cancel"
    await _submit_long(oms, cid, size=10.0)
    await oms.apply_fill(cid, fill_qty=3.0, fill_price=50_000.0)
    assert oms.active_orders[cid] == OrderState.PARTIALLY_FILLED

    new_state = await oms.cancel_order(cid)

    # NEVER FILLED -- the 70% remainder is gone, not silently executed.
    assert new_state == OrderState.CANCELED
    assert oms.active_orders[cid] == OrderState.CANCELED
    assert oms.get_filled_qty(cid) == pytest.approx(3.0), "filled_qty must survive cancel"
    assert oms.positions["BTC"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_late_aggregate_fill_after_cancel_cannot_resurrect_to_filled():
    """The F-0031 race: a late fill message arrives after the cancel landed.
    CANCELED is terminal; the fill must be refused, not flip the order FILLED."""
    oms = OrderManagementSystem(venue_adapter=MockVenueAdapter())
    cid = "strat_x:sig_y:race"
    await _submit_long(oms, cid, size=10.0)
    await oms.apply_fill(cid, fill_qty=3.0, fill_price=50_000.0)
    await oms.cancel_order(cid)
    assert oms.active_orders[cid] == OrderState.CANCELED

    with pytest.raises(IllegalTransitionError):
        await oms.apply_fill(cid, fill_qty=7.0, fill_price=50_000.0)

    assert oms.active_orders[cid] == OrderState.CANCELED
    assert oms.get_filled_qty(cid) == pytest.approx(3.0)  # unchanged


@pytest.mark.asyncio
async def test_cancel_of_fully_filled_order_is_illegal():
    oms = OrderManagementSystem(venue_adapter=MockVenueAdapter())
    cid = "strat_x:sig_y:filled"
    await _submit_long(oms, cid, size=1.0)
    await oms.apply_fill(cid, fill_qty=1.0, fill_price=50_000.0)
    with pytest.raises(IllegalTransitionError):
        await oms.cancel_order(cid)


@pytest.mark.asyncio
async def test_cancel_of_unknown_order_raises_keyerror():
    oms = OrderManagementSystem(venue_adapter=MockVenueAdapter())
    with pytest.raises(KeyError):
        await oms.cancel_order("ghost_order")


# --- F-0138: full-uuid client order ids --------------------------------------

def test_client_order_id_carries_full_uuid_entropy():
    for _ in range(25):
        cid = generate_client_order_id("strat_1", "sig_99")
        suffix = cid.rsplit(":", 1)[-1].replace("-", "")
        assert len(suffix) >= 32, f"uuid suffix truncated: {cid!r}"
    # and uniqueness across a decent batch
    ids = {generate_client_order_id(f"s{i}", "sig") for i in range(500)}
    assert len(ids) == 500


def test_client_order_id_keeps_strategy_signal_prefix():
    cid = generate_client_order_id("strat_1", "sig_99")
    assert cid.startswith("strat_1:sig_99:")
