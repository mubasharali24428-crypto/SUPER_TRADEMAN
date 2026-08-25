"""Effective Number of Independent Trials estimation.

Calculates N_eff based on the average pairwise correlation of backtest trial
returns, accounting for multi-testing / data-mining bias:

    N_eff = N / (1 + (N - 1) * rho_bar)

Missing-data policy (documented, conservative)
----------------------------------------------
- Correlation pairs involving a constant (zero-variance) series are undefined
  (NaN in ``np.corrcoef``). Such pairs are treated as **missing** and excluded
  from ``rho_bar``; the denominator then uses the count of valid pairs.
- If NO valid pair remains, ``rho_bar`` defaults to **1.0** (perfect
  correlation), i.e. ``N_eff = 1`` — the maximally conservative assumption for
  deflation purposes.
- A non-positive denominator (possible when avg_corr < -1/(N-1)) raises
  :class:`ValueError`; callers should pass correlations in the admissible
  range rather than silently receiving the full N.

Caller contract (wave-6 VB-007)
-------------------------------
The ValueError raise path is INTENTIONAL and load-bearing: any future
production call site MUST either pre-validate inputs (clip rho_bar above
the -1/(N-1) floor) or wrap the call in try/except that maps failure to a
conservative ``N_eff = N`` (uncorrected) plus a logged warning — never let
the exception escape mid-evaluation. Sample correlation matrices from
``np.corrcoef`` CAN reach the forbidden range numerically (small N,
strongly anticorrelated trials), so defensive wrapping is required, not
optional. See ``docs/stats_wire_plan.md`` for the planned first consumer.
"""

from __future__ import annotations

import numpy as np

__all__ = ["effective_trials"]


def effective_trials(
    data: np.ndarray | list[list[float]] | list[float] | int,
    avg_corr: float | None = None,
) -> float:
    """Computes the effective number of independent trials N_eff.

    Formula: N_eff = N / (1 + (N - 1) * rho_bar)

    Args:
        data: Either:
          - A 2D array of shape (n_observations, n_trials) containing return series for each trial.
          - An int N (or 1D array/list of length N) when avg_corr is provided.
        avg_corr: Optional pre-computed average pairwise correlation rho_bar in [-1, 1].

    Returns:
        N_eff float >= 1.0 (or 0.0 if empty input).

    Raises:
        ValueError: If ``avg_corr`` yields a non-positive denominator
            (rho_bar <= -1/(N-1)), or inputs are otherwise invalid.

        Callers are expected to pre-validate or catch (see module docstring,
        "Caller contract"); the raise must never surface unhandled inside a
        live evaluation loop.
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
            raise ValueError(
                f"Invalid effective-trials denominator {denom:.4f} "
                f"(N={n_trials}, avg_corr={float(avg_corr):.4f}): avg_corr must be "
                f"> -1/(N-1) = {-1.0 / (n_trials - 1):.4f}."
            )
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

        # Pairwise correlation matrix; NaNs arise for constant/zero-variance
        # series and are handled as missing data below (never coerced to 0).
        with np.errstate(invalid="ignore", divide="ignore"):
            corr_matrix = np.corrcoef(arr, rowvar=False)
        corr_matrix = np.atleast_2d(corr_matrix)

        triu_idx = np.triu_indices(n_trials, k=1)
        if len(triu_idx[0]) == 0:
            return float(n_trials)

        off_diag = corr_matrix[triu_idx]
        finite_mask = np.isfinite(off_diag)
        n_valid_pairs = int(finite_mask.sum())
        n_total_pairs = int(len(off_diag))
        if n_valid_pairs == 0:
            # All pairs undefined -> assume perfect correlation (conservative).
            return 1.0
        rho_bar = float(np.mean(off_diag[finite_mask]))
        denom = 1.0 + (n_trials - 1) * rho_bar

        if denom <= 0:
            # Numerically possible only at extreme negative average
            # correlations; refuse to inflate N_eff to the full N.
            raise ValueError(
                f"Invalid effective-trials denominator {denom:.4f} from "
                f"estimated avg_corr={rho_bar:.4f} over {n_valid_pairs}/{n_total_pairs} "
                "valid pairs."
            )

        n_eff = n_trials / denom
        if n_valid_pairs < n_total_pairs:
            # Documented conservative adjustment: pairs lost to NaN were
            # EXCLUDED (not imputed). With fewer constraints on rho_bar the
            # estimate is noisier, so we do not rescale upward; report via clip.
            pass
        return float(np.clip(n_eff, 1.0, float(n_trials)))

    raise ValueError(f"Expected 1D or 2D array for effective_trials, got shape {arr.shape}")
