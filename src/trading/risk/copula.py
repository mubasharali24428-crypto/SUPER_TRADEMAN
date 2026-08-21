import logging
import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from scipy import stats

logger = logging.getLogger("trading.risk.copula")


@dataclass(frozen=True)
class CopulaDependencyResult:
    """Output of Copula multi-asset joint dependency analysis."""
    asset_pair: tuple[str, str]
    linear_correlation: float      # Standard Pearson correlation
    rank_correlation: float        # Spearman rank correlation
    lower_tail_dependence: float   # Probability of joint extreme crash lambda_L
    upper_tail_dependence: float   # Probability of joint extreme rally lambda_U
    copula_type: str               # "clayton" | "gumbel" | "gaussian" | "student_t"
    is_co_dependent_crash: bool    # True if lower tail dependence > threshold
    recommended_cluster_cap: float # Max combined risk percentage allocated across this pair


class CopulaDependencyEngine:
    """Copula Multi-Asset Dependency and Joint Tail Risk Guard.

    Models non-linear dependence and asymmetric lower tail dependence (joint crash probability)
    across portfolio assets (e.g. BTC, ETH, SOL) using Archimedean and Gaussian copula structures.
    """

    def __init__(
        self,
        tail_dependence_threshold: float = 0.60, # lambda_L > 0.60 triggers joint crash clustering
        min_samples: int = 50,
    ):
        self.tail_dependence_threshold = tail_dependence_threshold
        self.min_samples = min_samples

    def analyze_pair_dependency(
        self,
        asset_a: str,
        asset_b: str,
        returns_a: Sequence[float],
        returns_b: Sequence[float],
    ) -> CopulaDependencyResult:
        """Analyzes non-linear and tail dependence between two asset return series."""
        ra = np.array(returns_a, dtype=float)
        rb = np.array(returns_b, dtype=float)

        min_len = min(len(ra), len(rb))
        if min_len < self.min_samples:
            return self._fallback_dependency(asset_a, asset_b, ra, rb)

        ra = ra[-min_len:]
        rb = rb[-min_len:]

        try:
            # 1. Compute Pearson and Spearman Rank Correlations
            pearson_corr = float(np.corrcoef(ra, rb)[0, 1])
            spearman_corr, _ = stats.spearmanr(ra, rb)
            spearman_corr = float(spearman_corr)

            # 2. Transform marginals to Uniform [0, 1] via empirical CDF (ranks)
            u = stats.rankdata(ra) / (len(ra) + 1.0)
            v = stats.rankdata(rb) / (len(rb) + 1.0)

            # 3. Estimate Empirical Lower and Upper Tail Dependence (quantile exceedance)
            # lambda_L = P(U <= q, V <= q) / q for small q (e.g. q = 0.05)
            q_lower = 0.10
            joint_lower = np.sum((u <= q_lower) & (v <= q_lower)) / len(u)
            lambda_lower = float(joint_lower / q_lower)

            # lambda_U = P(U >= 1-q, V >= 1-q) / q
            q_upper = 0.10
            joint_upper = np.sum((u >= (1.0 - q_upper)) & (v >= (1.0 - q_upper))) / len(u)
            lambda_upper = float(joint_upper / q_upper)

            # Bound between [0.0, 1.0]
            lambda_lower = float(np.clip(lambda_lower, 0.0, 1.0))
            lambda_upper = float(np.clip(lambda_upper, 0.0, 1.0))

            # Classify Copula Family based on tail asymmetry
            if lambda_lower > lambda_upper + 0.10:
                copula_type = "clayton"  # Strong lower tail dependency (joint crashes)
            elif lambda_upper > lambda_lower + 0.10:
                copula_type = "gumbel"   # Strong upper tail dependency (joint booms)
            elif abs(lambda_lower - lambda_upper) < 0.10 and lambda_lower > 0.30:
                copula_type = "student_t" # Symmetric heavy tail dependency
            else:
                copula_type = "gaussian"

            is_co_dependent = lambda_lower >= self.tail_dependence_threshold
            # Stricter risk cap if assets crash together
            cluster_cap = 0.04 if is_co_dependent else 0.08

            return CopulaDependencyResult(
                asset_pair=(asset_a, asset_b),
                linear_correlation=pearson_corr,
                rank_correlation=spearman_corr,
                lower_tail_dependence=lambda_lower,
                upper_tail_dependence=lambda_upper,
                copula_type=copula_type,
                is_co_dependent_crash=is_co_dependent,
                recommended_cluster_cap=cluster_cap,
            )

        except Exception as exc:
            logger.warning("Copula dependency estimation failed (%s). Using linear fallback.", exc)
            return self._fallback_dependency(asset_a, asset_b, ra, rb)

    def _fallback_dependency(
        self, asset_a: str, asset_b: str, ra: np.ndarray, rb: np.ndarray
    ) -> CopulaDependencyResult:
        """Linear correlation fallback."""
        corr = float(np.corrcoef(ra, rb)[0, 1]) if len(ra) > 1 and len(rb) > 1 else 0.50
        is_co_dependent = corr >= 0.70
        return CopulaDependencyResult(
            asset_pair=(asset_a, asset_b),
            linear_correlation=corr,
            rank_correlation=corr,
            lower_tail_dependence=corr * 0.8,
            upper_tail_dependence=corr * 0.8,
            copula_type="gaussian",
            is_co_dependent_crash=is_co_dependent,
            recommended_cluster_cap=0.04 if is_co_dependent else 0.08,
        )
