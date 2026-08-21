"""Portfolio Risk Aggregator and VaR Monitoring Engine."""

import math
from typing import Dict, List, Optional

from trading.risk.correlation import CorrelationMatrix
from trading.risk.models import ApprovedOrder, Position

__all__ = ["PortfolioRiskAggregator"]


class PortfolioRiskAggregator:
    """Aggregates portfolio-level VaR, effective leverage, and correlation-adjusted risk."""

    @staticmethod
    def calculate_effective_leverage(positions: List[Position], equity: float) -> float:
        """Calculates effective portfolio leverage: L_eff = sum(|notional_i|) / Equity."""
        if equity <= 0:
            return 0.0
        total_notional = sum(
            p.entry_price * getattr(p, "position_size", 1.0) for p in positions
        )
        return float(total_notional / equity)

    @staticmethod
    def calculate_portfolio_volatility(
        positions: List[Position],
        correlation_matrix: Optional[CorrelationMatrix],
        asset_vols: Dict[str, float],
        equity: float,
    ) -> float:
        """Calculates correlation-adjusted portfolio volatility sigma_portfolio.

        Formula: sigma_portfolio = sqrt( sum_i sum_j w_i w_j sigma_i sigma_j rho_ij )
        where w_i = notional_i / Equity.
        """
        if equity <= 0 or not positions:
            return 0.0

        n = len(positions)
        weights = [
            (p.entry_price * getattr(p, "position_size", 1.0)) / equity
            for p in positions
        ]
        vols = [asset_vols.get(p.asset, 0.02) for p in positions]

        var_sum = 0.0
        for i in range(n):
            for j in range(n):
                asset_i = positions[i].asset
                asset_j = positions[j].asset
                rho = (
                    correlation_matrix.get_correlation(asset_i, asset_j)
                    if correlation_matrix
                    else (1.0 if i == j else 0.0)
                )
                var_sum += weights[i] * weights[j] * vols[i] * vols[j] * rho

        return math.sqrt(max(0.0, var_sum))

    @staticmethod
    def calculate_portfolio_var(
        equity: float, portfolio_vol: float, confidence_level: float = 0.99
    ) -> float:
        """Calculates parametric 1-day Value at Risk (VaR) in USD."""
        # Normal z-score multiplier for confidence level
        z_score = 2.326 if confidence_level >= 0.99 else 1.645
        return float(equity * portfolio_vol * z_score)

    def is_entry_allowed(
        self,
        new_order: ApprovedOrder,
        current_positions: List[Position],
        correlation_matrix: Optional[CorrelationMatrix],
        asset_vols: Dict[str, float],
        equity: float,
        max_portfolio_risk_pct: float = 0.08,
    ) -> bool:
        """Checks if adding new_order causes portfolio volatility to exceed max_portfolio_risk_pct."""
        # Create hypothetical position list with new_order
        hypothetical_pos = list(current_positions) + [
            Position(
                asset=new_order.asset,
                asset_class=new_order.asset_class,
                side=new_order.side,
                entry_price=new_order.entry_price,
                stop_price=new_order.stop_price,
                risk_pct=new_order.risk_pct,
            )
        ]

        port_vol = self.calculate_portfolio_volatility(
            hypothetical_pos, correlation_matrix, asset_vols, equity
        )

        return port_vol <= max_portfolio_risk_pct
