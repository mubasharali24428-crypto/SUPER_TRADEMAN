"""Tests for Effective Number of Independent Trials computation."""

import numpy as np
import pytest

from trading.stats.effective_trials import effective_trials


def test_effective_trials_uncorrelated():
    # 5 uncorrelated trial return series of 100 observations
    np.random.seed(42)
    returns = np.random.randn(100, 5)
    n_eff = effective_trials(returns)
    assert 4.0 <= n_eff <= 5.0


def test_effective_trials_perfectly_correlated():
    # 5 identical trial return series
    col = np.random.randn(100, 1)
    returns = np.tile(col, (1, 5))
    n_eff = effective_trials(returns)
    assert pytest.approx(n_eff, abs=1e-3) == 1.0


def test_effective_trials_with_precomputed_corr():
    # N=10 trials, avg_corr=0.5
    # N_eff = 10 / (1 + 9*0.5) = 10 / 5.5 = 1.818
    n_eff = effective_trials(10, avg_corr=0.5)
    assert pytest.approx(n_eff, abs=1e-2) == 1.818


# --------------------------------------------------------------------- #
# Sub-06 rigor: fail loudly on invalid denominators; NaN pairs missing   #
# --------------------------------------------------------------------- #


def test_nonpositive_denominator_raises_instead_of_returning_full_n():
    # Old bug: N=10, avg_corr=-0.2 -> denom = 1 + 9*(-0.2) = -0.8 <= 0
    # silently returned the FULL N (10.0), inflating deflation headroom.
    with pytest.raises(ValueError, match="denominator"):
        effective_trials(10, avg_corr=-0.2)


def test_boundary_denominator_exactly_zero_raises():
    # N=5, avg_corr=-0.25 -> denom = 1 + 4*(-0.25) = 0 exactly.
    with pytest.raises(ValueError, match="avg_corr must be"):
        effective_trials(5, avg_corr=-0.25)


def test_admissible_negative_corr_still_works():
    # Legacy semantics preserved for admissible negative correlations:
    # N=10, rho=-0.05 -> denom = 1 + 9*(-0.05) = 0.55 -> N_eff = 18.18.
    # (N_eff > N is intentional under negative correlation; the [1, N] clip
    # applies only to the correlation-matrix estimation path.)
    n_eff = effective_trials(10, avg_corr=-0.05)
    assert n_eff == pytest.approx(10 / 0.55)


def test_nan_pairs_treated_as_missing_conservative():
    # Two constant series => their correlation pairs are undefined (NaN).
    rng = np.random.randn
    base = np.column_stack(
        [
            rng(80),  # trial 0: random
            rng(80),  # trial 1: random
            np.full(80, 7.7),  # constant -> zero variance
            np.full(80, -2.2),  # constant -> zero variance
        ]
    )
    n_eff = effective_trials(base)
    # Must NOT crash and must NOT treat NaN pairs as rho=0; with only one
    # valid pair (trials 0-1, ~uncorrelated) rho_bar~0 -> N_eff ~ N is wrong;
    # the conservative floor keeps N_eff within [1, N] and finite.
    assert 1.0 <= n_eff <= 4.0


def test_all_constant_series_returns_one():
    # Every pair undefined -> maximally conservative N_eff = 1.
    M = np.tile(np.arange(50.0).reshape(-1, 1) * 0 + 3.14, (1, 4))
    n_eff = effective_trials(M)
    assert n_eff == 1.0


def test_mixed_valid_and_constant_trials_uses_valid_pairs_only():
    rng = np.random.default_rng(5)
    shared = rng.normal(0.001, 0.01, 200)
    M = np.column_stack(
        [
            shared,  # perfectly correlated pair...
            shared,
            rng.normal(0.000, 0.02, 200),
            np.full(200, 1.23),  # constant: excluded from averaging
        ]
    )
    n_eff = effective_trials(M)
    assert np.isfinite(n_eff) and 1.0 <= n_eff <= 4.0


def test_single_column_and_empty_inputs():
    assert effective_trials(np.zeros((30, 1))) == 1.0
    assert effective_trials([]) == 0.0
    assert effective_trials(np.empty((0, 0))) == 0.0
