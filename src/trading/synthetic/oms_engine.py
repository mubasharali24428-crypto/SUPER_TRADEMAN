"""OMS Execution Engine, Actor Event Loop, Priority Queue, and Air-Gap Reconciler."""

import heapq
import itertools
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from trading.observability.logger import get_logger

__all__ = [
    "OMSEventTier",
    "OrderStatus",
    "OMSEvent",
    "ExchangeMessage",
    "AtomicMailbox",
    "OMSActorEngine",
]

logger = get_logger("trading.synthetic.oms_engine")


class OMSEventTier(int, Enum):
    TIER_0_SAFETY = 0       # STALE_QUOTE_DETECTED, EMERGENCY_CANCEL, KILL_SWITCH, TOXIC_FLOW
    TIER_1_STATE = 1        # REGIME_SHIFT, DEFENSE_TRIGGER, FORCE_STATE_SYNC
    TIER_2_EXECUTION = 2    # SNIPER_ENTRY, PASSIVE_QUOTE, IOC_SWEEP


class OrderStatus(str, Enum):
    NEW = "NEW"
    ACTIVE = "ACTIVE"
    PENDING_CANCEL = "PENDING_CANCEL"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass(order=True)
class OMSEvent:
    priority: int
    sequence: int
    event_type: str = field(compare=False)
    payload: Dict[str, Any] = field(compare=False)
    timestamp_ms: float = field(compare=False, default_factory=lambda: time.time() * 1000.0)


@dataclass
class ExchangeMessage:
    seq_num: int
    order_id: str
    msg_type: str  # "FILL", "CANCEL_CONFIRMED", "CANCEL_REJECTED", "ORDER_ACK"
    qty: float
    price: float
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000.0)


class AtomicMailbox:
    """Simulated lock-free atomic mailbox for asynchronous exchange state snapshots."""

    def __init__(self):
        self._box: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()  # Used only to model atomic CAS swap

    def put_state_sync(self, exchange_state: Dict[str, Any]) -> None:
        """Called by background thread to deposit latest exchange reconciliation snapshot."""
        with self._lock:
            self._box = exchange_state

    def drain(self) -> Optional[Dict[str, Any]]:
        """Called by Actor at Safe Boundary to atomically take snapshot without mutex contention."""
        with self._lock:
            snapshot = self._box
            self._box = None
            return snapshot


