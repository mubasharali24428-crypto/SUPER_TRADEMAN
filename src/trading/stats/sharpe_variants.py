"""Sharpe ratio variants for multiple-testing-aware performance evaluation.

Implements two estimators from the backtest-overfitting literature:

- ``probabilistic_sharpe_ratio`` — Probabilistic Sharpe Ratio (PSR),
  Lopez de Prado (2012), "The Sharpe Ratio Efficient Frontier",
  Journal of Risk 15(2), pp. 3-44.

- ``deflated_sharpe_ratio`` — Deflated Sharpe Ratio (DSR),
  Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio: Correcting for
  Selection Bias, Backtest Overfitting and Non-Normality",
  Journal of Portfolio Management 40(5), pp. 94-107.

Both operate on *per-period* (non-annualized) Sharpe ratios and account for
the non-normality (skewness g3, kurtosis g4) of returns.
"""

from __future__ import annotations

import numpy as np
from scipy import stats as scistats

__all__ = [
    "expected_max_sharpe",
    "deflated_sharpe_ratio",
    "probabilistic_sharpe_ratio",
]

_EULER_GAMMA = 0.5772156649015329  # Euler-Mascheroni constant


def _psr_variance_term(sharpe: float, skew: float, kurtosis: float) -> float:
    """Variance term of the PSR z-statistic (must be strictly positive).

    var = 1 - g3*SR + ((g4 - 1) / 4) * SR^2
    """
    var_term = (
        1.0
        - skew * sharpe
        + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe
    )
    if var_term <= 0.0:
        raise ValueError(
            "PSR variance term is non-positive "
            f"(1 - skew*SR + (kurt-1)/4*SR^2 = {var_term:.6g}); the Sharpe ratio "
            "is too extreme for the given skew/kurtosis — PSR undefined."
        )
    return var_term


def probabilistic_sharpe_ratio(
    benchmark_sharpe: float,
    sharpe_observed: float,
    n_observations: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Probabilistic Sharpe Ratio (Lopez de Prado, 2012).

    Probability that the true Sharpe ratio exceeds a benchmark SR*:

        PSR(SR*) = Phi( (SR_obs - SR*) * sqrt(T - 1)
                        / sqrt(1 - g3*SR_obs + ((g4 - 1)/4) * SR_obs^2) )

    Args:
        benchmark_sharpe: Benchmark Sharpe ratio SR* (per-period).
        sharpe_observed: Observed Sharpe ratio (per-period).
        n_observations: Number of return observations T (>= 2).
        skew: Sample skewness g3 of the returns.
        kurtosis: Sample (Pearson, non-excess) kurtosis g4 of the returns.

    Returns:
        PSR in (0, 1). 0.5 means the observed SR carries no evidence above
        the benchmark; > 0.95 is the usual significance threshold.

    Raises:
        ValueError: If T < 2, the variance term is non-positive, or inputs
            are non-finite.
    """
    if n_observations < 2:
        raise ValueError(f"n_observations must be >= 2, got {n_observations}")
    for name, val in (
        ("benchmark_sharpe", benchmark_sharpe),
        ("sharpe_observed", sharpe_observed),
        ("skew", skew),
        ("kurtosis", kurtosis),
    ):
        if not np.isfinite(val):
            raise ValueError(f"{name} must be finite, got {val}")

    var_term = _psr_variance_term(float(sharpe_observed), float(skew), float(kurtosis))
    z = (float(sharpe_observed) - float(benchmark_sharpe)) * np.sqrt(n_observations - 1)
    z /= np.sqrt(var_term)
    return float(scistats.norm.cdf(z))


def expected_max_sharpe(n_trials: int, var_sharpe_trials: float) -> float:
    """Expected maximum Sharpe ratio across N independent trials.

    E[max SR_N] = sd(SR) * ( (1 - gamma) * Phi^{-1}(1 - 1/N)
                             + gamma * Phi^{-1}(1 - 1/(N e)) )

    (Bailey & Lopez de Prado 2014, eq. 4). This is the expected Sharpe ratio
    of the *best* strategy picked ex-post from N trials under H0 (no skill),
    and is the benchmark SR0 used by the Deflated Sharpe Ratio.

    Args:
        n_trials: Number of independent trials/configurations N >= 1.
        var_sharpe_trials: Variance of the Sharpe ratios across trials
            (sd(SR)^2). Must be > 0 unless n_trials == 1.

    Returns:
        Expected maximum Sharpe ratio (same units as sd(SR)).
    """
    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials}")
    if var_sharpe_trials < 0:
        raise ValueError("var_sharpe_trials must be >= 0")
    if n_trials == 1:
        return 0.0
    if var_sharpe_trials == 0.0:
        raise ValueError(
            "var_sharpe_trials must be > 0 when n_trials > 1 "
            "(identical trial Sharpes carry no dispersion to deflate)"
        )

    sd_sr = float(np.sqrt(var_sharpe_trials))
    n = float(n_trials)
    emc = 0.5772156649015329  # Euler-Mascheroni constant
    sr0 = (1.0 - emc) * scistats.norm.ppf(1.0 - 1.0 / n) + emc * scistats.norm.ppf(
        1.0 - 1.0 / (n * np.e)
    )
    return float(sd_sr * sr0)


def deflated_sharpe_ratio(
    sharpe_observed: float,
    n_trials: int,
    n_observations: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    var_sharpe_trials: float = 1.0,
) -> float:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

    DSR = PSR(SR0) where the benchmark SR0 is the *expected maximum* Sharpe
    ratio under H0 across ``n_trials`` independent trials:

        DSR = Phi( (SR_obs - SR0) * sqrt(T - 1)
                   / sqrt(1 - g3*SR_obs + ((g4 - 1)/4) * SR_obs^2) )

    Args:
        sharpe_observed: Best (selected) trial's Sharpe ratio, per-period.
        n_trials: Number of independent backtest trials N attempted.
        n_observations: Number of return observations T in the selected trial.
        skew: Skewness g3 of the selected trial's returns.
        kurtosis: Non-excess kurtosis g4 of the selected trial's returns.
        var_sharpe_trials: Variance of Sharpe ratios ACROSS the N trials, on
            the SAME per-period scale as ``sharpe_observed``. E.g. to deflate
            the Bailey & Lopez de Prado (2014) example (sd(SR)=1.0 across
            trials measured on an aggregate/annualized scale with T=2479
            observations), pass ``var_sharpe_trials=1.0 / T`` so the expected
            maximum lands on the per-period scale too.

    Returns:
        DSR in (0, 1): probability that the selected strategy's SR is greater
        than what pure selection bias would deliver.

    Raises:
        ValueError: On invalid inputs (see helpers above).
    """
    # SR0 stays on the caller's declared scale: expected_max_sharpe() scales
    # with sd(trial SRs), so feeding it per-period trial variance yields a
    # per-period SR0 comparable to sharpe_observed.
    sr0 = expected_max_sharpe(int(n_trials), float(var_sharpe_trials))
    return probabilistic_sharpe_ratio(
        benchmark_sharpe=sr0,
        sharpe_observed=float(sharpe_observed),
        n_observations=int(n_observations),
        skew=float(skew),
        kurtosis=float(kurtosis),
    )
