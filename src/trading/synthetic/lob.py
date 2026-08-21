"""In-Memory Synthetic Limit Order Book (LOB) Engine with Queue Priority, Dynamic Friction, and Endogeneity Tagging."""

import collections
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from trading.observability.logger import get_logger

__all__ = [
    "LimitOrder",
    "SyntheticLOB",
]

logger = get_logger("trading.synthetic.lob")


@dataclass
class LimitOrder:
    order_id: str
    side: str  # "buy" or "sell"
    price: float
    qty: float
    timestamp_ms: float = 0.0
    agent_id: str = "agent_default"
    filled_qty: float = 0.0
    arrival_timestamp_ms: float = 0.0
    priority_timestamp_ms: float = 0.0

    def __post_init__(self):
        if self.timestamp_ms > 0:
            if self.arrival_timestamp_ms == 0.0:
                self.arrival_timestamp_ms = self.timestamp_ms
            if self.priority_timestamp_ms == 0.0:
                self.priority_timestamp_ms = self.timestamp_ms
        else:
            now_ms = time.time() * 1000.0
            if self.arrival_timestamp_ms == 0.0:
                self.arrival_timestamp_ms = now_ms
            if self.priority_timestamp_ms == 0.0:
                self.priority_timestamp_ms = now_ms
            self.timestamp_ms = now_ms

    @property
    def is_internal_strategy(self) -> bool:
        return self.agent_id.startswith("SUPER_TRADEMAN")


