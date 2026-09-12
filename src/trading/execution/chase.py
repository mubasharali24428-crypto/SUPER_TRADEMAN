"""Execution Chase & Partial Fill Resolution Engine.

SOCRATIC GUARDRAIL ANSWERS:

1. RACE CONDITION RESOLUTION (Cancel Timeout vs Exchange Fill):
   If the OrderChaser issues a Cancel request to the exchange, but the network times out or
   a fill occurs right as the cancel arrives, the exchange matching engine processes the fill
   first and returns FILLED status (or rejects the cancel as unknown/closed).
   The StateReconciler and OMS handle this race condition by querying the exchange order/trade status.
   If the exchange executed the fill, local state transitions from CANCELED to FILLED/PARTIAL_FILL_FINALIZED,
   updates the portfolio position size, and attaches the required ApprovedExit stop loss protection.

2. WASH TRADING PREVENTION (Partial Fill vs Rapid Strategy Reversal):
   If a partial fill occurs and the strategy generates an immediate opposite signal (reversal),
   the OMS checks all active working orders for the target symbol. Before creating or dispatching
   the new opposite order, OMS explicitly cancels and finalizes any resting chase/working orders for
   that symbol first, preventing simultaneous resting buy and sell limit orders (wash trades).
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading.execution.state_machine import OrderState
from trading.execution.venue_adapter import InstrumentInfo, VenueAdapter
from trading.observability.logger import get_logger
from trading.risk.models import Side

__all__ = [
    "TokenBucketRateLimiter",
    "WorkingOrderInfo",
    "OrderChaser",
]

logger = get_logger("trading.execution.chase")


class TokenBucketRateLimiter:
    """Token-bucket rate limiter for managing API request limits."""

    def __init__(self, capacity: int = 10, refill_rate: float = 5.0):
        self.capacity = capacity
        self.refill_rate = refill_rate  # tokens per second
        self.tokens = float(capacity)
        self.last_refill_time = time.time()
        self._lock = asyncio.Lock()
        # Denied-acquire telemetry (EX5): a denial must be visible, not silent.
        self.denied_count = 0

    async def acquire(self) -> bool:
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_refill_time
            self.tokens = min(
                float(self.capacity), self.tokens + elapsed * self.refill_rate
            )
            self.last_refill_time = now

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            self.denied_count += 1
            return False


@dataclass
class WorkingOrderInfo:
    client_order_id: str
    symbol: str
    side: Side
    submitted_price: float
    stop_price: float
    requested_qty: float
    filled_qty: float = 0.0
    last_fill_time_ms: float = field(default_factory=lambda: time.time() * 1000.0)
    status: OrderState = OrderState.SUBMITTED


class OrderChaser:
    """Monitors active limit orders, handles stale orders, and enforces min_notional filters."""

    def __init__(
        self,
        venue_adapter: VenueAdapter,
        chase_timeout_ms: float = 5000.0,
        rate_limiter: Optional[TokenBucketRateLimiter] = None,
    ):
        self.venue_adapter = venue_adapter
        self.chase_timeout_ms = chase_timeout_ms
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter()
        self.working_orders: Dict[str, WorkingOrderInfo] = {}
        # Count of order-touching actions skipped because the rate limiter
        # denied the acquire (EX5: denials must be observable, never silent).
        self.denied_actions: int = 0

    def register_order(self, info: WorkingOrderInfo) -> None:
        self.working_orders[info.client_order_id] = info

    def on_fill_event(
        self,
        client_order_id: str,
        filled_qty: float,
        fill_price: float,
        timestamp_ms: Optional[float] = None,
    ) -> None:
        if client_order_id in self.working_orders:
            order = self.working_orders[client_order_id]
            order.filled_qty += filled_qty
            order.last_fill_time_ms = (
                timestamp_ms if timestamp_ms is not None else (time.time() * 1000.0)
            )
            if order.filled_qty >= order.requested_qty:
                order.status = OrderState.FILLED

    async def check_and_chase(
        self,
        current_time_ms: Optional[float] = None,
        oms_reference: Optional[Any] = None,
    ) -> List[str]:
        """Scans working orders and performs cancel/reprice or remainder abandonment.

        Rate-limit invariant (EX5): EVERY order-touching venue action (cancel,
        reprice/resubmit, remainder abandonment) must first win a token from
        the limiter. A denied acquire means the action is SKIPPED and logged --
        never silently swallowed -- and the scan simply resumes on a later call;
        it does not spin into an unthrottled rescan storm. Denials are counted
        on ``rate_limiter.denied_count`` and ``self.denied_actions``.
        """
        now_ms = (
            current_time_ms if current_time_ms is not None else (time.time() * 1000.0)
        )
        action_log: List[str] = []

        to_remove = []
        for client_order_id, order in list(self.working_orders.items()):
            if order.status in (
                OrderState.FILLED,
                OrderState.CANCELED,
                OrderState.REJECTED,
                OrderState.PARTIAL_FILL_FINALIZED,
            ):
                to_remove.append(client_order_id)
                continue

            elapsed_ms = now_ms - order.last_fill_time_ms
            if elapsed_ms > self.chase_timeout_ms:
                remaining_qty = order.requested_qty - order.filled_qty
                instrument = await self.venue_adapter.get_instrument_info(order.symbol)

                current_notional = remaining_qty * order.submitted_price
                below_min = (
                    current_notional < instrument.min_notional
                    or remaining_qty < instrument.min_qty
                )
                # Gate EVERY order-touching action on the shared rate limiter:
                # abandonment (cancel + OMS finalize) and cancel/reprice alike.
                acquired = await self.rate_limiter.acquire()
                if not acquired:
                    # Denied => skip-and-log. The stale order stays tracked and
                    # will be reconsidered by a future scan once budget frees
                    # up; no unthrottled rescan storm.
                    self.denied_actions += 1
                    logger.warning(
                        f"[RATE_LIMIT_DENIED] Chase action for {client_order_id} "
                        f"({'abandon_remainder' if below_min else 'cancel_and_reprice'}) "
                        f"skipped; will retry next scan."
                        f" (denied_actions={self.denied_actions})"
                    )
                    action_log.append(f"RATE_LIMIT_DENIED:{client_order_id}")
                    continue

                if below_min:
                    # Abandon remainder below min_notional threshold
                    logger.info(
                        f"[REMAINDER_ABANDONED_MIN_NOTIONAL] Order {client_order_id} remaining notional "
                        f"${current_notional:.2f} < min_notional ${instrument.min_notional}. Abandoning remainder."
                    )
                    await self.venue_adapter.cancel_order(client_order_id, order.symbol)
                    order.status = OrderState.PARTIAL_FILL_FINALIZED
                    if oms_reference is not None and hasattr(
                        oms_reference, "finalize_partial_fill"
                    ):
                        await oms_reference.finalize_partial_fill(
                            client_order_id=client_order_id,
                            filled_qty=order.filled_qty,
                            intended_qty=order.requested_qty,
                            initial_stop_price=order.stop_price,
                            asset=order.symbol,
                        )
                    else:
                        # No OMS wired in: still resolve the remainder EXPLICITLY.
                        # The canceled remainder must not vanish from tracking
                        # without a terminal status + audit log (F-0280).
                        logger.warning(
                            f"[REMAINDER_UNRESOLVED_NO_OMS] {client_order_id}: remaining qty "
                            f"{remaining_qty} abandoned with filled_qty={order.filled_qty} "
                            f"preserved; NO replacement registered -- position may be "
                            f"under-filled vs strategy intent."
                        )
                    action_log.append(f"ABANDONED:{client_order_id}")
                    to_remove.append(client_order_id)
                else:
                    # Cancel-and-reprice: this implementation cancels and then
                    # RESOLVES the remainder explicitly (abandonment with OMS
                    # risk-deviation when available). Re-quoting at a fresh
                    # market price would require a live reference price, which
                    # VenueAdapter does not expose; until it does, abandoning
                    # beats silently dropping the remainder (F-0280).
                    logger.info(
                        f"[CANCEL_AND_REPRICE] Stale order {client_order_id} (age {elapsed_ms:.0f}ms). Canceling."
                    )
                    await self.venue_adapter.cancel_order(client_order_id, order.symbol)
                    order.status = OrderState.PARTIAL_FILL_FINALIZED
                    if oms_reference is not None and hasattr(
                        oms_reference, "finalize_partial_fill"
                    ):
                        await oms_reference.finalize_partial_fill(
                            client_order_id=client_order_id,
                            filled_qty=order.filled_qty,
                            intended_qty=order.requested_qty,
                            initial_stop_price=order.stop_price,
                            asset=order.symbol,
                        )
                    else:
                        logger.warning(
                            f"[REMAINDER_UNRESOLVED_NO_OMS] {client_order_id}: remaining qty "
                            f"{remaining_qty} abandoned after chase-cancel with "
                            f"filled_qty={order.filled_qty} preserved."
                        )
                    action_log.append(f"REPRICED:{client_order_id}")

        for cid in to_remove:
            self.working_orders.pop(cid, None)

        return action_log
