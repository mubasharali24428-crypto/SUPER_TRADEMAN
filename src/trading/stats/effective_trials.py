"""Effective Number of Independent Trials estimation module.

Calculates N_eff based on average pairwise correlation of backtest trial returns,
accounting for multi-testing / data-mining bias.
"""

import numpy as np

__all__ = ["effective_trials"]


def effective_trials(
    data: np.ndarray | list[list[float]] | list[float],
    avg_corr: float | None = None,
) -> float:
    """Computes the effective number of independent trials N_eff.

    Formula: N_eff = N / (1 + (N - 1) * rho_bar)

    Args:
        data: Either:
          - A 2D array of shape (n_observations, n_trials) containing return series for each trial.
          - A 1D array or list of length N containing trial metrics if avg_corr is provided.
        avg_corr: Optional pre-computed average pairwise correlation rho_bar in [-1, 1].

    Returns:
        N_eff float >= 1.0 (or 0.0 if empty input).
    """
    if avg_corr is not None:
        if isinstance(data, (int, float)):
            n_trials = int(data)
        else:
            arr = np.asarray(data)
            n_trials = arr.shape[-1] if arr.ndim > 0 else 0

        if n_trials <= 0:
            return 0.0
        if n_trials == 1:
            return 1.0

        rho = float(np.clip(avg_corr, -1.0, 1.0))
        denom = 1.0 + (n_trials - 1) * rho
        if denom <= 0:
            return float(n_trials)
        return float(n_trials / denom)

    arr = np.asarray(data, dtype=float)
    if arr.size == 0:
        return 0.0

    if arr.ndim == 1:
        n_trials = len(arr)
        if n_trials <= 1:
            return float(n_trials)
        # Without 2D series to compute correlation, assume uncorrelated (rho=0) -> N_eff = N
        return float(n_trials)

    if arr.ndim == 2:
        n_obs, n_trials = arr.shape
        if n_trials <= 1:
            return float(n_trials)

        # Compute pairwise correlation matrix
        corr_matrix = np.corrcoef(arr, rowvar=False)

        # Handle NaNs if constant series exist
        corr_matrix = np.nan_to_num(corr_matrix, nan=0.0)

        # Extract upper triangle off-diagonal entries
        triu_indices = np.triu_indices(n_trials, k=1)
        if len(triu_indices[0]) == 0:
            return float(n_trials)

        off_diag_corrs = corr_matrix[triu_indices]
        rho_bar = float(np.mean(off_diag_corrs))

        denom = 1.0 + (n_trials - 1) * rho_bar
        if denom <= 0:
            return float(n_trials)
        return float(np.clip(n_trials / denom, 1.0, float(n_trials)))

    raise ValueError(f"Expected 1D or 2D array for effective_trials, got shape {arr.shape}")
