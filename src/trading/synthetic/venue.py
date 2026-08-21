"""Synthetic Exchange Venue Adapter for Local Multi-Agent LOB Simulation."""

import time
from typing import Any, Dict, List, Optional

from trading.execution.venue_adapter import VenueAdapter
from trading.observability.logger import get_logger
from trading.risk.models import ApprovedExit, ApprovedOrder, Position, Side, _ISSUER
from trading.synthetic.lob import LimitOrder, SyntheticLOB

__all__ = ["SyntheticVenue"]

logger = get_logger("trading.synthetic.venue")


class SyntheticVenue(VenueAdapter):
    """In-memory venue adapter wrapping SyntheticLOB for zero-latency simulation."""

    def __init__(self, lob: Optional[SyntheticLOB] = None):
        self.lob = lob or SyntheticLOB()
        self.open_orders: Dict[str, ApprovedOrder] = {}
        self.open_positions: Dict[str, Position] = {}
        self.fill_history: List[Dict[str, Any]] = []

    async def submit_approved_order(self, approved_order: ApprovedOrder) -> Dict[str, Any]:
        """Routes ApprovedOrder to SyntheticLOB."""
        self.open_orders[approved_order.client_order_id] = approved_order
        now_ms = time.time() * 1000.0

        limit_ord = LimitOrder(
            order_id=approved_order.client_order_id,
            side=approved_order.side.value,
            price=approved_order.entry_price,
            qty=approved_order.position_size,
            timestamp_ms=now_ms,
            agent_id="SUPER_TRADEMAN",
        )

        fills = self.lob.place_order(limit_ord)
        filled_qty = sum(f["qty"] for f in fills)

        if filled_qty > 0:
            self.fill_history.extend(fills)
            # Update position
            self.open_positions[approved_order.asset] = Position(
                asset=approved_order.asset,
                side=approved_order.side,
                entry_price=approved_order.entry_price,
                stop_price=approved_order.stop_price,
                risk_pct=approved_order.risk_pct,
                position_size=filled_qty,
            )

        status = "FILLED" if filled_qty >= approved_order.position_size else ("PARTIAL" if filled_qty > 0 else "SUBMITTED")
        return {
            "client_order_id": approved_order.client_order_id,
            "status": status,
            "filled_qty": filled_qty,
            "fills": fills,
        }

    async def submit_approved_exit(self, approved_exit: ApprovedExit) -> Dict[str, Any]:
        """Executes emergency or strategy exit against SyntheticLOB."""
        best_bid, best_ask = self.lob.get_best_bid_ask()
        pos = self.open_positions.pop(approved_exit.asset, None)

        exit_qty = pos.position_size if pos else 1.0
        exit_side = "sell" if (pos and pos.side == Side.LONG) else "buy"
        exit_price = best_bid if exit_side == "sell" else best_ask

        fills = self.lob.place_order(
            LimitOrder(
                order_id=f"exit_{approved_exit.asset}",
                side=exit_side,
                price=exit_price,
                qty=exit_qty,
                timestamp_ms=time.time() * 1000.0,
                agent_id="SUPER_TRADEMAN_EXIT",
            )
        )

        logger.info(f"[SYNTHETIC_EXIT_EXECUTED] Asset {approved_exit.asset}, Qty {exit_qty}, Price {exit_price}")
        return {
            "status": "FILLED",
            "asset": approved_exit.asset,
            "filled_qty": exit_qty,
            "price": exit_price,
            "fills": fills,
        }

    async def fetch_positions(() -> List[Dict[str, Any]]:
        pass

    async def fetch_positions(self) -> List[Dict[str, Any]]:
        return [
            {
                "symbol": p.asset,
                "amount": p.position_size if p.side == Side.LONG else -p.position_size,
                "entry_price": p.entry_price,
            }
            for p in self.open_positions.values()
        ]

    async def fetch_open_orders(self) -> List[Dict[str, Any]]:
        return [
            {
                "client_order_id": o.client_order_id,
                "symbol": o.asset,
                "amount": o.position_size,
            }
            for o in self.open_orders.values()
        ]

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        b, a = self.lob.get_best_bid_ask()
        return {
            "symbol": symbol,
            "bid": b,
            "ask": a,
            "last": self.lob.current_price,
            "micro_price": self.lob.get_micro_price(),
        }
