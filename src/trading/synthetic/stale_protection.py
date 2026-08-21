"""Stale Quote Protection Engine for Monitoring Resting Child Orders."""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from trading.observability.logger import get_logger

__all__ = [
    "RestingOrder",
    "StaleQuoteProtection",
]

logger = get_logger("trading.synthetic.stale_protection")


@dataclass
class RestingOrder:
    order_id: str
    side: str
    price: float
    remaining_qty: float
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000.0)
    source: str = "INTERNAL_STRATEGY"
    parent_order_id: Optional[str] = None


class StaleQuoteProtection:
    """Continuously monitors resting child orders and flags quotes that have drifted from micro-price."""

    def __init__(self, sigma_multiplier: float = 1.5, min_distance_bps: float = 5.0):
        self.sigma_multiplier = sigma_multiplier
        self.min_distance_bps = min_distance_bps
        self.monitored_orders: Dict[str, RestingOrder] = {}

    def register_order(self, order: RestingOrder) -> None:
        self.monitored_orders[order.order_id] = order

    def deregister_order(self, order_id: str) -> Optional[RestingOrder]:
        return self.monitored_orders.pop(order_id, None)

    def evaluate(self, current_micro_price: Optional[float], current_sigma: float) -> List[RestingOrder]:
        """Evaluates price drift against micro-price volatility and returns stale orders to cancel."""
        stale_orders: List[RestingOrder] = []

        if current_micro_price is None:
            logger.warning("[STALE_PROTECTION_FALLBACK] Missing micro-price data; skipping evaluation this cycle.")
            return stale_orders

        # Dynamic threshold: max(volatility-based sigma threshold, minimum bps floor)
        volatility_threshold = self.sigma_multiplier * current_sigma
        min_threshold = (self.min_distance_bps / 10000.0) * current_micro_price
        threshold = max(volatility_threshold, min_threshold)

        for order_id, order in list(self.monitored_orders.items()):
            distance_from_micro = abs(order.price - current_micro_price)
            if distance_from_micro > threshold:
                stale_orders.append(order)
                logger.info(
                    f"[STALE_QUOTE_DETECTED] Order {order_id} drifted {distance_from_micro:.2f} > threshold {threshold:.2f} "
                    f"(MicroPrice={current_micro_price:.2f}, OrderPrice={order.price:.2f})."
                )

        return stale_orders
