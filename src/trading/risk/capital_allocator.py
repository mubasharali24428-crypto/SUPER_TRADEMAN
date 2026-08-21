"""Capital Allocation Engine for Multi-Asset Portfolio Management.

SOCRATIC GUARDRAIL ANSWER 1:
If the CapitalAllocator rejects signals due to capital constraints (e.g. 3 out of 5 signals),
the rejected signals are PERMANENTLY DISCARDED for that bar. They are NOT re-evaluated on subsequent
bars. Re-evaluating deferred signals on subsequent bars risks executing stale entry signals
on outdated market conditions, violating strategy temporal freshness.

SOVEREIGN POSITION SIZING INVARIANT:
The CapitalAllocator either grants FULL capital for the formula-derived size:
    size = (Equity * risk_pct) / |Entry - Stop|
or REJECTS the signal with a CAPITAL_ALLOCATION_REJECTED event.
It NEVER alters, scales down, or outputs a partial position size order.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from trading.observability.logger import get_logger
from trading.risk.models import AccountState, ApprovedOrder, Position

__all__ = [
    "AllocationPriorityScore",
    "CapitalAllocator",
]

logger = get_logger("trading.risk.capital_allocator")


@dataclass
class AllocationPriorityScore:
    asset: str
    signal_strength: float
    vol_adj_return: float
    inverse_correlation: float
    symbol_recency: float
    total_score: float


class CapitalAllocator:
    """Allocates available capital among competing ApprovedOrder signals based on priority scoring."""

    def __init__(
        self,
        max_concurrent_positions: int = 5,
        max_portfolio_exposure_pct: float = 0.50,
        max_single_asset_pct: float = 0.15,
    ):
        self.max_concurrent_positions = max_concurrent_positions
        self.max_portfolio_exposure_pct = max_portfolio_exposure_pct
        self.max_single_asset_pct = max_single_asset_pct
        self.last_trade_time: Dict[str, float] = {}

    def calculate_priority_score(
        self,
        order: ApprovedOrder,
        current_positions: List[Position],
        correlations: Optional[Dict[Tuple[str, str], float]] = None,
        now_ts: float = 0.0,
    ) -> AllocationPriorityScore:
        """Computes priority score components for an ApprovedOrder."""
        # 1. Signal strength proxy (risk-reward distance or price magnitude)
        dist = abs(order.entry_price - order.stop_price)
        signal_strength = abs(order.target_price - order.entry_price) / dist if dist > 0 else 1.0

        # 2. Volatility-adjusted return
        vol_adj_return = order.entry_price / dist if dist > 0 else 1.0

        # 3. Inverse correlation to existing open positions
        avg_corr = 0.0
        if current_positions and correlations:
            corrs = []
            for pos in current_positions:
                key = (min(order.asset, pos.asset), max(order.asset, pos.asset))
                corrs.append(correlations.get(key, 0.5))
            avg_corr = sum(corrs) / len(corrs) if corrs else 0.0
        inverse_correlation = 1.0 - avg_corr

        # 4. Symbol recency penalty (prefer assets not traded recently)
        last_t = self.last_trade_time.get(order.asset, 0.0)
        recency_hours = (now_ts - last_t) / 3600.0 if last_t > 0 else 100.0
        symbol_recency = min(1.0, recency_hours / 24.0)

        total_score = (
            0.35 * signal_strength
            + 0.25 * vol_adj_return
            + 0.25 * inverse_correlation
            + 0.15 * symbol_recency
        )

        return AllocationPriorityScore(
            asset=order.asset,
            signal_strength=signal_strength,
            vol_adj_return=vol_adj_return,
            inverse_correlation=inverse_correlation,
            symbol_recency=symbol_recency,
            total_score=total_score,
        )

    def allocate_capital(
        self,
        pending_orders: List[ApprovedOrder],
        account_state: AccountState,
        correlations: Optional[Dict[Tuple[str, str], float]] = None,
        now_ts: float = 0.0,
    ) -> Tuple[List[ApprovedOrder], List[Tuple[ApprovedOrder, str]]]:
        """Prioritizes and filters pending ApprovedOrders.

        Returns:
            (approved_allocations, rejected_allocations_with_reasons)
        """
        if not pending_orders:
            return [], []

        current_positions = account_state.open_positions
        current_pos_count = len(current_positions)
        current_exposure = sum(p.entry_price * getattr(p, "position_size", 1.0) for p in current_positions)
        max_allowed_exposure = account_state.equity * self.max_portfolio_exposure_pct

        # Score pending orders
        scored_orders = [
            (
                order,
                self.calculate_priority_score(order, current_positions, correlations, now_ts),
            )
            for order in pending_orders
        ]
        # Sort descending by total_score
        scored_orders.sort(key=lambda x: x[1].total_score, reverse=True)

        accepted: List[ApprovedOrder] = []
        rejected: List[Tuple[ApprovedOrder, str]] = []

        active_count = current_pos_count
        accumulated_exp = current_exposure

        for order, score in scored_orders:
            order_notional = order.position_size * order.entry_price
            single_asset_limit = account_state.equity * self.max_single_asset_pct

            if active_count >= self.max_concurrent_positions:
                rejected.append((order, f"CAPITAL_ALLOCATION_REJECTED: Max concurrent positions ({self.max_concurrent_positions}) reached"))
                logger.info(f"[CAPITAL_ALLOCATION_REJECTED] {order.asset}: Max positions reached.")
                continue

            if accumulated_exp + order_notional > max_allowed_exposure:
                rejected.append((order, f"CAPITAL_ALLOCATION_REJECTED: Portfolio exposure limit (${max_allowed_exposure:.2f}) exceeded"))
                logger.info(f"[CAPITAL_ALLOCATION_REJECTED] {order.asset}: Exposure limit exceeded.")
                continue

            if order_notional > single_asset_limit:
                rejected.append((order, f"CAPITAL_ALLOCATION_REJECTED: Single asset notional (${order_notional:.2f}) > cap (${single_asset_limit:.2f})"))
                logger.info(f"[CAPITAL_ALLOCATION_REJECTED] {order.asset}: Single asset limit exceeded.")
                continue

            # Accept full order (SOVEREIGN INVARIANT: Never alter position size)
            accepted.append(order)
            active_count += 1
            accumulated_exp += order_notional
            self.last_trade_time[order.asset] = now_ts

        return accepted, rejected
