"""Order state machine and event definitions for event-sourced execution.

The transition model is an EXPLICIT LEGAL-TRANSITION TABLE: a dict mapping each
state to the set of states it may legally move to. Every state change must be
routed through :func:`transition_order_state`, which consults the table and
raises :class:`IllegalTransitionError` on any unknown/illegal move (fixes
F-0075: previously any event transitioned from any state).

PARTIALLY_FILLED is a first-class, REACHABLE state: a FILL event whose fill_qty
is strictly less than the order's intended quantity lands in PARTIALLY_FILLED,
never FILLED (fixes F-0274).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, FrozenSet, Optional

__all__ = [
    "OrderState",
    "OrderEventType",
    "OrderEvent",
    "IllegalTransitionError",
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATES",
    "transition_order_state",
]


class OrderState(Enum):
    CREATED = "created"
    SUBMITTED = "submitted"
    ACKED = "acked"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    PARTIAL_FILL_FINALIZED = "partial_fill_finalized"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    QUARANTINED = "quarantined"
    UNKNOWN = "unknown"


class OrderEventType(Enum):
    CREATED = "created"
    SUBMITTED = "submitted"
    ACKED = "acked"
    FILL = "fill"
    CANCEL = "cancel"
    EXPIRY = "expiry"
    PARTIAL_FILL_FINALIZED = "partial_fill_finalized"
    CANCELED = "canceled"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class OrderEvent:
    event_id: str
    client_order_id: str
    event_type: OrderEventType
    timestamp: datetime
    fill_price: Optional[float] = None
    fill_qty: Optional[float] = None
    reason: Optional[str] = None
    # NOTE: appended AFTER legacy fields to keep positional construction
    # (event_id, client_order_id, event_type, timestamp, ...) stable.
    intended_qty: Optional[float] = None


class IllegalTransitionError(ValueError):
    """Raised when an order event would drive an order through an illegal or
    unknown state transition (e.g. a FILL against a CANCELED order)."""

    def __init__(self, current_state: "OrderState", event_type: "OrderEventType"):
        self.current_state = current_state
        self.event_type = event_type
        super().__init__(
            f"Illegal order-state transition: {current_state.value} "
            f"cannot accept event '{event_type.value}'"
        )


def _frozenset(*states: OrderState) -> FrozenSet[OrderState]:
    return frozenset(states)


#: Explicit legal-transition table: state -> set of allowed next states.
#: Absence from the table (or an empty set) means terminal/no-exit.
#: This table is THE source of truth; transition_order_state() enforces it.
LEGAL_TRANSITIONS: Dict[OrderState, FrozenSet[OrderState]] = {
    OrderState.CREATED: _frozenset(
        OrderState.SUBMITTED,
        OrderState.REJECTED,
        OrderState.CANCELED,
        OrderState.EXPIRED,
        OrderState.QUARANTINED,
    ),
    OrderState.SUBMITTED: _frozenset(
        OrderState.ACKED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
        OrderState.QUARANTINED,
    ),
    OrderState.ACKED: _frozenset(
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
        OrderState.QUARANTINED,
    ),
    # A working order with one or more partial fills can still: take another
    # partial fill (self-loop), fill fully, cancel (keeping its filled_qty),
    # expire, be rejected by the venue, or be finalized as a partial-fill
    # resolution (chase remainder abandonment).
    OrderState.PARTIALLY_FILLED: _frozenset(
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.PARTIAL_FILL_FINALIZED,
        OrderState.CANCELED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
        OrderState.QUARANTINED,
    ),
    OrderState.FILLED: frozenset(),
    OrderState.PARTIAL_FILL_FINALIZED: frozenset(),
    OrderState.CANCELED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.EXPIRED: frozenset(),
    OrderState.QUARANTINED: frozenset(),
    OrderState.UNKNOWN: frozenset({OrderState.QUARANTINED}),
}

#: States from which no legal exit exists.
TERMINAL_STATES: FrozenSet[OrderState] = frozenset(
    state for state, allowed in LEGAL_TRANSITIONS.items() if not allowed
)


def transition_order_state(
    current_state: OrderState,
    event_type: OrderEventType,
    *,
    fill_qty: Optional[float] = None,
    intended_qty: Optional[float] = None,
    prev_filled_qty: Optional[float] = None,
) -> OrderState:
    """Compute the deterministic next OrderState for ``event_type`` applied to
    ``current_state``, enforcing the LEGAL_TRANSITIONS table.

    Raises IllegalTransitionError when the (state, event) pair is not in the
    table -- e.g. a FILL against a CANCELED/FILLED/REJECTED order, or a
    SUBMITTED event against anything but CREATED.

    A FILL event resolves to PARTIALLY_FILLED vs FILLED using fill_qty /
    intended_qty (plus prev_filled_qty for stacked partials) when both are
    provided: cumulative qty (< intended) while the order is open yields
    PARTIALLY_FILLED (F-0274); full-or-more yields FILLED. Without quantity
    context a FILL conservatively yields FILLED (the legacy single-shot-fill
    contract).
    """
    if not isinstance(current_state, OrderState):
        raise TypeError(
            f"current_state must be OrderState, got {type(current_state)!r}"
        )

    allowed = LEGAL_TRANSITIONS.get(current_state)
    if allowed is None:
        raise IllegalTransitionError(current_state, event_type)

    def _resolve_fill_target() -> OrderState:
        """Map a FILL event to its target state given quantity context."""
        if fill_qty is not None and intended_qty:
            cumulative = fill_qty + (prev_filled_qty or 0.0)
            return (
                OrderState.PARTIALLY_FILLED
                if cumulative < intended_qty
                else OrderState.FILLED
            )
        # No quantity context: legacy single-shot fills land in FILLED.
        return OrderState.FILLED

    if event_type is OrderEventType.FILL:
        target = _resolve_fill_target()
        if target not in allowed:
            raise IllegalTransitionError(current_state, event_type)
        return target

    next_state = {
        OrderEventType.SUBMITTED: OrderState.SUBMITTED,
        OrderEventType.ACKED: OrderState.ACKED,
        OrderEventType.CANCEL: OrderState.CANCELED,
        OrderEventType.CANCELED: OrderState.CANCELED,
        OrderEventType.EXPIRY: OrderState.EXPIRED,
        OrderEventType.REJECTED: OrderState.REJECTED,
        OrderEventType.QUARANTINED: OrderState.QUARANTINED,
    }.get(event_type)

    if event_type is OrderEventType.PARTIAL_FILL_FINALIZED:
        next_state = OrderState.PARTIAL_FILL_FINALIZED
    elif event_type is OrderEventType.CREATED:
        raise IllegalTransitionError(current_state, event_type)

    if next_state is None:
        raise IllegalTransitionError(current_state, event_type)
    if next_state not in allowed:
        raise IllegalTransitionError(current_state, event_type)
    return next_state
