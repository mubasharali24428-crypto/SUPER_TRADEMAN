import logging
import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

logger = logging.getLogger("trading.risk.garch")


@dataclass(frozen=True)
class GARCHForecastResult:
    """Output of a GARCH volatility estimation and forecast."""
    conditional_volatility: float  # 1-period ahead forecast sigma_{t+1} (in % scale or decimal scale)
    annualized_volatility: float   # Annualized conditional volatility
    omega: float                   # Constant variance term
    alpha: float                   # ARCH parameter (reaction to recent shocks)
    beta: float                    # GARCH parameter (volatility persistence)
    persistence: float             # alpha + beta
    unconditional_volatility: float # Long-run average volatility sqrt(omega / (1 - alpha - beta))
    is_high_volatility: bool       # High volatility regime indicator flag
    volatility_scale_factor: float # Scaling factor to adjust base position risk pct


class GARCHVolatilityModel:
    """GARCH(1,1) Volatility Modeling and Dynamic Risk Scaling.

    Uses `arch` library (GARCH(1,1) with Gaussian or Student's t errors) to fit historical
    asset log-returns and forecast 1-step ahead conditional volatility sigma_{t+1}.
    """

    def __init__(
        self,
        target_annual_vol: float = 0.40,  # 40% annual target volatility for crypto
        high_vol_threshold_ann: float = 0.65, # 65% annual vol triggers high_volatility flag
        min_history_length: int = 30,
        periods_per_year: float = 365.0,  # 365 for crypto 24/7 markets
    ):
        self.target_annual_vol = target_annual_vol
        self.high_vol_threshold_ann = high_vol_threshold_ann
        self.min_history_length = min_history_length
        self.periods_per_year = periods_per_year

    def fit_forecast(self, prices_or_returns: Sequence[float], is_returns: bool = False) -> GARCHForecastResult:
        """Fits GARCH(1,1) model and computes 1-period ahead conditional volatility forecast.

        Args:
            prices_or_returns: Sequence of asset closing prices or log-returns.
            is_returns: True if input is already log-returns.

        Returns:
            GARCHForecastResult with estimated parameters, forecast, and scaling factors.
        """
        arr = np.array(prices_or_returns, dtype=float)
        if len(arr) < self.min_history_length:
            return self._fallback_forecast(arr, is_returns=is_returns)

        if not is_returns:
            # Calculate log returns: ln(P_t / P_{t-1}) * 100 for arch library numerical stability
            returns = np.diff(np.log(arr)) * 100.0
        else:
            returns = arr * 100.0

        if len(returns) < self.min_history_length - 1:
            return self._fallback_forecast(arr, is_returns=is_returns)

        try:
            from arch import arch_model

            # Fit standard GARCH(1, 1) model
            am = arch_model(returns, vol="Garch", p=1, q=1, dist="normal", rescale=False)
            res = am.fit(disp="off", show_warning=False)

            # Extract estimated parameters
            params = res.params
            omega = float(params.get("omega", 0.01))
            alpha = float(params.get("alpha[1]", 0.05))
            beta = float(params.get("beta[1]", 0.90))
            persistence = alpha + beta

            # Forecast 1-step ahead variance
            forecast = res.forecast(horizon=1)
            next_variance = forecast.variance.iloc[-1, 0]
            if np.isnan(next_variance) or next_variance <= 0:
                return self._fallback_forecast(arr, is_returns=is_returns)

            # Convert forecast sigma back from percentage scale to decimal scale
            sigma_next_decimal = math.sqrt(next_variance) / 100.0
            ann_vol = sigma_next_decimal * math.sqrt(self.periods_per_year)

            # Unconditional long-run variance
            if persistence < 1.0 and (1.0 - persistence) > 1e-6:
                uncond_var = omega / (1.0 - persistence)
                uncond_vol = (math.sqrt(uncond_var) / 100.0) * math.sqrt(self.periods_per_year)
            else:
                uncond_vol = ann_vol

            # Calculate dynamic risk scaling factor based on target volatility
            vol_scale = self.target_annual_vol / max(ann_vol, 1e-4)
            # Safe bounding: Scale risk between 0.25x and 1.50x base risk
            vol_scale_bounded = float(np.clip(vol_scale, 0.25, 1.50))

            is_high_vol = ann_vol >= self.high_vol_threshold_ann

            return GARCHForecastResult(
                conditional_volatility=sigma_next_decimal,
                annualized_volatility=ann_vol,
                omega=omega,
                alpha=alpha,
                beta=beta,
                persistence=persistence,
                unconditional_volatility=uncond_vol,
                is_high_volatility=is_high_vol,
                volatility_scale_factor=vol_scale_bounded,
            )

        except Exception as exc:
            logger.warning("GARCH fitting failed (%s). Using fallback volatility estimation.", exc)
            return self._fallback_forecast(arr, is_returns=is_returns)

    def _fallback_forecast(self, arr: np.ndarray, is_returns: bool) -> GARCHForecastResult:
        """Fallback exponential moving average / standard deviation volatility estimate."""
        if len(arr) < 2:
            return GARCHForecastResult(
                conditional_volatility=0.02,
                annualized_volatility=0.40,
                omega=0.0,
                alpha=0.05,
                beta=0.90,
                persistence=0.95,
                unconditional_volatility=0.40,
                is_high_volatility=False,
                volatility_scale_factor=1.0,
            )

        if not is_returns:
            returns = np.diff(np.log(arr))
        else:
            returns = arr

        std_dev = float(np.std(returns)) if len(returns) > 1 else 0.02
        ann_vol = std_dev * math.sqrt(self.periods_per_year)
        vol_scale = float(np.clip(self.target_annual_vol / max(ann_vol, 1e-4), 0.25, 1.50))

        return GARCHForecastResult(
            conditional_volatility=std_dev,
            annualized_volatility=ann_vol,
            omega=0.0,
            alpha=0.05,
            beta=0.90,
            persistence=0.95,
            unconditional_volatility=ann_vol,
            is_high_volatility=ann_vol >= self.high_vol_threshold_ann,
            volatility_scale_factor=vol_scale,
        )
