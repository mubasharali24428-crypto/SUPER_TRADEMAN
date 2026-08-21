"""Exponentially Weighted Moving Average (EWMA) Pairwise Correlation Matrix Module.

SOCRATIC GUARDRAIL ANSWER 3:
If two symbols have a rolling correlation >= 0.85 (e.g. BTC and ETH at 0.95), they represent
a highly correlated risk cluster rather than independent bets. The CorrelationMatrix flags these
high-correlation pairs. The CapitalAllocator penalizes the inverse_correlation priority score component,
and the PortfolioRiskAggregator accounts for their joint covariance w_i * w_j * sigma_i * sigma_j * rho_ij.
If adding the second asset causes portfolio_sigma to breach max_portfolio_risk_pct, the entry is blocked.
"""

from typing import Dict, List, Tuple, Optional
import numpy as np
import pandas as pd

__all__ = ["CorrelationMatrix"]


class CorrelationMatrix:
    """Maintains an EWMA pairwise correlation matrix across traded symbols."""

    def __init__(self, decay_factor: float = 0.94, min_periods: int = 10):
        self.decay_factor = decay_factor  # ~30 day half-life for daily returns
        self.min_periods = min_periods
        self.matrix: pd.DataFrame = pd.DataFrame()

    def update_returns(self, returns_df: pd.DataFrame) -> pd.DataFrame:
        """Updates the EWMA correlation matrix given a dataframe of asset returns."""
        if len(returns_df) < self.min_periods:
            return self.matrix

        # EWMA covariance matrix: Cov_t = (1 - lambda) * r_t * r_t^T + lambda * Cov_{t-1}
        ewma_cov = returns_df.ewm(alpha=1.0 - self.decay_factor, min_periods=self.min_periods).cov()
        last_date = ewma_cov.index.levels[0][-1]
        cov_last = ewma_cov.loc[last_date]

        # Convert covariance to correlation: Corr = D^-1 * Cov * D^-1
        stds = np.sqrt(np.diag(cov_last.values))
        stds[stds == 0] = 1e-8
        outer_stds = np.outer(stds, stds)

        corr_vals = cov_last.values / outer_stds
        np.clip(corr_vals, -1.0, 1.0, out=corr_vals)

        self.matrix = pd.DataFrame(corr_vals, index=cov_last.index, columns=cov_last.columns)
        return self.matrix

    def get_correlation(self, asset1: str, asset2: str) -> float:
        """Returns the pairwise correlation between asset1 and asset2 (default 0.0 if unknown)."""
        if asset1 == asset2:
            return 1.0
        if self.matrix.empty or asset1 not in self.matrix.index or asset2 not in self.matrix.columns:
            return 0.0
        return float(self.matrix.loc[asset1, asset2])

    def get_high_correlation_pairs(self, threshold: float = 0.85) -> List[Tuple[str, str, float]]:
        """Returns all asset pairs with pairwise correlation >= threshold."""
        if self.matrix.empty:
            return []

        pairs: List[Tuple[str, str, float]] = []
        cols = list(self.matrix.columns)
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                corr = float(self.matrix.iloc[i, j])
                if abs(corr) >= threshold:
                    pairs.append((cols[i], cols[j], corr))
        return pairs
