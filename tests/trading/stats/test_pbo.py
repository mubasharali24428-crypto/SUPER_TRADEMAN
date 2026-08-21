"""Tests for PBO (Probability of Backtest Overfitting) computation."""

import numpy as np
import pytest

from trading.stats.pbo import compute_pbo


def test_compute_pbo_1d():
    # If out-of-sample relative metrics are negative 50% of the time:
    metrics = np.array([1.5, -0.2, 0.8, -1.0])
    pbo = compute_pbo(metrics)
    assert pbo == 0.5


def test_compute_pbo_2d():
    # Matrix of 4 splits x 3 strategies
    # Split 0: strat 0 is best IS and retains high OOS score
    # Split 1: strat 0 is best IS but has lowest OOS score
    # Split 2: strat 1 is best IS but underperforms median
    # Split 3: strat 2 is best IS and retains high OOS score
    metrics = np.array([
        [2.0, 1.0, 0.5],   # Top choice (idx 0) score 2.0 > median (1.0)
        [0.1, 1.5, 2.0],   # Top choice (idx 2) score 2.0 > median (1.5)
        [0.2, -0.5, 0.8],  # Top choice (idx 2) score 0.8 > median (0.2)
        [-1.0, 0.0, -0.5], # Top choice (idx 1) score 0.0 > median (-0.5)
    ])
    pbo = compute_pbo(metrics)
    assert 0.0 <= pbo <= 1.0


def test_compute_pbo_empty():
    assert compute_pbo([]) == 0.0
