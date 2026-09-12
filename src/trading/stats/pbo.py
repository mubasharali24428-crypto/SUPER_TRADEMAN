"""Probability of Backtest Overfitting (PBO) via CSCV.

Implements Combinatorially Symmetric Cross-Validation (CSCV) from
Bailey, Borwein, Lopez de Prado & Zhu (2017), "The Probability of Backtest
Overfitting", Journal of Computational Finance 20(4), pp. 39-69.

Method
------
Given a matrix ``M`` of shape ``(n_observations, n_strategies)`` holding each
strategy/trial's per-period returns:

1. Slice the observation axis into ``T`` contiguous blocks (default 16).
2. Form every combination of ``T choose T/2`` blocks as the in-sample (IS)
   set; the complementary blocks form the out-of-sample (OOS) set. For very
   large combination spaces a seeded random subset (default 1000 splits,
   ``CSCVConfig.max_splits``) is used instead.
3. For each split, select the IS-best strategy ``j* = argmax_j mean(M_IS[:, j])``
   and record, on the OOS blocks only, the cross-sectional rank of ``j*`` at
   every OOS observation against all other strategies (matrix ``R`` of the
   paper; rank 1 = best). The relative rank ``lambda = mean(R[:, j*]) / (N + 1)``
   lies in (0, 1) and equals 0.5 exactly when the IS-best strategy sits at the
   OOS field median; ``lambda > 0.5`` means it fell into the *worse* half.
4. ``PBO = P(lambda > 0.5)`` — the fraction of splits where the IS-best
   strategy lands in the *worse* half of the OOS field. Per-split
   logit-transformed ranks ``ln(lambda / (1 - lambda))`` are also reported;
   PBO equivalently equals the fraction of positive logits.

A PBO near 0 means IS winners generalize OOS; near 0.5 means selection is no
better than coin-flipping; above 0.5 indicates systematic backtest overfitting.
"""

from __future__ import annotations

import itertools
import logging
import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy.stats import rankdata

logger = logging.getLogger("trading.stats.pbo")

__all__ = [
    "CSCVConfig",
    "PBOResult",
    "compute_pbo_cscv",
    "compute_pbo",  # deprecated legacy alias (kept for backward compat)
]

_LOGIT_EPS = 1e-12


@dataclass(frozen=True)
class CSCVConfig:
    """Configuration for combinatorially symmetric cross-validation."""

    n_blocks: int = 16                 # T: number of contiguous sub-matrices
    max_splits: int = 1000             # cap on evaluated C(T, T/2) splits
    metric: str = "mean"               # "mean" or "sharpe" IS selection metric
    random_state: int = 42             # seed for split subsampling

    def __post_init__(self) -> None:
        if self.n_blocks < 4 or self.n_blocks % 2 != 0:
            raise ValueError(
                f"n_blocks must be an even integer >= 4, got {self.n_blocks}"
            )
        if self.max_splits < 1:
            raise ValueError(f"max_splits must be >= 1, got {self.max_splits}")
        if self.metric not in ("mean", "sharpe"):
            raise ValueError(f"metric must be 'mean' or 'sharpe', got {self.metric!r}")


@dataclass(frozen=True)
class PBOResult:
    """Outcome of a CSCV probability-of-backtest-overfitting computation."""

    pbo: float                          # P(lambda > 0.5)
    n_strategies: int
    n_blocks: int
    n_splits_evaluated: int             # number of combinations actually used
    n_splits_total: int                 # full C(T, T/2) combination count
    lambdas: np.ndarray                 # relative OOS rank of IS-best per split
    logits: np.ndarray                  # logit(lambda) per split
    is_best_indices: np.ndarray         # IS-best strategy id per split
    combinations: list = field(default_factory=list)  # IS block tuples per split
    # --- Wave-6 additions (defaulted fields keep results backward-compatible) ---
    # VB-003: per-IS-best-strategy conditional PBO, mapping
    # {strategy_id: P(lambda > 0.5 | that strategy won IS)}. The pooled
    # ``pbo`` averages over ALL splits, so a deterministically-condemned
    # winner on a minority of splits dilutes toward 0.5; consumers gating on
    # overfitting should also inspect ``max_conditional_pbo``.
    conditional_pbo: dict = field(default_factory=dict)
    max_conditional_pbo: float = 0.0
    # VB-044: binomial standard error sqrt(p*(1-p)/n_splits_evaluated) of the
    # pooled estimate. A single-split run carries stderr ~0.5 regardless of
    # outcome and must not be read as a converged result.
    pbo_stderr: float = 0.0


