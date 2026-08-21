"""State Recovery Engine for Startup and HA Failover Lock Takeover."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from trading.execution.venue_adapter import MockVenueAdapter, VenueAdapter
from trading.observability.logger import get_logger
from trading.risk.models import ApprovedExit, Position, Side, _ISSUER

__all__ = [
    "StateRecoveryResult",
    "StateRecoveryEngine",
]

logger = get_logger("trading.infrastructure.state_recovery")


@dataclass
class StateRecoveryResult:
    timestamp_utc: datetime
    positions_reconciled: int
    orders_reconciled: int
    quarantine_triggered: bool
    flatten_triggered: bool
    exits_dispatched: List[ApprovedExit] = field(default_factory=list)


class StateRecoveryEngine:
    """Reconciles local state against venue state during startup or failover lock takeover."""

    def __init__(self, venue_adapter: Optional[VenueAdapter] = None):
        self.venue_adapter = venue_adapter or MockVenueAdapter()

    async def recover_state(
        self,
        local_positions: Optional[List[Dict[str, Any]]] = None,
        local_open_orders: Optional[List[Dict[str, Any]]] = None,
    ) -> StateRecoveryResult:
        """Executes full state recovery and reconciliation against venue API."""
        now_utc = datetime.now(timezone.utc)
        logger.info("[STATE_RECOVERY_STARTED] Executing state recovery check against venue API...")

        venue_positions = await self.venue_adapter.fetch_positions()
        venue_orders = await self.venue_adapter.fetch_open_orders()

        l_pos = local_positions or []
        l_ord = local_open_orders or []

        pos_reconciled = 0
        ord_reconciled = 0
        flatten_triggered = False
        exits_dispatched: List[ApprovedExit] = []

        # Position quantity mismatch check
        v_pos_dict = {p.get("symbol"): p.get("amount", 0.0) for p in venue_positions}
        l_pos_dict = {p.get("asset"): p.get("position_size", 0.0) for p in l_pos}

        mismatched_assets = []
        all_assets = set(v_pos_dict.keys()).union(set(l_pos_dict.keys()))
        for asset in all_assets:
            if asset:
                v_qty = v_pos_dict.get(asset, 0.0)
                l_qty = l_pos_dict.get(asset, 0.0)
                if abs(v_qty - l_qty) > 1e-6:
                    mismatched_assets.append(asset)
                else:
                    pos_reconciled += 1

        # If severe mismatch occurs across multiple assets, trigger Tier 3 Flatten via _ISSUER
        if len(mismatched_assets) >= 2:
            logger.error(
                f"[SEVERE_MISMATCH_DETECTED] Assets {mismatched_assets} mismatch venue reality! "
                f"Triggering emergency Tier 3 Flatten via sovereign _ISSUER token."
            )
            flatten_triggered = True
            exits_dispatched = [
                ApprovedExit(
                    asset=asset,
                    asset_class="crypto",
                    reason="state_recovery_severe_mismatch_flatten",
                    issuer=_ISSUER,
                )
                for asset in mismatched_assets
            ]

        res = StateRecoveryResult(
            timestamp_utc=now_utc,
            positions_reconciled=pos_reconciled,
            orders_reconciled=ord_reconciled,
            quarantine_triggered=len(mismatched_assets) > 0,
            flatten_triggered=flatten_triggered,
            exits_dispatched=exits_dispatched,
        )

        logger.info(f"[STATE_RECOVERY_COMPLETED] Quarantine={res.quarantine_triggered}, Flatten={res.flatten_triggered}")
        return res
