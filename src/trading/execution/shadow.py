"""Shadow Mode Interceptor (The Ghost Protocol).

SOCRATIC GUARDRAIL ANSWER 2:
In Shadow Mode, if the real exchange undergoes a sudden flash crash and the L2 order book
empties out (insufficient depth for requested ApprovedOrder size), ShadowInterceptor walks
all available book levels up to exhaustion. It fills the available quantity at the volume-weighted
depth price, applies Almgren-Chriss impact, logs a LIQUIDITY_DEFICIT_SHADOW_FILL alert/event,
and marks the remaining quantity as unfilled (partially filled/capped). It NEVER forces a fill
at an arbitrary price beyond available book depth.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Tuple

from trading.execution.tca import calculate_market_impact
from trading.observability.logger import get_logger
from trading.risk.models import ApprovedOrder, Side

__all__ = [
    "L2OrderBookSnapshot",
    "ShadowFillResult",
    "ShadowInterceptor",
]

logger = get_logger("trading.execution.shadow")


@dataclass(frozen=True)
class L2OrderBookSnapshot:
    symbol: str
    bids: List[Tuple[float, float]]  # (price, qty) sorted descending by price
    asks: List[Tuple[float, float]]  # (price, qty) sorted ascending by price
    timestamp_ms: float


@dataclass(frozen=True)
class ShadowFillResult:
    client_order_id: str
    asset: str
    side: Side
    requested_qty: float
    filled_qty: float
    shadow_fill_price: float
    slippage_pct: float
    was_capped: bool
    reason: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ShadowInterceptor:
    """Intercepts execution in SHADOW mode, simulating fills against live L2 order books."""

    def __init__(self, max_participation_pct: float = 0.03):
        self.max_participation_pct = max_participation_pct
        self.shadow_fills: List[ShadowFillResult] = []

    async def execute_shadow_fill(
        self,
        order: ApprovedOrder,
        client_order_id: str,
        order_book: L2OrderBookSnapshot,
        adv_notional: float = 1_000_000.0,
    ) -> ShadowFillResult:
        """Simulates order book execution without invoking venue_adapter.create_order()."""
        requested_qty = order.position_size
        levels = order_book.asks if order.side is Side.LONG else order_book.bids

        if not levels:
            logger.error(f"[SHADOW_INTERCEPTOR] Empty order book for {order.asset}. Zero liquidity available.")
            res = ShadowFillResult(
                client_order_id=client_order_id,
                asset=order.asset,
                side=order.side,
                requested_qty=requested_qty,
                filled_qty=0.0,
                shadow_fill_price=order.entry_price,
                slippage_pct=0.0,
                was_capped=True,
                reason="LIQUIDITY_DEFICIT_EMPTY_BOOK",
            )
            self.shadow_fills.append(res)
            return res

        # Walk the order book tiers
        remaining_qty = requested_qty
        accumulated_cost = 0.0
        filled_qty = 0.0

        for price, depth in levels:
            take_qty = min(remaining_qty, depth)
            accumulated_cost += take_qty * price
            filled_qty += take_qty
            remaining_qty -= take_qty
            if remaining_qty <= 0:
                break

        vwap_price = accumulated_cost / filled_qty if filled_qty > 0 else order.entry_price
        was_liquidity_capped = remaining_qty > 0

        if was_liquidity_capped:
            logger.warning(
                f"[SHADOW_INTERCEPTOR] LIQUIDITY_DEFICIT_SHADOW_FILL: {order.asset} order size {requested_qty} "
                f"exceeds book depth. Filled {filled_qty} @ VWAP {vwap_price:.4f}."
            )

        # Apply Almgren-Chriss market impact model
        order_notional = filled_qty * vwap_price
        impact_pct, _, impact_capped = calculate_market_impact(
            order_notional=order_notional,
            adv_notional=adv_notional,
            max_participation_pct=self.max_participation_pct,
        )

        sign = 1.0 if order.side is Side.LONG else -1.0
        shadow_fill_price = vwap_price * (1.0 + sign * impact_pct)
        slippage_pct = abs(shadow_fill_price - order.entry_price) / order.entry_price

        reason = "SHADOW_FILL"
        if was_liquidity_capped or impact_capped:
            reason = "LIQUIDITY_DEFICIT_SHADOW_FILL"

        res = ShadowFillResult(
            client_order_id=client_order_id,
            asset=order.asset,
            side=order.side,
            requested_qty=requested_qty,
            filled_qty=filled_qty,
            shadow_fill_price=shadow_fill_price,
            slippage_pct=slippage_pct,
            was_capped=(was_liquidity_capped or impact_capped),
            reason=reason,
        )
        self.shadow_fills.append(res)
        return res