def _is_scores(block_matrix: np.ndarray, metric: str) -> np.ndarray:
    """Per-strategy IS performance scores for a stacked IS-block matrix."""
    if metric == "sharpe":
        mu = block_matrix.mean(axis=0)
        sd = block_matrix.std(axis=0, ddof=1)
        # Degenerate-field guard (wave-6 VB-012): zero-variance trials score
        # -inf and can never win argmax; if EVERY trial is flat, argmax would
        # deterministically elect strategy 0 as IS-best and poison j* (hence
        # every downstream lambda). Fewer than 2 positive-variance strategies
        # means there is nothing to rank — refuse loudly instead.
        if int((sd > 0).sum()) < 2:
            raise ValueError(
                "metric='sharpe' requires at least 2 strategies with positive "
                f"return variance in-sample; got {int((sd > 0).sum())} "
                "(degenerate all-flat field)."
            )
        out = np.divide(
            mu, sd, out=np.full_like(mu, -np.inf), where=sd > 0
        )
        # Deterministic ordering guard: replace non-finite extremes with large
        # finite sentinels so argmax remains well-defined and stable.
        out = np.where(np.isposinf(out), np.finfo(float).max / 4, out)
        return out
    return block_matrix.mean(axis=0)


def compute_pbo_cscv(
    return_matrix: np.ndarray | list[list[float]],
    config: CSCVConfig | None = None,
) -> PBOResult:
    """Probability of Backtest Overfitting via combinatorial CSCV.

    Args:
        return_matrix: 2D array of shape ``(n_observations, n_strategies)``
            with each trial/strategy's per-period returns (rows = time).
        config: Optional :class:`CSCVConfig`.

    Returns:
        :class:`PBOResult` with ``pbo``, per-split ``lambdas`` (relative OOS
        rank of the IS-best strategy, in (0, 1]) and their ``logits``.

    Raises:
        ValueError: On malformed input (wrong dims, too few observations or
            strategies).
    """
    cfg = config or CSCVConfig()
    M = np.asarray(return_matrix, dtype=float)
    if M.ndim != 2:
        raise ValueError(
            f"return_matrix must be 2D (n_observations, n_strategies); got 1D/wrong "
            f"shape {M.shape}"
        )
    n_obs, n_strats = M.shape
    T = cfg.n_blocks
    if not np.isfinite(M).all():
        raise ValueError("return_matrix contains NaN/inf entries")
    if n_obs < 2 * T:
        raise ValueError(
            f"Need at least {2 * T} observations for {T} CSCV blocks, got {n_obs}"
        )
    if n_strats < 2:
        raise ValueError(
            f"Need at least 2 strategies/trials to measure overfitting, got {n_strats}"
        )

    # 1. Contiguous blocks (array_split tolerates non-divisible lengths).
    blocks = np.array_split(np.arange(n_obs), T)

    # 2. All C(T, T/2) IS/OOS symmetric splits, subsampled if needed.
    half = T // 2
    all_combos = list(itertools.combinations(range(T), half))
    n_total = len(all_combos)
    if n_total > cfg.max_splits:
        rng = np.random.default_rng(cfg.random_state)
        picked = rng.choice(n_total, size=cfg.max_splits, replace=False)
        combos = [all_combos[i] for i in sorted(picked)]
        logger.info(
            "CSCV: sampled %d of %d combinations (seed=%d)",
            cfg.max_splits, n_total, cfg.random_state,
        )
    else:
        combos = all_combos

    lambdas = np.empty(len(combos), dtype=float)
    is_best = np.empty(len(combos), dtype=int)

    for k, is_combo in enumerate(combos):
        is_mask = np.zeros(T, dtype=bool)
        is_mask[list(is_combo)] = True
        is_rows = np.concatenate([blocks[b] for b in is_combo])
        oos_rows = np.concatenate([blocks[b] for b in np.flatnonzero(~is_mask)])

        # 3a. IS-best strategy under the chosen selection metric.
        j_star = int(np.argmax(_is_scores(M[is_rows], cfg.metric)))
        is_best[k] = j_star

        # 3b. Cross-sectional OOS ranks of every strategy at every OOS step
        #     (paper's R matrix): rank 1 = BEST of the field at that step
        #     (rankdata on negated returns). lambda = mean(R[:, j*]) / (N + 1)
        #     lies in (0, 1) and equals 0.5 when the IS-best sits exactly at
        #     the OOS field median; lambda > 0.5 <=> the IS-best landed in the
        #     WORSE half OOS (the overfitting event). The (N+1) normalization
        #     centers the null logit at exactly 0; a plain /N would push the
        #     midpoint upward (lambda -> 1 under ties) and invert PBO.
        R = rankdata(-M[oos_rows], method="average", axis=1)
        lam_rel = float(R[:, j_star].mean()) / (n_strats + 1)  # in (0, 1)
        lambdas[k] = lam_rel

    # 4. Logit transform and PBO = P(lambda > 0.5).
    clipped = np.clip(lambdas, _LOGIT_EPS, 1.0 - _LOGIT_EPS)
    logits = np.log(clipped / (1.0 - clipped))
    pbo = float(np.mean(logits > 0.0))

    # VB-003: group per-split overfitting outcomes by IS-best strategy so a
    # concentrated condemnation stays visible next to the diluted pool.
    conditional_pbo: dict[int, float] = {
        int(strat): float(np.mean(logits[is_best == strat] > 0.0))
        for strat in np.unique(is_best)
    }
    max_conditional_pbo = max(conditional_pbo.values())

    # VB-044: reliability floor — warn when too few splits are evaluated for
    # the pooled fraction to carry statistical meaning.
    n_eval = len(combos)
    pbo_stderr = float(np.sqrt(pbo * (1.0 - pbo) / n_eval))
    if n_eval < 20:
        warnings.warn(
            f"CSCV PBO evaluated on only {n_eval} split(s); binomial standard "
            f"error is {pbo_stderr:.3f}, so the point estimate is degenerate. "
            "Increase CSCVConfig.max_splits / n_blocks for a meaningful PBO.",
            stacklevel=2,
        )

    return PBOResult(
        pbo=pbo,
        n_strategies=n_strats,
        n_blocks=T,
        n_splits_evaluated=len(combos),
        n_splits_total=n_total,
        lambdas=lambdas,
        logits=logits,
        is_best_indices=is_best,
        combinations=[tuple(c) for c in combos],
        conditional_pbo=conditional_pbo,
        max_conditional_pbo=max_conditional_pbo,
        pbo_stderr=pbo_stderr,
    )


