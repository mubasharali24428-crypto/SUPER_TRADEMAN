"""Order Management System (OMS) with idempotent submission, staleness sentinel pre-flight checks, and shadow mode interception.

Order-state correctness invariants (WAVE4 EX5):

- Every state change routes through ``transition_order_state`` and its explicit
  LEGAL_TRANSITIONS table; illegal transitions raise IllegalTransitionError.
- Fill applications for UNKNOWN client_order_ids raise UnknownOrderFillError
  and are logged -- a direction-defaulted position delta is NEVER applied
  (fixes F-0030). Direction always comes from the order's own recorded side,
  never from a caller-supplied default.
- Canceling a partially-filled order yields CANCELED with the accumulated
  filled_qty preserved -- never FILLED, never a silent remainder vanish
  (fixes F-0031).
"""

from dataclasses import replace
from typing import Any, Dict, Mapping, Optional

from trading.config import ExecutionMode
from trading.core.money import quantize_to_step
from trading.data.staleness import StalenessSentinel
from trading.execution.outbox import OrderIntent, OutboxStore
from trading.execution.shadow import L2OrderBookSnapshot, ShadowInterceptor
from trading.execution.state_machine import (
    TERMINAL_STATES,
    IllegalTransitionError,
    OrderEvent,
    OrderEventType,
    OrderState,
    transition_order_state,
)
from trading.execution.venue_adapter import VenueAdapter
from trading.observability.logger import get_logger
from trading.risk.models import ApprovedOrder, RiskDeviationEvent, Side

__all__ = ["OrderManagementSystem", "UnknownOrderFillError", "OrderFill"]

logger = get_logger("trading.execution.oms")


class UnknownOrderFillError(KeyError):
    """A fill arrived for a client_order_id this OMS never issued or tracks.

    Raised INSTEAD of applying a direction-defaulted position delta (F-0030).
    """


class OrderFill(Dict[str, Any]):
    """Result record of an applied fill."""


