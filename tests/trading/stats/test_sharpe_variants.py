"""Tests for Sharpe ratio variants (PSR / DSR, Lopez de Prado & Bailey).

Hand-computed values verified with scipy; published sanity example from
Bailey & Lopez de Prado (2014) "The Deflated Sharpe Ratio", JPM 40(5).
"""

import numpy as np
import pytest
from scipy import stats as scistats

from trading.stats.sharpe_variants import (
    deflated_sharpe_ratio,
    estimate_moments,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
)


# --------------------------------------------------------------------- #
# Hand-computed PSR values                                               #
# --------------------------------------------------------------------- #

def test_psr_zero_difference_is_half():
    # SR_obs == SR* => z = 0 => PSR = Phi(0) = 0.5 exactly.
    assert probabilistic_sharpe_ratio(0.5, 0.5, 250) == pytest.approx(0.5)


def test_psr_hand_computed_gaussian_returns():
    # SR*=0, SR_obs=0.1, T=252, skew=0, kurt=3:
    # z = 0.1 * sqrt(251) / sqrt(1 + 0.5*0.01)
    #   = 0.1 * 15.842979516660863 / 1.0024968807214134
    #   = 1.580359...  -> PSR = Phi(z) = 0.942987...
    psr = probabilistic_sharpe_ratio(0.0, 0.1, 252, skew=0.0, kurtosis=3.0)
    z = 0.1 * np.sqrt(251) / np.sqrt(1.0 + 0.5 * 0.01)
    assert psr == pytest.approx(scistats.norm.cdf(z), rel=1e-12)
    assert psr == pytest.approx(0.9429868610243624, abs=1e-10)  # hand-computed
    # Cross-check against the closed-form hand arithmetic
    assert z == pytest.approx(1.580359, abs=1e-5)


def test_psr_negative_skew_reduces_confidence():
    # Same |SR| and T: negative skew must LOWER the PSR of a positive SR,
    # positive skew raises it. Kurtosis > 3 also penalizes.
    psr_neg = probabilistic_sharpe_ratio(0.0, 0.15, 500, skew=-1.0, kurtosis=3.0)
    psr_zero = probabilistic_sharpe_ratio(0.0, 0.15, 500, skew=0.0, kurtosis=3.0)
    psr_pos = probabilistic_sharpe_ratio(0.0, 0.15, 500, skew=+1.0, kurtosis=3.0)
    assert psr_neg < psr_zero < psr_pos


def test_psr_higher_benchmark_lower_psr():
    psr_low_bar = probabilistic_sharpe_ratio(0.0, 0.2, 300)
    psr_high_bar = probabilistic_sharpe_ratio(0.15, 0.2, 300)
    assert psr_low_bar > psr_high_bar > 0.5
    assert psr_low_bar < 1.0


def test_psr_grows_with_t():
    psrs = [
        probabilistic_sharpe_ratio(0.0, 0.1, t) for t in (50, 100, 500, 2000)
    ]
    assert psrs == sorted(psrs)
    assert psrs[-1] > 0.999


def test_psr_invalid_inputs_raise():
    with pytest.raises(ValueError):
        probabilistic_sharpe_ratio(0.0, 0.1, 1)  # T < 2
    with pytest.raises(ValueError):
        probabilistic_sharpe_ratio(0.0, float("nan"), 100)  # NaN SR
    # Variance term non-positive: 1 - 2*SR + 0.5*SR^2 < 0 for SR in (0.59, 3.41)
    with pytest.raises(ValueError):
        probabilistic_sharpe_ratio(0.0, 1.5, 100, skew=2.0, kurtosis=3.0)