def compute_pbo(metrics: np.ndarray | list[float] | list[list[float]]) -> float:
    """DEPRECATED legacy heuristic PBO — superseded by :func:`compute_pbo_cscv`.

    This function does **not** implement Bailey-et-al combinatorial
    cross-validation; it treats the input as pre-computed per-split scores.
    New code must build the full ``(n_observations, n_strategies)`` return
    matrix and call::

        result = compute_pbo_cscv(M, CSCVConfig())
        result.pbo

    Kept as an alias raising :class:`DeprecationWarning`; behavior is unchanged
    for existing callers.
    """
    warnings.warn(
        "compute_pbo() is deprecated: it does not implement Bailey-et-al CSCV. "
        "Use compute_pbo_cscv(return_matrix, CSCVConfig()) on the raw "
        "(n_observations, n_strategies) return matrix instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    arr = np.asarray(metrics, dtype=float)

    if arr.size == 0:
        return 0.0

    if arr.ndim == 1:
        return float(np.mean(arr <= 0.0))

    if arr.ndim == 2:
        n_splits, n_strategies = arr.shape
        if n_strategies <= 1:
            return 0.0

        underperformed_count = 0
        for s in range(n_splits):
            split_scores = arr[s]
            best_idx = int(np.argmax(split_scores))
            median_score = float(np.median(split_scores))
            if split_scores[best_idx] <= median_score:
                underperformed_count += 1

        return float(underperformed_count / n_splits)

    raise ValueError(f"Expected 1D or 2D metrics array, got shape {arr.shape}")
