import logging
import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy import stats

logger = logging.getLogger("trading.risk.evt")


@dataclass(frozen=True)
class EVTRiskResult:
    """Output of Extreme Value Theory (EVT) Generalized Pareto Distribution analysis."""
    var_95: float             # Value-at-Risk at 95% confidence (decimal fraction, e.g. 0.02 = 2%)
    var_99: float             # Value-at-Risk at 99% confidence
    cvar_99: float            # Expected Shortfall / Tail-VaR at 99% confidence
    shape_xi: float           # Tail index parameter xi (xi > 0 indicates fat/heavy tails)
    scale_beta: float         # Scale parameter beta
    threshold_u: float        # Threshold u for Peaks-over-Threshold (POT)
    num_exceedances: int      # Count of losses exceeding threshold u
    is_heavy_tailed: bool     # True if xi > 0.1 (common in crypto crash tails)
    recommended_risk_scale: float # Multiplier to scale down position size if tail risk is severe


class EVTRiskEngine:
    """Extreme Value Theory (EVT) Peaks-Over-Threshold (POT) Tail-Risk Engine.

    Fits a Generalized Pareto Distribution (GPD) using `scipy.stats.genpareto`
    to empirical loss tails to compute mathematically rigorous 99% Tail-VaR (CVaR).
    """

    def __init__(
        self,
        threshold_quantile: float = 0.90,  # 90th percentile of losses as threshold u
        min_samples: int = 50,
        target_tail_var_cap: float = 0.05,  # 5% max acceptable 99% Tail-VaR
    ):
        self.threshold_quantile = threshold_quantile
        self.min_samples = min_samples
        self.target_tail_var_cap = target_tail_var_cap

    def estimate_tail_risk(self, prices_or_returns: Sequence[float], is_returns: bool = False) -> EVTRiskResult:
        """Estimates EVT GPD parameters and computes 99% Tail-VaR on asset returns.

        Args:
            prices_or_returns: Sequence of asset prices or log-returns.
            is_returns: True if input is already log-returns.

        Returns:
            EVTRiskResult with VaR, CVaR (Tail-VaR), GPD parameters, and risk scaling recommendations.
        """
        arr = np.array(prices_or_returns, dtype=float)
        if len(arr) < self.min_samples:
            return self._fallback_risk(arr, is_returns=is_returns)

        if not is_returns:
            returns = np.diff(np.log(arr))
        else:
            returns = arr

        # Focus on negative returns (losses: L = -returns)
        losses = -returns[returns < 0]
        if len(losses) < 20:
            return self._fallback_risk(arr, is_returns=is_returns)

        try:
            # Set threshold u at designated quantile of loss distribution
            u = float(np.quantile(losses, self.threshold_quantile))
            exceedances = losses[losses > u] - u
            n_exceed = len(exceedances)
            n_total = len(returns)

            if n_exceed < 8:
                return self._fallback_risk(arr, is_returns=is_returns)

            # Fit Generalized Pareto Distribution (GPD) via Maximum Likelihood
            # scipy genpareto parameterization: c = xi, scale = beta, loc = 0
            shape_c, loc, scale_b = stats.genpareto.fit(exceedances, floc=0)
            xi = float(shape_c)
            beta = float(scale_b)

            # Calculate EVT VaR at alpha = 0.05 (95%) and alpha = 0.01 (99%)
            def calc_evt_var(alpha: float) -> float:
                if xi != 0:
                    term = (n_total / n_exceed) * alpha
                    if term <= 0:
                        return u
                    return u + (beta / xi) * (math.pow(term, -xi) - 1.0)
                else:
                    return u - beta * math.log((n_total / n_exceed) * alpha)

            var_95 = max(calc_evt_var(0.05), u)
            var_99 = max(calc_evt_var(0.01), var_95)

            # Calculate EVT Expected Shortfall (Tail-VaR) at 99%
            if xi < 1.0:
                cvar_99 = (var_99 + beta - xi * u) / (1.0 - xi)
            else:
                cvar_99 = var_99 * 1.5  # Fallback if first moment does not exist

            cvar_99 = max(cvar_99, var_99)

            is_heavy = xi > 0.10
            # Scale down position risk if Tail-VaR exceeds safe cap
            scale_factor = float(np.clip(self.target_tail_var_cap / max(cvar_99, 1e-4), 0.20, 1.25))

            return EVTRiskResult(
                var_95=float(var_95),
                var_99=float(var_99),
                cvar_99=float(cvar_99),
                shape_xi=xi,
                scale_beta=beta,
                threshold_u=u,
                num_exceedances=n_exceed,
                is_heavy_tailed=is_heavy,
                recommended_risk_scale=scale_factor,
            )

        except Exception as exc:
            logger.warning("EVT GPD fitting failed (%s). Using empirical fallback.", exc)
            return self._fallback_risk(arr, is_returns=is_returns)

    def _fallback_risk(self, arr: np.ndarray, is_returns: bool) -> EVTRiskResult:
        """Empirical quantile fallback for short or non-converging return series."""
        if len(arr) < 2:
            return EVTRiskResult(
                var_95=0.02,
                var_99=0.04,
                cvar_99=0.05,
                shape_xi=0.15,
                scale_beta=0.01,
                threshold_u=0.015,
                num_exceedances=5,
                is_heavy_tailed=True,
                recommended_risk_scale=1.0,
            )

        if not is_returns:
            returns = np.diff(np.log(arr))
        else:
            returns = arr

        losses = -returns[returns < 0]
        if len(losses) > 0:
            var_95 = float(np.quantile(losses, 0.95))
            var_99 = float(np.quantile(losses, 0.99)) if len(losses) >= 100 else var_95 * 1.3
            cvar_99 = float(np.mean(losses[losses >= var_95])) if any(losses >= var_95) else var_99 * 1.2
            cvar_99 = max(cvar_99, var_99)
        else:
            var_95, var_99, cvar_99 = 0.02, 0.04, 0.05

        scale_factor = float(np.clip(self.target_tail_var_cap / max(cvar_99, 1e-4), 0.20, 1.25))

        return EVTRiskResult(
            var_95=var_95,
            var_99=var_99,
            cvar_99=cvar_99,
            shape_xi=0.15,
            scale_beta=0.01,
            threshold_u=var_95,
            num_exceedances=len(losses),
            is_heavy_tailed=True,
            recommended_risk_scale=scale_factor,
        )
