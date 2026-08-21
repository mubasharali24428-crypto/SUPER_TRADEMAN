"""Order state machine and event definitions for event-sourced execution."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

__all__ = [
    "OrderState",
    "OrderEventType",
    "OrderEvent",
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
    QUARANTINED = "quarantined"
    UNKNOWN = "unknown"


class OrderEventType(Enum):
    CREATED = "created"
    SUBMITTED = "submitted"
    ACKED = "acked"
    FILL = "fill"
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


def transition_order_state(current_state: OrderState, event_type: OrderEventType) -> OrderState:
    """Computes the deterministic next OrderState given an OrderEvent."""
    if current_state is OrderState.QUARANTINED:
        return OrderState.QUARANTINED

    if event_type is OrderEventType.QUARANTINED:
        return OrderState.QUARANTINED

    if event_type is OrderEventType.SUBMITTED:
        return OrderState.SUBMITTED

    if event_type is OrderEventType.ACKED:
        return OrderState.ACKED

    if event_type is OrderEventType.FILL:
        return OrderState.FILLED

    if event_type is OrderEventType.PARTIAL_FILL_FINALIZED:
        return OrderState.PARTIAL_FILL_FINALIZED

    if event_type is OrderEventType.CANCELED:
        return OrderState.CANCELED

    if event_type is OrderEventType.REJECTED:
        return OrderState.REJECTED

    return OrderState.UNKNOWN