def test_psr_extreme_sr_valid_for_gaussian():
    # With skew=0, kurt=3 variance term = 1 + SR^2/2 is always positive.
    psr = probabilistic_sharpe_ratio(0.0, 5.0, 100)
    assert psr > 0.999999
    # But a large SR with positive skew CAN make the variance term negative:
    # var(SR) = 1 - 2.5*SR + 0.5*SR^2 < 0 for SR in (0.586, 4.414).
    with pytest.raises(ValueError):
        probabilistic_sharpe_ratio(0.0, 3.0, 100, skew=2.5, kurtosis=3.0)
    with pytest.raises(ValueError):
        probabilistic_sharpe_ratio(0.0, 2.0, 100, skew=2.5, kurtosis=3.0)


# --------------------------------------------------------------------- #
# Expected max Sharpe under multiple testing                             #
# --------------------------------------------------------------------- #

def test_expected_max_sharpe_single_trial_and_monotonicity():
    assert expected_max_sharpe(1, var_sharpe_trials=1.0) == 0.0
    vals = [expected_max_sharpe(n, var_sharpe_trials=1.0) for n in (2, 5, 20, 100)]
    assert vals == sorted(vals)          # grows with N
    assert all(v >= 0 for v in vals)
    # Published ballpark (Bailey & LdP 2014, eq. 4): E[max] over N=100 iid
    # standard-normal trials is ~2.50-2.51.
    assert 2.4 < vals[-1] < 2.6


def test_expected_max_sharpe_scales_with_dispersion():
    a = expected_max_sharpe(50, var_sharpe_trials=0.04)   # sd(SR)=0.2
    b = expected_max_sharpe(50, var_sharpe_trials=1.0)    # sd(SR)=1.0
    assert b == pytest.approx(a / 0.2, rel=1e-9)          # linear in sd(SR)


def test_expected_max_sharpe_invalid_inputs():
    with pytest.raises(ValueError):
        expected_max_sharpe(0, 1.0)
    with pytest.raises(ValueError):
        expected_max_sharpe(10, -1.0)
    with pytest.raises(ValueError):
        expected_max_sharpe(10, 0.0)  # zero dispersion with N>1 is meaningless


# --------------------------------------------------------------------- #
# Deflated Sharpe Ratio                                                  #
# --------------------------------------------------------------------- #

def test_dsr_published_example_bailey_lopez_de_prado_2014():
    # Bailey & Lopez de Prado (2014): N=100 trials, sd(SR)=1.0 across trials
    # measured on the paper's aggregate scale, T=2479 daily observations,
    # best trial's aggregate-equivalent SR = 1.55 (per-period: 1.55/sqrt(T)).
    # The cross-trial dispersion must be stated on the SAME per-period scale
    # as sharpe_observed -> var_sharpe_trials = 1.0 / T.
    T = 2479
    sr0_per_period = expected_max_sharpe(100, var_sharpe_trials=1.0 / T)
    # E[max SR_N=100, sd=1] = 2.5306 aggregate -> per-period:
    assert sr0_per_period == pytest.approx(0.0508260, abs=1e-6)
    assert sr0_per_period * np.sqrt(T) == pytest.approx(2.5306, abs=1e-4)

    dsr = deflated_sharpe_ratio(
        sharpe_observed=1.55 / np.sqrt(T),
        n_trials=100,
        n_observations=T,
        skew=0.0,
        kurtosis=3.0,
        var_sharpe_trials=1.0 / T,
    )
    # Hand-computed: SR0 ~= 0.05082, SR_obs ~= 0.03114 ->
    # z = -0.9798 -> DSR = Phi(z) ~= 0.1635.
    assert dsr == pytest.approx(0.1635, abs=1e-3)
    # Selection bias dominates: an annualized-SR-1.55 winner among 100 trials
    # sits well below its deflation benchmark (E[max] ~= 2.53 aggregate) --
    # only ~16% probability the pick reflects true skill.
    assert dsr < 0.20


