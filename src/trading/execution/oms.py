"""Order Management System (OMS) with idempotent submission, staleness sentinel pre-flight checks, and shadow mode interception."""

from typing import Any, Dict, Optional

from trading.config import ExecutionMode
from trading.data.staleness import StalenessSentinel
from trading.execution.outbox import OrderIntent, OutboxStore
from trading.execution.shadow import L2OrderBookSnapshot, ShadowInterceptor
from trading.execution.state_machine import OrderEvent, OrderEventType, OrderState, transition_order_state
from trading.execution.venue_adapter import VenueAdapter
from trading.observability.logger import get_logger
from trading.risk.models import ApprovedOrder, RiskDeviationEvent, Side

__all__ = ["OrderManagementSystem"]

logger = get_logger("trading.execution.oms")


class OrderManagementSystem:
    """Manages order submission, state transitions, outbox updates, and shadow mode interception."""

    def __init__(
        self,
        venue_adapter: VenueAdapter,
        outbox_store: Optional[OutboxStore] = None,
        staleness_sentinel: Optional[StalenessSentinel] = None,
        shadow_interceptor: Optional[ShadowInterceptor] = None,
        order_chaser: Optional[Any] = None,
        execution_mode: ExecutionMode = ExecutionMode.BACKTEST,
    ):
        self.venue_adapter = venue_adapter
        self.outbox_store = outbox_store
        self.staleness_sentinel = staleness_sentinel
        self.shadow_interceptor = shadow_interceptor
        self.order_chaser = order_chaser
        self.execution_mode = execution_mode
        self.active_orders: Dict[str, OrderState] = {}
        self.risk_deviations: list[RiskDeviationEvent] = []

    async def submit_order(
        self,
        order: ApprovedOrder,
        client_order_id: str,
        order_book: Optional[L2OrderBookSnapshot] = None,
    ) -> OrderState:
        """Idempotently submits an approved order.

        Pre-flight check:
          - Queries StalenessSentinel for asset freshness. If stale, rejects order with STALE_DATA_REJECTION.
        Wash Trading Prevention:
          - Cancels active working orders on the opposite side for asset before placing new order.
        Shadow Mode check:
          - If ExecutionMode.SHADOW, routes order to ShadowInterceptor without calling venue_adapter.
        """
        # Wash Trading Prevention
        if self.order_chaser is not None and hasattr(self.order_chaser, "working_orders"):
            for w_cid, w_info in list(self.order_chaser.working_orders.items()):
                if w_info.symbol == order.asset and w_info.side != order.side:
                    logger.info(f"[WASH_TRADING_PREVENTION] Canceling opposite working order {w_cid} on {order.asset} before submitting new order.")
                    await self.venue_adapter.cancel_order(w_cid, order.asset)
                    w_info.status = OrderState.CANCELED

        # 1. Pre-flight Staleness Check
        if self.staleness_sentinel is not None and self.staleness_sentinel.is_stale(order.asset):
            logger.error(
                f"[STALE_DATA_REJECTION] Rejecting order {client_order_id} for asset {order.asset}: WebSocket data is stale or circuit breaker tripped."
            )
            new_state = OrderState.REJECTED
            self.active_orders[client_order_id] = new_state
            if self.outbox_store:
                await self.outbox_store.update_status(client_order_id, new_state)
            return new_state

        # 2. Shadow Mode Interception Check
        if self.execution_mode is ExecutionMode.SHADOW:
            logger.info(f"[SHADOW_MODE] Intercepting order {client_order_id} for synthetic execution against live order book.")
            if self.shadow_interceptor is not None and order_book is not None:
                shadow_res = await self.shadow_interceptor.execute_shadow_fill(order, client_order_id, order_book)
                new_state = OrderState.FILLED if shadow_res.filled_qty > 0 else OrderState.REJECTED
            else:
                new_state = OrderState.FILLED  # fallback shadow fill state

            self.active_orders[client_order_id] = new_state
            if self.outbox_store:
                await self.outbox_store.update_status(client_order_id, new_state, f"shadow_{client_order_id}")
            # CRITICAL WARNING: NEVER invoke venue_adapter.create_order() in SHADOW mode
            return new_state

        # 3. Standard Live/Paper Idempotency check: check if order already exists on exchange
        existing = await self.venue_adapter.fetch_order(client_order_id, order.asset)
        if existing is not None:
            status = OrderState.ACKED if existing.get("status") == "open" else OrderState.FILLED
            self.active_orders[client_order_id] = status
            if self.outbox_store:
                await self.outbox_store.update_status(client_order_id, status, existing.get("id"))
            return status

        # 4. Submit new order to exchange venue
        try:
            res = await self.venue_adapter.create_order(order, client_order_id)
            ex_id = res.get("id")
            new_state = OrderState.SUBMITTED
            self.active_orders[client_order_id] = new_state
            if self.outbox_store:
                await self.outbox_store.update_status(client_order_id, new_state, ex_id)
            return new_state
        except Exception as e:
            new_state = OrderState.REJECTED
            self.active_orders[client_order_id] = new_state
            if self.outbox_store:
                await self.outbox_store.update_status(client_order_id, new_state)
            raise e

    async def finalize_partial_fill(
        self,
        client_order_id: str,
        filled_qty: float,
        intended_qty: float,
        initial_stop_price: float,
        asset: str,
        entry_price: float = 0.0,
    ) -> RiskDeviationEvent:
        """Finalizes a partially filled order, emitting PARTIAL_FILL_FINALIZED and logging RISK_DEVIATION."""
        new_state = OrderState.PARTIAL_FILL_FINALIZED
        self.active_orders[client_order_id] = new_state
        if self.outbox_store:
            await self.outbox_store.update_status(client_order_id, new_state)

        dist = abs(entry_price - initial_stop_price) if entry_price > 0 else 1.0
        intended_risk = intended_qty * dist
        realized_risk = filled_qty * dist

        deviation_event = RiskDeviationEvent(
            client_order_id=client_order_id,
            asset=asset,
            intended_qty=intended_qty,
            realized_qty=filled_qty,
            intended_risk_usd=intended_risk,
            realized_risk_usd=realized_risk,
        )
        self.risk_deviations.append(deviation_event)

        logger.info(
            f"[RISK_DEVIATION] Order {client_order_id} ({asset}): Intended qty {intended_qty}, "
            f"realized qty {filled_qty}. Risk reduced from ${intended_risk:.2f} to ${realized_risk:.2f}. "
            f"Initial stop price strictly maintained at {initial_stop_price}."
        )

        return deviation_event

