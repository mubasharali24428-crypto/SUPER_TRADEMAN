"""Synthetic Exchange Venue Adapter for Local Multi-Agent LOB Simulation."""

import time
from typing import Any, Dict, List, Optional

from trading.execution.venue_adapter import InstrumentInfo, VenueAdapter
from trading.observability.logger import get_logger
from trading.risk.models import (_ISSUER, ApprovedExit, ApprovedOrder,
                                 Position, Side)
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
        # Cumulative filled quantity per client_order_id, tracked venue-side:
        # ApprovedOrder is a frozen dataclass with no fill fields of its own.
        self.filled_qty_by_order: Dict[str, float] = {}
        self.instrument_info_map: Dict[str, InstrumentInfo] = {}

    async def submit_approved_order(
        self,
        approved_order: ApprovedOrder,
        client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Routes ApprovedOrder to SyntheticLOB."""
        if client_order_id is None:
            # ApprovedOrder carries no id of its own; fall back to a deterministic
            # venue-side id derived from its immutable fields.
            client_order_id = f"ord_{approved_order.asset}_{int(approved_order.entry_price * 100)}_{id(approved_order) % 10**8}"
        self.open_orders[client_order_id] = approved_order
        now_ms = time.time() * 1000.0

        # The LOB matcher branches on "buy"/"sell"; Side values are "long"/"short".
        lob_side = "buy" if approved_order.side == Side.LONG else "sell"

        limit_ord = LimitOrder(
            order_id=client_order_id,
            side=lob_side,
            price=approved_order.entry_price,
            qty=approved_order.position_size,
            timestamp_ms=now_ms,
            agent_id="SUPER_TRADEMAN",
        )

        fills = self.lob.place_order(limit_ord)
        filled_qty = sum(f["qty"] for f in fills)

        if filled_qty > 0:
            self.fill_history.extend(fills)
            self.filled_qty_by_order[client_order_id] = (
                self.filled_qty_by_order.get(client_order_id, 0.0) + filled_qty
            )
            # Update position
            self.open_positions[approved_order.asset] = Position(
                asset=approved_order.asset,
                asset_class=approved_order.asset_class,
                side=approved_order.side,
                entry_price=approved_order.entry_price,
                stop_price=approved_order.stop_price,
                risk_pct=approved_order.risk_pct,
                position_size=filled_qty,
            )

        status = (
            "FILLED"
            if filled_qty >= approved_order.position_size
            else ("PARTIAL" if filled_qty > 0 else "SUBMITTED")
        )
        return {
            "client_order_id": client_order_id,
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

        logger.info(
            f"[SYNTHETIC_EXIT_EXECUTED] Asset {approved_exit.asset}, Qty {exit_qty}, Price {exit_price}"
        )
        return {
            "status": "FILLED",
            "asset": approved_exit.asset,
            "filled_qty": exit_qty,
            "price": exit_price,
            "fills": fills,
        }

    async def create_order(
        self, order: ApprovedOrder, client_order_id: str
    ) -> Dict[str, Any]:
        """VenueAdapter protocol: submit an ApprovedOrder under a caller-supplied id."""
        result = await self.submit_approved_order(
            approved_order=order, client_order_id=client_order_id
        )
        return {
            "id": f"syn_{client_order_id}",
            "clientOrderId": client_order_id,
            **result,
        }

    async def create_exit(
        self, exit_order: ApprovedExit, client_order_id: str
    ) -> Dict[str, Any]:
        """VenueAdapter protocol: submit an ApprovedExit under a caller-supplied id."""
        result = await self.submit_approved_exit(exit_order)
        return {
            "id": f"syn_exit_{client_order_id}",
            "clientOrderId": client_order_id,
            **result,
        }

    async def cancel_order(self, client_order_id: str, symbol: str) -> Dict[str, Any]:
        """VenueAdapter protocol: cancel a resting order in the SyntheticLOB."""
        self.open_orders.pop(client_order_id, None)
        cancelled = self.lob.cancel_order(client_order_id)
        return {
            "clientOrderId": client_order_id,
            "symbol": symbol,
            "status": "canceled" if cancelled else "not_found",
        }

    async def fetch_order(
        self, client_order_id: str, symbol: str
    ) -> Optional[Dict[str, Any]]:
        """VenueAdapter protocol: query order status by client_order_id."""
        order = self.open_orders.get(client_order_id)
        if order is None:
            return None
        filled_qty = self.filled_qty_by_order.get(client_order_id, 0.0)
        if filled_qty <= 0.0:
            status = "open"
        elif filled_qty < order.position_size:
            status = "partially_filled"
        else:
            status = "filled"
        return {
            "clientOrderId": client_order_id,
            "symbol": symbol,
            "status": status,
            "filled_qty": filled_qty,
        }

    async def fetch_positions(self) -> List[Dict[str, Any]]:
        return [
            {
                "symbol": p.asset,
                "amount": p.position_size if p.side == Side.LONG else -p.position_size,
                "entry_price": p.entry_price,
            }
            for p in self.open_positions.values()
        ]

    async def fetch_open_orders(
        self, symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        return [
            {
                "client_order_id": cid,
                "symbol": o.asset,
                "amount": o.position_size,
            }
            for cid, o in self.open_orders.items()
            if symbol is None or o.asset == symbol
        ]

    async def get_instrument_info(self, symbol: str) -> InstrumentInfo:
        """VenueAdapter protocol: instrument filters for the synthetic book."""
        return self.instrument_info_map.get(symbol, InstrumentInfo(symbol=symbol))

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        b, a = self.lob.get_best_bid_ask()
        return {
            "symbol": symbol,
            "bid": b,
            "ask": a,
            "last": self.lob.current_price,
            "micro_price": self.lob.get_micro_price(),
        }
