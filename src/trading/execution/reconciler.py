"""Reconciler watchdog for state synchronization and quarantine handling."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from trading.execution.venue_adapter import VenueAdapter

__all__ = [
    "QuarantineReason",
    "QuarantineItem",
    "StateReconciler",
]


class QuarantineReason(Enum):
    UNKNOWN_ORDER = "unknown_order"
    ORPHAN_FILL = "orphan_fill"
    POSITION_MISMATCH = "position_mismatch"


@dataclass
class QuarantineItem:
    item_id: str
    reason: QuarantineReason
    details: str
    timestamp: datetime


class StateReconciler:
    """Monitors alignment between venue exchange state and local OMS database."""

    def __init__(self, venue_adapter: VenueAdapter, reconcile_interval_sec: float = 60.0):
        self.venue_adapter = venue_adapter
        self.reconcile_interval_sec = reconcile_interval_sec
        self.quarantine_items: List[QuarantineItem] = []

    def has_quarantine(self) -> bool:
        """Returns True if system is in quarantine mode (blocking new ApprovedOrders)."""
        return len(self.quarantine_items) > 0

    async def reconcile_once(self, local_positions: Dict[str, float]) -> List[QuarantineItem]:
        """Runs a single reconciliation cycle against exchange state."""
        try:
            exchange_positions = await self.venue_adapter.fetch_positions()
        except Exception as e:
            # Venue query error triggers temporary quarantine safety
            item = QuarantineItem(
                item_id="venue_connectivity",
                reason=QuarantineReason.UNKNOWN_ORDER,
                details=f"Connectivity error during reconciliation: {str(e)}",
                timestamp=datetime.now(timezone.utc),
            )
            self.quarantine_items.append(item)
            return self.quarantine_items

        ex_map = {pos["symbol"]: pos["amount"] for pos in exchange_positions if "symbol" in pos}

        # Check for mismatches
        all_assets = set(local_positions.keys()).union(ex_map.keys())
        for asset in all_assets:
            local_qty = local_positions.get(asset, 0.0)
            ex_qty = ex_map.get(asset, 0.0)

            if abs(local_qty - ex_qty) > 1e-6:
                item = QuarantineItem(
                    item_id=f"pos_{asset}",
                    reason=QuarantineReason.POSITION_MISMATCH,
                    details=f"Asset {asset}: local={local_qty}, exchange={ex_qty}",
                    timestamp=datetime.now(timezone.utc),
                )
                self.quarantine_items.append(item)

        return self.quarantine_items

    def clear_quarantine(self) -> None:
        """Clears quarantine after manual/automatic resolution."""
        self.quarantine_items.clear()