class SyntheticLOB:
    """Simulates high-performance limit order book matching, queue priority, and endogeneity-tagged event logs."""

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_price: float = 50000.0,
        base_fill_probability: float = 0.85,
    ):
        self.symbol = symbol
        self.current_price = initial_price
        self.base_fill_probability = base_fill_probability
        self.bids: List[LimitOrder] = []  # Sorted descending by price, then ascending by priority_timestamp_ms
        self.asks: List[LimitOrder] = []  # Sorted ascending by price, then ascending by priority_timestamp_ms
        self.event_log: Deque[Dict[str, Any]] = collections.deque(maxlen=10000)

        # Seed initial depth around initial_price
        self._seed_initial_book()

    def _seed_initial_book(self) -> None:
        now_ms = time.time() * 1000.0
        self.bids = [
            LimitOrder("b1", "buy", self.current_price - 1.0, 2.5, now_ms, "mm_init"),
            LimitOrder("b2", "buy", self.current_price - 5.0, 5.0, now_ms, "mm_init"),
            LimitOrder("b3", "buy", self.current_price - 10.0, 10.0, now_ms, "mm_init"),
        ]
        self.asks = [
            LimitOrder("a1", "sell", self.current_price + 1.0, 2.5, now_ms, "mm_init"),
            LimitOrder("a2", "sell", self.current_price + 5.0, 5.0, now_ms, "mm_init"),
            LimitOrder("a3", "sell", self.current_price + 10.0, 10.0, now_ms, "mm_init"),
        ]

    def get_best_bid_ask(self) -> Tuple[float, float]:
        best_bid = self.bids[0].price if self.bids else self.current_price - 1.0
        best_ask = self.asks[0].price if self.asks else self.current_price + 1.0
        return best_bid, best_ask

    def get_effective_fill_probability(self, stress_score: float = 0.0) -> float:
        """Scales fill probability dynamically from base_fill_probability down under stress."""
        if self.base_fill_probability <= 0.0:
            return 0.0
        clamped_stress = max(0.0, min(1.0, stress_score))
        return max(0.20, self.base_fill_probability - (0.25 * clamped_stress))

    def get_micro_price(self) -> float:
        """Calculates volume-weighted micro-price with exponential depth decay over top 3 levels."""
        if not self.bids or not self.asks:
            return self.current_price

        best_bid = self.bids[0].price
        best_ask = self.asks[0].price

        # Calculate decayed volume across top 3 levels (lambda = 0.5)
        total_weighted_bid_vol = 0.0
        for d in range(min(3, len(self.bids))):
            avail_v = max(0.0, self.bids[d].qty - self.bids[d].filled_qty)
            total_weighted_bid_vol += avail_v * math.exp(-0.5 * d)

        total_weighted_ask_vol = 0.0
        for d in range(min(3, len(self.asks))):
            avail_v = max(0.0, self.asks[d].qty - self.asks[d].filled_qty)
            total_weighted_ask_vol += avail_v * math.exp(-0.5 * d)

        total_vol = total_weighted_bid_vol + total_weighted_ask_vol
        if total_vol <= 0.0:
            return (best_bid + best_ask) / 2.0

        return (best_bid * total_weighted_ask_vol + best_ask * total_weighted_bid_vol) / total_vol

    def place_order(self, order: LimitOrder, stress_score: float = 0.0) -> List[Dict[str, Any]]:
        """Processes limit/market order placement with queue friction and returns fill events."""
        fills = []
        fill_prob = self.get_effective_fill_probability(stress_score)
        slip_penalty = 50.0 if stress_score < 0.70 else 75.0

        source_tag = "INTERNAL_STRATEGY" if order.is_internal_strategy else "EXTERNAL_MARKET"

        if order.side == "buy":
            while self.asks and order.filled_qty < order.qty:
                best_ask = self.asks[0]
                if order.price >= best_ask.price:
                    # Stochastic queue slip friction check
                    if random.random() > fill_prob:
                        logger.debug(f"[FRICTION] Order {order.order_id} experienced queue slip (penalty +{slip_penalty}ms).")
                        order.priority_timestamp_ms += slip_penalty
                        break

                    matched_qty = min(order.qty - order.filled_qty, best_ask.qty - best_ask.filled_qty)
                    order.filled_qty += matched_qty
                    best_ask.filled_qty += matched_qty
                    self.current_price = best_ask.price
                    now_ms = time.time() * 1000.0

                    fill_event = {
                        "fill_id": f"fill_{int(now_ms)}",
                        "buy_order_id": order.order_id,
                        "sell_order_id": best_ask.order_id,
                        "price": best_ask.price,
                        "qty": matched_qty,
                        "side": "buy",
                        "source": source_tag,
                    }
                    fills.append(fill_event)

                    self.event_log.append({
                        "event_type": "trade",
                        "side": order.side,
                        "price": best_ask.price,
                        "qty": matched_qty,
                        "timestamp_ms": now_ms,
                        "source": source_tag,
                    })

                    if best_ask.filled_qty >= best_ask.qty:
                        self.asks.pop(0)
                else:
                    break

            if order.filled_qty < order.qty and order.price > 0:
                self.bids.append(order)
                self.bids.sort(key=lambda o: (-o.price, o.priority_timestamp_ms))

        elif order.side == "sell":
            while self.bids and order.filled_qty < order.qty:
                best_bid = self.bids[0]
                if order.price <= best_bid.price:
                    # Stochastic queue slip friction check
                    if random.random() > fill_prob:
                        logger.debug(f"[FRICTION] Order {order.order_id} experienced queue slip (penalty +{slip_penalty}ms).")
                        order.priority_timestamp_ms += slip_penalty
                        break

                    matched_qty = min(order.qty - order.filled_qty, best_bid.qty - best_bid.filled_qty)
                    order.filled_qty += matched_qty
                    best_bid.filled_qty += matched_qty
                    self.current_price = best_bid.price
                    now_ms = time.time() * 1000.0

                    fill_event = {
                        "fill_id": f"fill_{int(now_ms)}",
                        "buy_order_id": best_bid.order_id,
                        "sell_order_id": order.order_id,
                        "price": best_bid.price,
                        "qty": matched_qty,
                        "side": "sell",
                        "source": source_tag,
                    }
                    fills.append(fill_event)

                    self.event_log.append({
                        "event_type": "trade",
                        "side": order.side,
                        "price": best_bid.price,
                        "qty": matched_qty,
                        "timestamp_ms": now_ms,
                        "source": source_tag,
                    })

                    if best_bid.filled_qty >= best_bid.qty:
                        self.bids.pop(0)
                else:
                    break

            if order.filled_qty < order.qty and order.price > 0:
                self.asks.append(order)
                self.asks.sort(key=lambda o: (o.price, o.priority_timestamp_ms))

        return fills

    def cancel_order(self, order_id: str) -> bool:
        """Cancels an order in bids or asks queue and logs cancel event with source tagging."""
        now_ms = time.time() * 1000.0

        for i, o in enumerate(self.bids):
            if o.order_id == order_id:
                source_tag = "INTERNAL_STRATEGY" if o.is_internal_strategy else "EXTERNAL_MARKET"
                self.event_log.append({
                    "event_type": "cancel",
                    "side": o.side,
                    "price": o.price,
                    "qty": max(0.0, o.qty - o.filled_qty),
                    "timestamp_ms": now_ms,
                    "source": source_tag,
                })
                self.bids.pop(i)
                return True

        for i, o in enumerate(self.asks):
            if o.order_id == order_id:
                source_tag = "INTERNAL_STRATEGY" if o.is_internal_strategy else "EXTERNAL_MARKET"
                self.event_log.append({
                    "event_type": "cancel",
                    "side": o.side,
                    "price": o.price,
                    "qty": max(0.0, o.qty - o.filled_qty),
                    "timestamp_ms": now_ms,
                    "source": source_tag,
                })
                self.asks.pop(i)
                return True

        return False
