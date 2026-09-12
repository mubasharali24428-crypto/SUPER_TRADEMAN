"""EX5 tests: explicit legal-transition table (F-0075) and PARTIALLY_FILLED
reachability (F-0274), plus EXPIRED handling."""

import pytest

from trading.execution.state_machine import (LEGAL_TRANSITIONS,
                                             TERMINAL_STATES,
                                             IllegalTransitionError,
                                             OrderEventType, OrderState,
                                             transition_order_state)

# --- legal happy paths -------------------------------------------------------


@pytest.mark.parametrize(
    "current,event,expected",
    [
        # lifecycle forward path
        (OrderState.CREATED, OrderEventType.SUBMITTED, OrderState.SUBMITTED),
        (OrderState.SUBMITTED, OrderEventType.ACKED, OrderState.ACKED),
        (OrderState.CREATED, OrderEventType.CANCELED, OrderState.CANCELED),
        (OrderState.CREATED, OrderEventType.REJECTED, OrderState.REJECTED),
        (OrderState.CREATED, OrderEventType.EXPIRY, OrderState.EXPIRED),
        (OrderState.CREATED, OrderEventType.QUARANTINED, OrderState.QUARANTINED),
        (OrderState.SUBMITTED, OrderEventType.EXPIRY, OrderState.EXPIRED),
        (OrderState.ACKED, OrderEventType.EXPIRY, OrderState.EXPIRED),
        (OrderState.ACKED, OrderEventType.CANCEL, OrderState.CANCELED),
        (
            OrderState.PARTIALLY_FILLED,
            OrderEventType.PARTIAL_FILL_FINALIZED,
            OrderState.PARTIAL_FILL_FINALIZED,
        ),
    ],
)
def test_legal_transitions_are_accepted(current, event, expected):
    assert transition_order_state(current, event) == expected


def test_full_uuid_event_is_rejected_from_everywhere():
    """A CREATED event only makes sense as the genesis; from any live state it
    must be rejected, not silently accepted like the old any-to-any map."""
    for state in OrderState:
        if state is OrderState.CREATED:
            continue
        with pytest.raises(IllegalTransitionError):
            transition_order_state(state, OrderEventType.CREATED)


# --- illegal-transition rejection matrix (F-0075) ----------------------------


@pytest.mark.parametrize(
    "current,event",
    [
        # fills against terminal states
        (OrderState.FILLED, OrderEventType.FILL),
        (OrderState.CANCELED, OrderEventType.FILL),
        (OrderState.REJECTED, OrderEventType.FILL),
        (OrderState.EXPIRED, OrderEventType.FILL),
        (OrderState.PARTIAL_FILL_FINALIZED, OrderEventType.FILL),
        (OrderState.QUARANTINED, OrderEventType.FILL),
        # resubmit / ack after anything but CREATED
        (OrderState.SUBMITTED, OrderEventType.SUBMITTED),
        (OrderState.ACKED, OrderEventType.SUBMITTED),
        (OrderState.FILLED, OrderEventType.SUBMITTED),
        (OrderState.CANCELED, OrderEventType.SUBMITTED),
        (OrderState.REJECTED, OrderEventType.SUBMITTED),
        # late fills after cancel/reject/expiry (the F-0031 class of bug)
        (OrderState.CANCELED, OrderEventType.ACKED),
        (OrderState.FILLED, OrderEventType.CANCEL),
        (OrderState.REJECTED, OrderEventType.CANCEL),
        (OrderState.EXPIRED, OrderEventType.CANCEL),
        # double-terminalization
        (OrderState.FILLED, OrderEventType.REJECTED),
        (OrderState.CANCELED, OrderEventType.REJECTED),
        (OrderState.PARTIAL_FILL_FINALIZED, OrderEventType.CANCEL),
        # UNKNOWN only escalates to QUARANTINED
        (OrderState.UNKNOWN, OrderEventType.FILL),
        (OrderState.UNKNOWN, OrderEventType.SUBMITTED),
    ],
)
def test_illegal_transitions_raise(current, event):
    with pytest.raises(IllegalTransitionError):
        transition_order_state(current, event)


def test_unknown_state_only_escalates_to_quarantined():
    assert transition_order_state(OrderState.UNKNOWN, OrderEventType.QUARANTINED) == (
        OrderState.QUARANTINED
    )


def test_terminal_states_have_no_exits():
    for state in (
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
        OrderState.PARTIAL_FILL_FINALIZED,
        OrderState.QUARANTINED,
    ):
        assert state in TERMINAL_STATES
        assert LEGAL_TRANSITIONS[state] == frozenset()


def test_error_message_names_the_offending_pair():
    with pytest.raises(IllegalTransitionError) as ei:
        transition_order_state(OrderState.CANCELED, OrderEventType.FILL)
    assert "canceled" in str(ei.value)
    assert "fill" in str(ei.value)
    assert ei.value.current_state is OrderState.CANCELED


# --- PARTIALLY_FILLED reachability (F-0274) ----------------------------------


def test_partial_fill_qty_yields_partially_filled_not_filled():
    assert (
        transition_order_state(
            OrderState.SUBMITTED,
            OrderEventType.FILL,
            fill_qty=3.0,
            intended_qty=10.0,
        )
        == OrderState.PARTIALLY_FILLED
    )
    assert (
        transition_order_state(
            OrderState.ACKED,
            OrderEventType.FILL,
            fill_qty=3.0,
            intended_qty=10.0,
        )
        == OrderState.PARTIALLY_FILLED
    )


def test_full_fill_qty_yields_filled():
    assert (
        transition_order_state(
            OrderState.SUBMITTED,
            OrderEventType.FILL,
            fill_qty=10.0,
            intended_qty=10.0,
        )
        == OrderState.FILLED
    )
    # overfill is also terminal-FILLED, not partially
    assert (
        transition_order_state(
            OrderState.ACKED,
            OrderEventType.FILL,
            fill_qty=11.0,
            intended_qty=10.0,
        )
        == OrderState.FILLED
    )


def test_stacked_partials_self_loop_then_fill():
    # Callers with a cumulative book (e.g. the OMS) pass prev_filled_qty so
    # the table resolves on CUMULATIVE vs intended quantity.
    state = transition_order_state(
        OrderState.SUBMITTED, OrderEventType.FILL, fill_qty=1.0, intended_qty=3.0
    )
    assert state == OrderState.PARTIALLY_FILLED
    state = transition_order_state(
        state, OrderEventType.FILL, fill_qty=1.0, intended_qty=3.0, prev_filled_qty=1.0
    )
    assert state == OrderState.PARTIALLY_FILLED, "second partial must stay reachable"
    state = transition_order_state(
        state, OrderEventType.FILL, fill_qty=1.0, intended_qty=3.0, prev_filled_qty=2.0
    )
    assert state == OrderState.FILLED


def test_partial_cancel_keeps_a_legal_path_to_canceled():
    """A partially-filled order can still cancel -- the exact path the OMS
    uses to keep filled_qty under a CANCELED terminal state."""
    state = transition_order_state(
        OrderState.SUBMITTED, OrderEventType.FILL, fill_qty=0.3, intended_qty=1.0
    )
    assert transition_order_state(state, OrderEventType.CANCEL) == OrderState.CANCELED
