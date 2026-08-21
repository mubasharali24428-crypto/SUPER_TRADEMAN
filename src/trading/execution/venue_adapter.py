"""Abstract and concrete venue adapters for normalized exchange interactions."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from trading.risk.models import ApprovedExit, ApprovedOrder, Side

__all__ = ["InstrumentInfo", "VenueAdapter", "MockVenueAdapter"]


@dataclass(frozen=True)
class InstrumentInfo:
    symbol: str
    min_qty: float = 0.001
    min_notional: float = 10.0
    step_size: float = 0.0001
    price_precision: int = 2


class VenueAdapter(ABC):
    """Abstract adapter defining venue interaction contracts."""

    @abstractmethod
    async def create_order(self, order: ApprovedOrder, client_order_id: str) -> Dict[str, Any]:
        """Submits an ApprovedOrder to the exchange with client_order_id."""
        pass

    @abstractmethod
    async def create_exit(self, exit_order: ApprovedExit, client_order_id: str) -> Dict[str, Any]:
        """Submits an ApprovedExit to the exchange with client_order_id."""
        pass

    @abstractmethod
    async def cancel_order(self, client_order_id: str, symbol: str) -> Dict[str, Any]:
        """Cancels an existing order by client_order_id."""
        pass

    @abstractmethod
    async def fetch_order(self, client_order_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Queries order status on exchange by client_order_id."""
        pass

    @abstractmethod
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> list[Dict[str, Any]]:
        """Queries active open orders from the exchange."""
        pass

    @abstractmethod
    async def fetch_positions(self) -> list[Dict[str, Any]]:
        """Queries current open position balances from exchange."""
        pass

    @abstractmethod
    async def get_instrument_info(self, symbol: str) -> InstrumentInfo:
        """Retrieves exchange instrument filters (min_qty, min_notional, step_size)."""
        pass


class MockVenueAdapter(VenueAdapter):
    """In-memory mock venue adapter for testing and paper trading."""

    def __init__(self, instrument_info_map: Optional[Dict[str, InstrumentInfo]] = None):
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.instrument_info_map = instrument_info_map or {}

    async def create_order(self, order: ApprovedOrder, client_order_id: str) -> Dict[str, Any]:
        resp = {
            "id": f"ex_{client_order_id}",
            "clientOrderId": client_order_id,
            "symbol": order.asset,
            "side": order.side.value,
            "price": order.entry_price,
            "amount": order.position_size,
            "status": "open",
        }
        self.orders[client_order_id] = resp
        return resp

    async def create_exit(self, exit_order: ApprovedExit, client_order_id: str) -> Dict[str, Any]:
        resp = {
            "id": f"ex_exit_{client_order_id}",
            "clientOrderId": client_order_id,
            "symbol": exit_order.asset,
            "status": "closed",
        }
        self.orders[client_order_id] = resp
        return resp

    async def cancel_order(self, client_order_id: str, symbol: str) -> Dict[str, Any]:
        if client_order_id in self.orders:
            self.orders[client_order_id]["status"] = "canceled"
            return self.orders[client_order_id]
        resp = {"id": f"ex_cancel_{client_order_id}", "clientOrderId": client_order_id, "status": "canceled"}
        self.orders[client_order_id] = resp
        return resp

    async def fetch_order(self, client_order_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        return self.orders.get(client_order_id)

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> list[Dict[str, Any]]:
        return [o for o in self.orders.values() if o.get("status") in ("open", "submitted")]

    async def fetch_positions(self) -> list[Dict[str, Any]]:
        return list(self.positions.values())

    async def get_instrument_info(self, symbol: str) -> InstrumentInfo:
        return self.instrument_info_map.get(symbol, InstrumentInfo(symbol=symbol))

