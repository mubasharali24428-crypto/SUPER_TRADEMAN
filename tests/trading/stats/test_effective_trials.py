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