class OrderManagementSystem:
    """Manages order submission, state transitions, outbox updates, and shadow mode
    interception. VA-066: order sizes are quantized to the instrument step via
    RiskEngine.instruments (quantize_to_step) before submission."""

    def __init__(
        self,
        venue_adapter: VenueAdapter,
        outbox_store: Optional[OutboxStore] = None,
        staleness_sentinel: Optional[StalenessSentinel] = None,
        shadow_interceptor: Optional[ShadowInterceptor] = None,
        order_chaser: Optional[Any] = None,
        execution_mode: ExecutionMode = ExecutionMode.BACKTEST,
        instruments: Optional[Mapping[str, str]] = None,
    ):
        # VA-066: optional per-instrument step-size map (asset -> step string),
        # same convention as RiskEngine.instruments. When present, submit_order
        # quantizes the order size DOWN onto the venue grid before submission
        # so the exchange never receives an off-grid quantity — defense in
        # depth on top of the engine-side quantization (F-0342).
        self.instruments = dict(instruments) if instruments else None
        self.venue_adapter = venue_adapter
        self.outbox_store = outbox_store
        self.staleness_sentinel = staleness_sentinel
        self.shadow_interceptor = shadow_interceptor
        self.order_chaser = order_chaser
        self.execution_mode = execution_mode
        self.active_orders: Dict[str, OrderState] = {}
        self.risk_deviations: list[RiskDeviationEvent] = []
        # Per-order execution bookkeeping: recorded side/intent and cumulative fills.
        self.order_records: Dict[str, Dict[str, Any]] = {}
        self.order_fills: Dict[str, float] = {}  # cid -> cumulative filled_qty
        # Net position per asset, moved ONLY via apply_fill with a verified record.
        self.positions: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # State-transition plumbing: ALL active_orders writes go through here.
    # ------------------------------------------------------------------
    async def _transition(
        self,
        client_order_id: str,
        event_type: OrderEventType,
        *,
        fill_price: Optional[float] = None,
        fill_qty: Optional[float] = None,
        exchange_order_id: Optional[str] = None,
    ) -> OrderState:
        current = self.active_orders.get(client_order_id, OrderState.CREATED)
        intended_qty = self.order_records.get(client_order_id, {}).get("intended_qty")

        def _resolve() -> OrderState:
            return transition_order_state(
                current,
                event_type,
                fill_qty=fill_qty,
                intended_qty=intended_qty,
                prev_filled_qty=self.order_fills.get(client_order_id, 0.0),
            )

        try:
            new_state = _resolve()
        except IllegalTransitionError:
            if current is OrderState.CREATED:
                # Any venue-observable event (fill/ack/cancel) implies the order
                # left local creation and was submitted; replay the implicit
                # CREATED -> SUBMITTED hop so the table stays authoritative.
                logger.debug(
                    f"[STATE_FAST_PATH] {client_order_id}: implicit CREATED -> "
                    f"SUBMITTED before '{event_type.value}'."
                )
                self.active_orders[client_order_id] = transition_order_state(
                    OrderState.CREATED, OrderEventType.SUBMITTED
                )
                current = OrderState.SUBMITTED
                new_state = _resolve()
            else:
                raise
        self.active_orders[client_order_id] = new_state
        if fill_qty is not None:
            self.order_fills[client_order_id] = (
                self.order_fills.get(client_order_id, 0.0) + fill_qty
            )
        if self.outbox_store:
            await self.outbox_store.update_status(
                client_order_id, new_state, exchange_order_id
            )
        return new_state

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
        # VA-066: quantize the size onto the venue grid (round DOWN, exchange
        # convention) before the order leaves this process. The engine also
        # quantizes when its instruments map is configured; this is the
        # boundary guard for callers that construct orders directly.
        step_str = self.instruments.get(order.asset) if self.instruments else None
        if step_str is not None:
            grid_qty = float(quantize_to_step(order.position_size, step_str))
            if grid_qty <= 0:
                logger.error(
                    f"[GRID_SIZE_REJECTION] {client_order_id}: quantized size for "
                    f"{order.asset} is zero (size={order.position_size}, "
                    f"step={step_str}) — below venue minimum lot."
                )
                return await self._transition(client_order_id, OrderEventType.REJECTED)
            if grid_qty != order.position_size:
                logger.info(
                    f"[GRID_SIZE_QUANTIZED] {client_order_id}: {order.position_size} -> "
                    f"{grid_qty} on step {step_str} for {order.asset} (VA-066)."
                )
                order = replace(order, position_size=grid_qty)

        # Record the order's OWN identity/side before anything else: fills may
        # only ever be applied against this record (F-0030).
        self.order_records.setdefault(
            client_order_id,
            {
                "asset": order.asset,
                "side": order.side,
                "intended_qty": order.position_size,
            },
        )

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
            return await self._transition(client_order_id, OrderEventType.REJECTED)

        # 2. Shadow Mode Interception Check
        if self.execution_mode is ExecutionMode.SHADOW:
            logger.info(f"[SHADOW_MODE] Intercepting order {client_order_id} for synthetic execution against live order book.")
            if self.shadow_interceptor is not None and order_book is not None:
                shadow_res = await self.shadow_interceptor.execute_shadow_fill(order, client_order_id, order_book)
                if shadow_res.filled_qty > 0:
                    return await self._transition(
                        client_order_id,
                        OrderEventType.FILL,
                        fill_qty=shadow_res.filled_qty,
                        fill_price=shadow_res.shadow_fill_price,
                        exchange_order_id=f"shadow_{client_order_id}",
                    )
                return await self._transition(client_order_id, OrderEventType.REJECTED)
            # No book available: legacy fallback treats the intent as filled.
            logger.warning(
                f"[SHADOW_MODE] No shadow interceptor/order book for {client_order_id}; "
                "applying legacy unconditional shadow fill."
            )
            return await self._transition(
                client_order_id,
                OrderEventType.FILL,
                fill_qty=order.position_size,
                exchange_order_id=f"shadow_{client_order_id}",
            )

        # 3. Standard Live/Paper Idempotency check: check if order already exists on exchange
        existing = await self.venue_adapter.fetch_order(client_order_id, order.asset)
        if existing is not None:
            status = existing.get("status")
            if status == "open":
                return await self._transition(client_order_id, OrderEventType.ACKED)
            return await self._transition(
                client_order_id,
                OrderEventType.FILL,
                fill_qty=order.position_size,
            )

        # 4. Submit new order to exchange venue
        try:
            res = await self.venue_adapter.create_order(order, client_order_id)
            ex_id = res.get("id")
            return await self._transition(
                client_order_id, OrderEventType.SUBMITTED, exchange_order_id=ex_id
            )
        except Exception as e:
            await self._transition(client_order_id, OrderEventType.REJECTED)
            raise e

    # ------------------------------------------------------------------
    # Fill application (F-0030): unknown ids are REJECTED, never defaulted.
    # ------------------------------------------------------------------
    async def apply_fill(
        self,
        client_order_id: str,
        fill_qty: float,
        fill_price: float,
        timestamp: Optional[Any] = None,
    ) -> OrderFill:
        """Apply an exchange fill against the order's own tracked record.

        - Unknown/untracked client_order_id => logged + UnknownOrderFillError;
          NO position delta is computed, let alone applied (F-0030).
        - Direction comes exclusively from the order's recorded side; there is
          no default and none is invented.
        - Partial quantities move the order to PARTIALLY_FILLED (F-0274);
          cumulative qty >= intended moves it to FILLED.
        """
        record = self.order_records.get(client_order_id)
        if record is None or client_order_id not in self.active_orders:
            logger.error(
                f"[ORPHAN_FILL_REJECTED] Fill for unknown client_order_id "
                f"{client_order_id!r} (qty={fill_qty} @ {fill_price}) rejected; "
                "no position delta applied."
            )
            raise UnknownOrderFillError(client_order_id)

        side = record["side"]
        asset = record["asset"]
        direction = 1.0 if side is Side.LONG else -1.0
        delta = direction * fill_qty

        new_state = await self._transition(
            client_order_id,
            OrderEventType.FILL,
            fill_qty=fill_qty,
            fill_price=fill_price,
        )

        self.positions[asset] = self.positions.get(asset, 0.0) + delta
        logger.info(
            f"[FILL_APPLIED] {client_order_id} ({asset}, {side.value}): "
            f"+{fill_qty} @ {fill_price} -> state={new_state.value}, "
            f"filled={self.order_fills[client_order_id]}, net_position={self.positions[asset]}"
        )
        return OrderFill(
            client_order_id=client_order_id,
            asset=asset,
            side=side,
            fill_qty=fill_qty,
            fill_price=fill_price,
            position_delta=delta,
            net_position=self.positions[asset],
            state=new_state,
        )

    # ------------------------------------------------------------------
    # Cancellation (F-0031): partial-cancel keeps filled_qty, lands CANCELED.
    # ------------------------------------------------------------------
    async def cancel_order(self, client_order_id: str) -> OrderState:
        """Cancel a working order.

        - Unknown client_order_id => KeyError (never invents state).
        - Terminal states => IllegalTransitionError (e.g. cancel-after-fill).
        - A partially-filled order becomes CANCELED with its cumulative
          filled_qty preserved; it NEVER resolves to FILLED (F-0031).
        """
        current = self.active_orders.get(client_order_id)
        if current is None:
            logger.error(
                f"[CANCEL_UNKNOWN_ORDER] Cancel requested for unknown client_order_id {client_order_id!r}."
            )
            raise KeyError(client_order_id)

        new_state = transition_order_state(current, OrderEventType.CANCEL)
        self.active_orders[client_order_id] = new_state
        if self.outbox_store:
            await self.outbox_store.update_status(client_order_id, new_state)
        filled = self.order_fills.get(client_order_id, 0.0)
        logger.info(
            f"[ORDER_CANCELED] {client_order_id}: {current.value} -> {new_state.value} "
            f"(filled_qty preserved: {filled})"
        )
        return new_state

    def get_filled_qty(self, client_order_id: str) -> float:
        """Cumulative executed quantity for an order (survives cancellation)."""
        return self.order_fills.get(client_order_id, 0.0)

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
        if client_order_id not in self.order_records:
            # Late external finalization (e.g. ops drill / reconciler replay):
            # tolerated with a loud log because it touches no positions --
            # unlike fills, which must be strictly known-id (F-0030).
            logger.warning(
                f"[FINALIZE_UNKNOWN_ORDER] finalize_partial_fill for untracked "
                f"{client_order_id!r}; registering as PARTIAL_FILL_FINALIZED."
            )
            self.order_records[client_order_id] = {
                "asset": asset,
                "intended_qty": intended_qty,
            }
            self.active_orders.setdefault(client_order_id, OrderState.SUBMITTED)
        current = self.active_orders.get(client_order_id)
        if current is None:
            current = OrderState.SUBMITTED
            self.active_orders[client_order_id] = current

        # Replay the aggregate partial fill through the transition table so the
        # path to PARTIALLY_FILLED is itself legal:
        #   fresh order:  SUBMITTED -> (FILL partial) -> PARTIALLY_FILLED
        #                 -> PARTIAL_FILL_FINALIZED
        # This also encodes F-0031's late-fill-after-cancel race: a cancel that
        # landed first leaves CANCELED, which is terminal -- the late aggregate
        # is NOT resurrected to FILLED; the deviation is still recorded.
        if current in (OrderState.CREATED, OrderState.SUBMITTED):
            current = await self._transition(
                client_order_id,
                OrderEventType.FILL,
                fill_qty=filled_qty,
            )

        try:
            new_state = transition_order_state(
                current, OrderEventType.PARTIAL_FILL_FINALIZED
            )
        except IllegalTransitionError:
            # e.g. the replay resolved to FILLED (qty covered intent), or the
            # order was already terminal. State stays as-is; deviation below
            # still recorded.
            logger.warning(
                f"[FINALIZE_STATE_KEPT] {client_order_id} in {current.value} cannot "
                "accept PARTIAL_FILL_FINALIZED; keeping current state."
            )
            new_state = current
        self.active_orders[client_order_id] = new_state
        self.order_fills[client_order_id] = max(
            self.order_fills.get(client_order_id, 0.0), filled_qty
        )
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