class OMSActorEngine:
    """Actor-based single-threaded execution engine per symbol enforcing priority queues and reconciliation."""

    def __init__(self, symbol: str = "BTC/USDT", max_discrepancy_pct: float = 0.10):
        self.symbol = symbol
        self.max_discrepancy_pct = max_discrepancy_pct
        
        self.event_pq: List[OMSEvent] = []
        self._counter = itertools.count()
        self.mailbox = AtomicMailbox()
        
        # Local state storage. Private backing dict -- external callers (e.g. the
        # background reconciler thread) only ever get a read-only view via `orders`;
        # mutation must go through the Actor's own event-loop methods.
        self._orders: Dict[str, Dict[str, Any]] = {}
        self.local_position: float = 0.0
        self.processed_events: List[str] = []
        
        # Sequence buffering for out-of-order exchange messages
        self.next_expected_seq_num: int = 1
        self.seq_buffer: Dict[int, ExchangeMessage] = {}
        
        # Circuit breaker state
        self.circuit_breaker_tripped: bool = False
        self.is_halted: bool = False
        self.is_processing_event: bool = False

    @property
    def orders(self) -> Mapping[str, Any]:
        """Read-only view of order state; direct external mutation is rejected."""
        return MappingProxyType(self._orders)

    def enqueue_event(self, tier: OMSEventTier, event_type: str, payload: Dict[str, Any]) -> int:
        """Enqueues event into prioritized heap (Tier 0 > Tier 1 > Tier 2)."""
        seq = next(self._counter)
        event = OMSEvent(
            priority=tier.value,
            sequence=seq,
            event_type=event_type,
            payload=payload,
        )
        heapq.heappush(self.event_pq, event)
        return seq

    def submit_order(self, order_id: str, side: str, price: float, qty: float) -> None:
        self._orders[order_id] = {
            "order_id": order_id,
            "side": side,
            "price": price,
            "qty": qty,
            "filled_qty": 0.0,
            "status": OrderStatus.ACTIVE,
        }

    def request_cancel(self, order_id: str) -> bool:
        """Transitions order to PENDING_CANCEL state machine limbo."""
        if order_id in self._orders:
            order = self._orders[order_id]
            if order["status"] in (OrderStatus.ACTIVE, OrderStatus.NEW):
                order["status"] = OrderStatus.PENDING_CANCEL
                logger.info(f"[OMS_PENDING_CANCEL] Order {order_id} moved to PENDING_CANCEL limbo.")
                return True
        return False

    def format_outgoing_order_message(self, order_id: str) -> Dict[str, Any]:
        """Formats a locally-tracked order as a timestamped outgoing (simulated FIX/WebSocket) wire message."""
        order = self._orders[order_id]
        return {
            "symbol": self.symbol,
            "order_id": order_id,
            "side": order["side"],
            "price": order["price"],
            "qty": order["qty"],
            "msg_type": "NEW_ORDER",
            "timestamp_ms": time.time() * 1000.0,
        }

    def on_exchange_message(self, msg: ExchangeMessage) -> None:
        """Buffers exchange message and processes in strict sequence order (Task 3.4)."""
        self.seq_buffer[msg.seq_num] = msg
        
        # Process contiguous sequence numbers
        while self.next_expected_seq_num in self.seq_buffer:
            curr_msg = self.seq_buffer.pop(self.next_expected_seq_num)
            self._apply_exchange_message(curr_msg)
            self.next_expected_seq_num += 1

    def _apply_exchange_message(self, msg: ExchangeMessage) -> None:
        """Applies sequenced message with late-fill resolution (Task 3.3)."""
        order_id = msg.order_id
        order = self._orders.get(order_id)

        if msg.msg_type == "FILL":
            if order is not None:
                order["filled_qty"] += msg.qty
                if order["status"] == OrderStatus.PENDING_CANCEL:
                    logger.warning(f"[OMS_LATE_FILL_CAPTURED] Caught late fill on PENDING_CANCEL order {order_id} (Qty={msg.qty}).")
                    order["status"] = OrderStatus.FILLED
                elif order["filled_qty"] >= order["qty"]:
                    order["status"] = OrderStatus.FILLED
                else:
                    order["status"] = OrderStatus.PARTIALLY_FILLED

            # Update inventory
            direction = 1.0 if (order and order["side"] == "BUY") else -1.0
            self.local_position += direction * msg.qty

        elif msg.msg_type == "CANCEL_CONFIRMED":
            if order is not None:
                if order["status"] == OrderStatus.FILLED:
                    logger.debug(f"[OMS_DISCARD_CANCEL_ACK] Order {order_id} already filled. Discarding cancel ack.")
                else:
                    order["status"] = OrderStatus.CANCELLED

        elif msg.msg_type == "CANCEL_REJECTED":
            if order is not None and order["status"] == OrderStatus.FILLED:
                logger.debug(f"[OMS_DISCARD_REJECT] Order {order_id} already filled. Discarding cancel reject.")

        elif msg.msg_type == "REJECT":
            if order is not None:
                order["status"] = OrderStatus.REJECTED
                logger.error(f"[OMS_ORDER_REJECTED] Order {order_id} rejected by exchange.")

    def run_event_step(self) -> Optional[OMSEvent]:
        """Runs a single event step, strictly enforcing Safe Boundary reconciler draining."""
        if not self.event_pq:
            # Drain mailbox at safe idle boundary
            self._drain_safe_boundary_reconciler()
            return None

        # Pop highest priority event (Tier 0 first)
        event = heapq.heappop(self.event_pq)
        self.is_processing_event = True

        try:
            self._process_single_event(event)
            self.processed_events.append(event.event_type)
        finally:
            self.is_processing_event = False
            # Drain mailbox strictly at the Safe Boundary AFTER event finishes
            self._drain_safe_boundary_reconciler()

        return event

    def shutdown(self) -> int:
        """Drains every pending queued event before termination, so nothing is lost. Returns the count flushed."""
        flushed = 0
        while self.event_pq:
            self.run_event_step()
            flushed += 1
        return flushed

    def _process_single_event(self, event: OMSEvent) -> None:
        """Executes event according to its type."""
        if event.event_type in ("STALE_QUOTE_DETECTED", "EMERGENCY_CANCEL", "KILL_SWITCH", "TOXIC_FLOW"):
            order_id = event.payload.get("order_id")
            if order_id:
                self.request_cancel(order_id)
        elif event.event_type == "SNIPER_ENTRY":
            if self.is_halted:
                logger.warning(f"[OMS_HALTED_ENTRY_BLOCKED] {self.symbol} is halted; discarding new order generation.")
                return
            order_id = event.payload["order_id"]
            self.submit_order(
                order_id=order_id,
                side=event.payload.get("side", "BUY"),
                price=event.payload.get("price", 50000.0),
                qty=event.payload.get("qty", 1.0),
            )

    def _drain_safe_boundary_reconciler(self) -> None:
        """Drains atomic mailbox and executes delta sync or halts on severe mismatch (Task 3.5)."""
        snapshot = self.mailbox.drain()
        if snapshot is None:
            return

        exchange_pos = snapshot.get("exchange_position", self.local_position)
        delta = abs(exchange_pos - self.local_position)
        reference_base = max(abs(exchange_pos), abs(self.local_position), 1.0)
        discrepancy_pct = delta / reference_base

        if discrepancy_pct > self.max_discrepancy_pct:
            self.circuit_breaker_tripped = True
            self.is_halted = True
            logger.critical(
                f"[OMS_RECONCILE_CIRCUIT_BREAKER] Severe position discrepancy: "
                f"Local={self.local_position:.2f}, Exchange={exchange_pos:.2f} (Delta={discrepancy_pct*100:.1f}% > {self.max_discrepancy_pct*100:.1f}%). HALTING."
            )
        elif delta > 1e-6:
            logger.warning(f"[OMS_DELTA_SYNC] Minor discrepancy adjusted: Local {self.local_position:.2f} -> Exchange {exchange_pos:.2f}.")
            self.local_position = exchange_pos
            self.processed_events.append("DELTA_SYNC")