def test_dsr_decreases_with_number_of_trials():
    dsrs = [
        deflated_sharpe_ratio(
            sharpe_observed=0.08,
            n_trials=n_trials,
            n_observations=1000,
            skew=0.0,
            kurtosis=3.0,
            var_sharpe_trials=1.0,
        )
        for n_trials in (1, 10, 100, 1000)
    ]
    assert dsrs == sorted(dsrs, reverse=True)  # strictly decreasing in N
    assert dsrs[0] > 0.95                      # single trial: high confidence
    assert dsrs[3] < 0.30                      # 1000 trials: selection eats it


def test_dsr_penalized_by_non_normality():
    # Both configs comfortably above their deflation benchmark (sd(SR)=0.01
    # per-period -> E[max] ~= 0.021), differing ONLY in skew/kurtosis:
    # heavy left tail + excess kurtosis must lower DSR.
    d_norm = deflated_sharpe_ratio(
        sharpe_observed=0.15, n_trials=20, n_observations=750,
        skew=0.0, kurtosis=3.0, var_sharpe_trials=1e-4,
    )
    d_fat = deflated_sharpe_ratio(
        sharpe_observed=0.15, n_trials=20, n_observations=750,
        skew=-1.5, kurtosis=9.0, var_sharpe_trials=1e-4,
    )
    assert 0.99 < d_fat < d_norm < 1.0


def test_dsr_perfectly_skillful_strategy_stays_above_half():
    # A genuinely strong strategy (SR far above the H0 expected max) keeps
    # DSR essentially 1 even after deflation.
    dsr = deflated_sharpe_ratio(
        sharpe_observed=0.5,
        n_trials=50,
        n_observations=2000,
        skew=0.0,
        kurtosis=4.0,
        var_sharpe_trials=0.01,  # sd(SR)=0.1 across trials -> SR0 ~= 0.235
    )
    assert dsr > 0.999999


# --------------------------------------------------------------------- #
# Wave-6 RECT-ALPHA (VB-079): pinned moment estimator                    #
# --------------------------------------------------------------------- #

def test_vb079_estimate_moments_biased_pearson_convention():
    """estimate_moments must return biased plug-in Pearson moments: g4 ~ 3.0
    for normal data (NOT scipy's excess ~0), and ddof=0 dispersion."""
    rng = np.random.default_rng(77)
    r = rng.normal(0.0005, 0.01, 5000)
    sr, g3, g4 = estimate_moments(r)
    # Hand-check Sharpe against ddof=0.
    assert sr == pytest.approx(float(np.mean(r) / np.std(r)), rel=1e-12)
    # Normal population -> biased Pearson kurtosis ~ 3, skew ~ 0.
    assert abs(g3) < 0.15
    assert 2.7 < g4 < 3.3


def test_vb079_estimate_moments_feeds_psr_end_to_end():
    r = np.concatenate([
        np.random.default_rng(5).normal(-0.01, 0.02, 250),
        np.random.default_rng(6).normal(0.02, 0.01, 250),
    ])
    sr, g3, g4 = estimate_moments(r)
    psr = probabilistic_sharpe_ratio(
        benchmark_sharpe=0.0,
        sharpe_observed=sr,
        n_observations=len(r),
        skew=g3,
        kurtosis=g4,
    )
    # Strong positive edge vs zero benchmark -> near-certain PSR.
    assert psr > 0.99
    dsr = deflated_sharpe_ratio(
        sharpe_observed=sr,
        n_trials=100,
        n_observations=len(r),
        skew=g3,
        kurtosis=g4,
        var_sharpe_trials=1e-6,
    )
    assert dsr > psr * 0.5  # sanity: deflation applied on same scale


def test_vb079_estimate_moments_rejects_degenerate_inputs():
    with pytest.raises(ValueError, match=">= 2"):
        estimate_moments([1.0])
    with pytest.raises(ValueError, match="NaN"):
        estimate_moments(np.array([0.1, np.nan, 0.2]))
    with pytest.raises(ValueError, match="zero-variance"):
        estimate_moments([1.0, 1.0, 1.0])
