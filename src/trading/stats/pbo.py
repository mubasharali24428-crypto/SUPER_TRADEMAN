"""Probability of Backtest Overfitting (PBO) computation module.

Calculates the probability that the optimal in-sample strategy configuration
underperforms out-of-sample due to overfitting (Bailey, Borwein, López de Prado, Zhu).
"""

import numpy as np

__all__ = ["compute_pbo"]


def compute_pbo(metrics: np.ndarray | list[float] | list[list[float]]) -> float:
    """Computes the Probability of Backtest Overfitting (PBO).

    Args:
        metrics: Either:
          - A 2D array of shape (n_splits, n_strategies) containing out-of-sample
            performance evaluation metrics (e.g., Sharpe Ratios).
          - A 1D array/list of out-of-sample performance metrics across folds.

    Returns:
        PBO value between 0.0 and 1.0 representing the proportion of splits where
        the top in-sample strategy degraded below the median out-of-sample performance.
    """
    arr = np.asarray(metrics, dtype=float)

    if arr.size == 0:
        return 0.0

    if arr.ndim == 1:
        # If 1D performance scores (e.g. OOS Sharpe relative to benchmark or IS):
        # PBO is fraction of splits where performance is <= 0
        return float(np.mean(arr <= 0.0))

    if arr.ndim == 2:
        n_splits, n_strategies = arr.shape
        if n_strategies <= 1:
            return 0.0

        underperformed_count = 0
        for s in range(n_splits):
            split_scores = arr[s]
            # Best strategy in this split (simulated top IS choice)
            best_idx = np.argmax(split_scores)

            # Rank of top choice OOS relative to other strategies in split s
            median_score = np.median(split_scores)
            if split_scores[best_idx] <= median_score:
                underperformed_count += 1

        return float(underperformed_count / n_splits)

    raise ValueError(f"Expected 1D or 2D metrics array, got shape {arr.shape}")
