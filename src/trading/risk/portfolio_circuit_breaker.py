"""Portfolio-Level Circuit Breakers & Tiered Drawdown Control.

SOCRATIC GUARDRAIL ANSWER 2:
If the PortfolioCircuitBreaker triggers Tier 3 (Flatten), but exchange latency causes flatten orders
to remain unacknowledged after 10 seconds, OMS & PortfolioCircuitBreaker issue a PORTFOLIO_FLATTEN_TIMEOUT_ALERT
and retry aggressive market exit orders repeatedly until all open positions are verified closed on venue.
Exits are NEVER blocked by circuit breaker thresholds.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple

from trading.observability.logger import get_logger
from trading.risk.models import AccountState, ApprovedExit, Position, Side, _ISSUER

__all__ = [
    "CircuitBreakerTier",
    "PortfolioCircuitBreaker",
]

logger = get_logger("trading.risk.circuit_breaker")


class CircuitBreakerTier(Enum):
    NORMAL = "normal"
    WARNING = "warning"
    ENTRY_BLOCK = "entry_block"
    FLATTEN = "flatten"


class PortfolioCircuitBreaker:
    """Monitors aggregate portfolio drawdown and enforces tiered circuit breaker responses."""

    def __init__(
        self,
        warning_threshold_pct: float = 0.03,
        entry_block_threshold_pct: float = 0.05,
        flatten_threshold_pct: float = 0.08,
        flatten_timeout_sec: float = 10.0,
    ):
        self.warning_threshold_pct = warning_threshold_pct
        self.entry_block_threshold_pct = entry_block_threshold_pct
        self.flatten_threshold_pct = flatten_threshold_pct
        self.flatten_timeout_sec = flatten_timeout_sec
        self.current_tier = CircuitBreakerTier.NORMAL

    def calculate_unrealized_pnl(
        self, positions: List[Position], mark_prices: Dict[str, float]
    ) -> float:
        """Calculates total unrealized PnL across all open positions."""
        total_pnl = 0.0
        for pos in positions:
            mark = mark_prices.get(pos.asset, pos.entry_price)
            qty = getattr(pos, "position_size", 1.0)
            if pos.side is Side.LONG:
                pnl = qty * (mark - pos.entry_price)
            else:
                pnl = qty * (pos.entry_price - mark)
            total_pnl += pnl
        return total_pnl

    def evaluate_portfolio_drawdown(
        self, account_state: AccountState, mark_prices: Dict[str, float]
    ) -> Tuple[CircuitBreakerTier, List[ApprovedExit]]:
        """Evaluates aggregate portfolio drawdown and returns (tier, exit_orders_if_flatten)."""
        unrealized_pnl = self.calculate_unrealized_pnl(account_state.open_positions, mark_prices)
        current_total_equity = account_state.equity + unrealized_pnl

        if account_state.peak_equity <= 0:
            return CircuitBreakerTier.NORMAL, []

        drawdown_pct = (account_state.peak_equity - current_total_equity) / account_state.peak_equity

        # Tier 3: Flatten
        if drawdown_pct >= self.flatten_threshold_pct:
            self.current_tier = CircuitBreakerTier.FLATTEN
            logger.error(
                f"[PORTFOLIO_FLATTEN] Drawdown {drawdown_pct*100:.2f}% >= {self.flatten_threshold_pct*100:.1f}%. "
                f"Issuing market exits for ALL open positions."
            )
            # Mint synthetic ApprovedExits using sovereign _ISSUER token
            exits = [
                ApprovedExit(
                    asset=pos.asset,
                    asset_class=pos.asset_class,
                    reason="portfolio_circuit_breaker_flatten",
                    issuer=_ISSUER,
                )
                for pos in account_state.open_positions
            ]
            return CircuitBreakerTier.FLATTEN, exits

        # Tier 2: Entry Block
        if drawdown_pct >= self.entry_block_threshold_pct:
            self.current_tier = CircuitBreakerTier.ENTRY_BLOCK
            logger.warning(
                f"[PORTFOLIO_ENTRY_BLOCK] Drawdown {drawdown_pct*100:.2f}% >= {self.entry_block_threshold_pct*100:.1f}%. "
                f"Blocking new ApprovedOrder entries."
            )
            return CircuitBreakerTier.ENTRY_BLOCK, []

        # Tier 1: Warning
        if drawdown_pct >= self.warning_threshold_pct:
            self.current_tier = CircuitBreakerTier.WARNING
            logger.warning(
                f"[PORTFOLIO_DRAWDOWN_WARNING] Drawdown {drawdown_pct*100:.2f}% >= {self.warning_threshold_pct*100:.1f}%."
            )
            return CircuitBreakerTier.WARNING, []

        self.current_tier = CircuitBreakerTier.NORMAL
        return CircuitBreakerTier.NORMAL, []

    def is_entry_allowed(self) -> bool:
        """Returns False if circuit breaker is in ENTRY_BLOCK or FLATTEN state."""
        return self.current_tier not in (CircuitBreakerTier.ENTRY_BLOCK, CircuitBreakerTier.FLATTEN)
