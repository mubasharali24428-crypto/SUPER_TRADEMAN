import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
from scipy.special import logsumexp

logger = logging.getLogger("trading.risk.hmm")


@dataclass(frozen=True)
class HMMRegimeResult:
    """Output of HMM market regime classification."""
    current_regime: str           # "trending_bull" | "volatile_bear" | "choppy_sideways"
    regime_id: int                # 0, 1, or 2
    state_probabilities: list[float] # Posterior probabilities [p_bull, p_bear, p_chop]
    transition_matrix: list[list[float]] # Estimated 3x3 state transition matrix
    is_trending: bool
    is_high_volatility: bool
    confidence: float             # Probability of the most likely state
    # --- Sub-06 rigor additions (additive, defaulted for backward compat) ---
    # "model": fitted HMM converged; probabilities are causal filtered posteriors.
    # "fallback": convergence failed / insufficient data / fit error -> last-known
    #             regime or heuristic, NOT a live model estimate.
    provenance: str = "fallback"
    converged: bool = False


class HMMRegimeClassifier:
    """Hidden Markov Model (HMM) 3-State Market Regime Classifier.

    Uses `hmmlearn.hmm.GaussianHMM` to segment asset returns and volatility into 3 states:
      0: Trending Bullish (Positive return, moderate vol)
      1: Volatile Bearish (Negative return, high vol)
      2: Choppy Sideways (Near-zero return, low/moderate vol)

    Rigor notes (Sub-06):
      * Fit convergence is verified via ``monitor_.converged``; one retry with a
        different ``random_state`` is attempted before declaring failure.
      * Live inference uses a FORWARD-ONLY filtered posterior p(z_T | x_1:T),
        computed by an explicit forward recursion over fitted parameters — the
        causal equivalent of evaluating ``score_samples`` on every growing
        prefix and reading its final-step posterior. In-sample smoothed
        ``predict_proba`` output is never used for the live estimate.
      * Rolling-volatility features DROP the first ``window - 1`` samples
        instead of fabricating a constant 0.01 volatility seed.
    """

    REGIME_NAMES = ["trending_bull", "volatile_bear", "choppy_sideways"]

    def __init__(
        self,
        n_components: int = 3,
        min_history_length: int = 40,
        random_state: int = 42,
    ):
        self.n_components = n_components
        self.min_history_length = min_history_length
        self.random_state = random_state
        self.last_result_: HMMRegimeResult | None = None
        # Wave-6 VB-015: cache the fitted EM model so a stateful daemon loop
        # (heartbeat) does not re-run full EM fitting every tick on an
        # append-only price buffer. The forward-only filtered posterior — the
        # causally correct live estimate — is still recomputed each call.
        # ``Any`` because hmmlearn's GaussianHMM is untyped; ``None`` until the
        # first successful, sanity-gated fit.
        self._cached_model: Any = None
        self._cached_feature_rows: np.ndarray | None = None

    # ------------------------------------------------------------------ #
    # Feature construction                                                #
    # ------------------------------------------------------------------ #

    def _build_features(self, returns: np.ndarray) -> np.ndarray:
        """Stacks [log-return, rolling vol] features, dropping warm-up samples.

        The first ``window - 1`` samples cannot form an honest rolling
        standard deviation, so they are dropped rather than seeded with an
        invented value (the old code planted 0.01 on the first element).
        """
        window = 5
        n = len(returns)
        if n < window:
            raise ValueError(
                f"Need >= {window} returns to build rolling-vol features, got {n}"
            )
        rolling_std = np.empty(n, dtype=float)
        for i in range(n):
            lo = i - window + 1
            seg = returns[max(0, lo): i + 1]
            rolling_std[i] = np.std(seg)
        # Honest samples start where a full window exists (index window-1).
        X = np.column_stack([returns, rolling_std])[window - 1:]
        if not np.isfinite(X).all():
            raise ValueError("Non-finite values in HMM feature matrix")
        return X

    # ------------------------------------------------------------------ #
    # Fitting with convergence verification                               #
    # ------------------------------------------------------------------ #

    def _fit_with_convergence_check(self, X: np.ndarray):
        """Fits GaussianHMM, verifying EM convergence; retries once.

        Returns ``(model, converged)`` where ``converged`` reflects
        ``monitor_.converged`` of the best attempt.
        """
        from hmmlearn.hmm import GaussianHMM

        attempts = (
            (self.random_state, True),
            (self.random_state + 1, False),
        )
        model = None
        converged = False
        for rs, first in attempts:
            candidate = GaussianHMM(
                n_components=self.n_components,
                covariance_type="diag",
                n_iter=100,
                random_state=rs,
                init_params="stmc",
            )
            candidate.fit(X)
            converged = bool(getattr(candidate.monitor_, "converged", False))
            logger.info(
                "HMM fit attempt (random_state=%s): converged=%s (%d iterations)",
                rs, converged, candidate.monitor_.iter,
            )
            if converged:
                return candidate, True
            if first:
                logger.warning(
                    "HMM EM failed to converge (random_state=%s); retrying once "
                    "with random_state=%s.",
                    rs, self.random_state + 1,
                )
            model = candidate
        return model, False

    # ------------------------------------------------------------------ #
    # Forward-only (causal) live posterior                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _observation_log_likelihood(model, X: np.ndarray) -> np.ndarray:
        """Per-state diagonal-Gaussian log densities, shape (T, K)."""
        means = np.asarray(model.means_, dtype=float)              # (K, D)
        covars_raw = np.asarray(model.covars_, dtype=float)
        if covars_raw.ndim == 3:
            # Some hmmlearn versions store (K, D, D) even for 'diag';
            # extract each state's diagonal variances.
            covars = np.diagonal(covars_raw, axis1=1, axis2=2)
        else:
            covars = covars_raw                                    # (K, D)
        covars = np.maximum(np.squeeze(covars), 1e-12)
        diff = X[:, None, :] - means[None, :, :]                   # (T, K, D)
        mahal = np.sum(diff * diff / covars[None, :, :], axis=2)   # (T, K)
        _, D = means.shape
        log_norm = -0.5 * (
            D * np.log(2.0 * np.pi) + np.sum(np.log(covars), axis=1)[None, :]
        )
        return log_norm - 0.5 * mahal

    @classmethod
    def _forward_filtered_posterior(cls, model, X: np.ndarray) -> np.ndarray:
        """Filtered posteriors p(z_t | x_1:t) for all t via forward recursion.

        Causal by construction (no backward pass, no future information).
        Equivalent to running ``model.score_samples(X[: t + 1])`` on every
        growing prefix and keeping only its FINAL-step posterior — computed
        here in a single O(T*K^2) pass. The last row is the live posterior.
        """
        log_start = np.log(np.clip(np.asarray(model.startprob_, dtype=float), 1e-300, None))
        log_trans = np.log(np.clip(np.asarray(model.transmat_, dtype=float), 1e-300, None))
        emit_ll = cls._observation_log_likelihood(model, X)

        K = log_start.shape[0]
        T = len(X)
        log_alpha = np.empty((T, K), dtype=float)
        log_alpha[0] = log_start + emit_ll[0]
        for t in range(1, T):
            # a_t(j) = [ sum_i a_{t-1}(i) A_ij ] * B_t(j)
            log_alpha[t] = emit_ll[t] + logsumexp(
                log_alpha[t - 1][:, None] + log_trans, axis=0
            )
        # Wave-6 VB-081: normalization is DELIBERATELY a single row-wise
        # logsumexp subtraction at the end, not per-step rescaling. exp() is
        # only ever taken of (log_alpha - norm), i.e. ratios of entries that
        # share each row's additive offset, so the posterior is invariant to
        # how far the unnormalized alphas drift down with cumulative
        # log-likelihood — they drift TOGETHER and their differences stay
        # bounded, well inside float64 range for any realistic window length.
        # Do NOT "optimize" this into per-step scaling: emitting exp() of raw
        # log_alpha entries (without the shared-offset subtraction) would
        # underflow them to 0 and silently flatten posteriors to uniform.
        norm = logsumexp(log_alpha, axis=1, keepdims=True)
        return np.exp(log_alpha - norm)

    # ------------------------------------------------------------------ #
    # Fitted-model cache (wave-6 VB-015)                                  #
    # ------------------------------------------------------------------ #

    def invalidate_model_cache(self) -> None:
        """Drops the cached EM fit so the next fit_predict() refits from scratch.

        Daemon callers should invoke this on a fixed schedule (e.g. once per
        N-th heartbeat tick) or on regime-shift alerts, instead of paying a
        full EM refit on EVERY tick. Between invalidations the cached fit is
        reused whenever the feature matrix is an append-only extension of the
        rows it was fitted on.
        """
        self._cached_model = None
        self._cached_feature_rows = None

    def _cache_matches(self, X: np.ndarray) -> bool:
        """True when X extends the cached fit's feature rows prefix-exactly."""
        cached = self._cached_feature_rows
        return (
            self._cached_model is not None
            and cached is not None
            and len(X) >= len(cached)
            and np.array_equal(X[: len(cached)], cached)
        )

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def fit_predict(self, prices_or_returns: Sequence[float] | np.ndarray, is_returns: bool = False) -> HMMRegimeResult:
        """Fits Gaussian HMM on observation sequence and returns state probabilities & current regime.

        Args:
            prices_or_returns: Sequence of asset prices or log-returns.
            is_returns: True if input is already log-returns.

        Returns:
            HMMRegimeResult containing regime classification, confidence, and transition matrix.
            ``result.provenance == "fallback"`` signals the estimate did NOT come
            from a converged model (insufficient data, non-convergence after
            retry, or fit error) and should not be trusted as a live read.
        """
        arr = np.array(prices_or_returns, dtype=float)
        if not is_returns:
            returns = np.diff(np.log(arr))
        else:
            returns = arr

        # Wave-6 VB-045: ONE history guard, applied AFTER return conversion, so
        # prices-input and returns-input admit identical usable-return counts
        # (the old pair of guards demanded one more sample from returns input
        # than from prices input for the same feature yield).
        if len(returns) < self.min_history_length:
            return self._fallback_regime(arr, is_returns=is_returns)

        try:
            X = self._build_features(returns)
            if len(X) < max(10, self.n_components * 4):
                raise ValueError(
                    f"Only {len(X)} usable feature samples after dropping "
                    "rolling-window warm-up; too few to identify regimes."
                )

            # Wave-6 VB-015: reuse the cached converged fit when features are
            # an append-only extension of its training rows; otherwise run EM
            # (with convergence verification) and re-cache only after the fit
            # passes the degenerate-posterior sanity gate below, so a collapsed
            # fit can never stick in the cache.
            if self._cache_matches(X):
                model = self._cached_model
                assert self._cached_feature_rows is not None  # guaranteed by _cache_matches
                converged = True
                logger.info(
                    "HMM: reusing cached fit (%d cached feature rows; %d now).",
                    len(self._cached_feature_rows), len(X),
                )
            else:
                model, converged = self._fit_with_convergence_check(X)
                if not converged:
                    logger.error(
                        "HMM EM did not converge after initial fit and one retry "
                        "(random_states %s/%s). Returning last-known regime flagged "
                        "provenance='fallback'.",
                        self.random_state, self.random_state + 1,
                    )
                    return self._last_known_fallback()
                if model is None:  # defensive: cannot happen when converged
                    raise RuntimeError("HMM fit returned no model despite convergence")

            # LIVE inference: forward-only filtered posterior at time T.
            filtered = self._forward_filtered_posterior(model, X)
            latest_probs_raw = filtered[-1]

            # Wave-6 VB-032: sanity gate on SUCCESSFUL fits. Convergence does
            # not guarantee quality — a degenerate EM outcome can route ALL
            # samples into a single hidden state without ever raising,
            # producing confident nonsense with provenance='model'. Count
            # ACTIVE states (non-trivial time-averaged posterior mass): an
            # unused extra component on 2-regime data is normal, but fewer
            # than two active states means the model identified no regime
            # structure at all -> fall back instead of trusting it.
            weights = filtered.mean(axis=0)
            active = weights >= 1e-8
            if (
                not np.isfinite(latest_probs_raw).all()
                or not np.isfinite(weights).all()
                or int(active.sum()) < 2
            ):
                logger.warning(
                    "HMM fit produced a degenerate posterior (%d/%d active "
                    "states over %d samples); treating as fallback.",
                    int(active.sum()), len(weights),
                    len(X),
                )
                return self._last_known_fallback()

            if not self._cache_matches(X):
                self._cached_model = model
                self._cached_feature_rows = X.copy()

            # Map hidden state IDs to canonical regimes based on state mean return and volatility
            means = model.means_  # Shape (n_components, 2) -> [mean_return, mean_std]
            state_mapping = self._align_states(means)

            # Re-order probabilities according to canonical mapping: [bull, bear, chop]
            mapped_probs = [0.0] * self.n_components
            for orig_state, canon_idx in state_mapping.items():
                mapped_probs[canon_idx] = float(latest_probs_raw[orig_state])
            total = float(sum(mapped_probs))
            mapped_probs = [p / total for p in mapped_probs]  # guard float drift

            best_canon_idx = int(np.argmax(mapped_probs))
            current_regime = self.REGIME_NAMES[best_canon_idx]
            confidence = float(mapped_probs[best_canon_idx])

            # Transition matrix
            trans_mat = np.asarray(model.transmat_).tolist()

            is_trending = current_regime == "trending_bull"
            is_high_vol = current_regime == "volatile_bear"

            result = HMMRegimeResult(
                current_regime=current_regime,
                regime_id=best_canon_idx,
                state_probabilities=mapped_probs,
                transition_matrix=trans_mat,
                is_trending=is_trending,
                is_high_volatility=is_high_vol,
                confidence=confidence,
                provenance="model",
                converged=True,
            )
            self.last_result_ = result
            return result

        except Exception as exc:
            logger.warning("HMM fitting failed (%s). Using fallback regime heuristic.", exc)
            return self._fallback_regime(arr, is_returns=is_returns)

    def _align_states(self, means: np.ndarray) -> dict[int, int]:
        """Aligns unsupervised HMM state IDs to canonical indices:
        0: Bullish (highest return / vol ratio)
        1: Bearish/Volatile (lowest return / highest vol)
        2: Choppy (near zero return, moderate vol)

        Wave-6 VB-013 hardening:
          * The heuristic is written for exactly the 3 canonical regimes;
            any other component count would leave canonical slots silently
            unmapped, so it is refused up front.
          * Bear scoring normalizes both features (min-max to comparable
            ranges) before combining — raw mean log-return and rolling-vol
            live on different units, so an unnormalized ``-return + vol``
            let whichever feature had the larger numeric scale dominate.
          * If the bear pick collides with bull, the runner-up is chosen by
            masked re-argmax instead of "lowest return", which under unit
            mixing could hand bull's slot to a high-return state.
        """
        if self.n_components != len(self.REGIME_NAMES):
            raise ValueError(
                f"_align_states maps exactly {len(self.REGIME_NAMES)} canonical "
                f"regimes; got n_components={self.n_components}."
            )

        returns_m = means[:, 0]
        vol_m = means[:, 1]

        def _unit_scaled(values: np.ndarray) -> np.ndarray:
            span = float(values.max() - values.min())
            if span <= 0.0:
                return np.zeros_like(values)
            return (values - values.min()) / span

        # Bullish: highest positive return
        bull_idx = int(np.argmax(returns_m))

        # Bearish: lowest return AND/OR highest volatility, compared on a
        # common normalized scale.
        bear_scores = -_unit_scaled(returns_m) + _unit_scaled(vol_m)
        bear_idx = int(np.argmax(bear_scores))
        if bear_idx == bull_idx:
            bear_scores = bear_scores.copy()
            bear_scores[bull_idx] = -np.inf
            bear_idx = int(np.argmax(bear_scores))

        remaining = [i for i in range(self.n_components) if i not in (bull_idx, bear_idx)]
        chop_idx = remaining[0]

        return {bull_idx: 0, bear_idx: 1, chop_idx: 2}

    def _last_known_fallback(self) -> HMMRegimeResult:
        """Returns the last successful model estimate, flagged as fallback."""
        if self.last_result_ is not None:
            prev = self.last_result_
            stale = HMMRegimeResult(
                current_regime=prev.current_regime,
                regime_id=prev.regime_id,
                state_probabilities=list(prev.state_probabilities),
                transition_matrix=[list(row) for row in prev.transition_matrix],
                is_trending=prev.is_trending,
                is_high_volatility=prev.is_high_volatility,
                confidence=prev.confidence,
                provenance="fallback",
                converged=False,
            )
            return stale
        return HMMRegimeResult(
            current_regime="choppy_sideways",
            regime_id=2,
            state_probabilities=[0.33, 0.33, 0.34],
            transition_matrix=[[0.8, 0.1, 0.1], [0.1, 0.8, 0.1], [0.1, 0.1, 0.8]],
            is_trending=False,
            is_high_volatility=False,
            confidence=0.34,
            provenance="fallback",
            converged=False,
        )

    def _fallback_regime(self, arr: np.ndarray, is_returns: bool) -> HMMRegimeResult:
        """Simple heuristic fallback regime classifier (provenance='fallback')."""
        if len(arr) < 2:
            return HMMRegimeResult(
                current_regime="choppy_sideways",
                regime_id=2,
                state_probabilities=[0.33, 0.33, 0.34],
                transition_matrix=[[0.8, 0.1, 0.1], [0.1, 0.8, 0.1], [0.1, 0.1, 0.8]],
                is_trending=False,
                is_high_volatility=False,
                confidence=0.34,
                provenance="fallback",
                converged=False,
            )

        if not is_returns:
            returns = np.diff(np.log(arr))
        else:
            returns = arr

        recent_return = float(np.sum(returns[-10:])) if len(returns) >= 10 else float(np.sum(returns))
        vol = float(np.std(returns)) if len(returns) > 1 else 0.02

        if recent_return > 0.02 and vol < 0.04:
            regime = "trending_bull"
            rid = 0
            probs = [0.70, 0.10, 0.20]
        elif recent_return < -0.02 or vol > 0.05:
            regime = "volatile_bear"
            rid = 1
            probs = [0.10, 0.70, 0.20]
        else:
            regime = "choppy_sideways"
            rid = 2
            probs = [0.20, 0.20, 0.60]

        return HMMRegimeResult(
            current_regime=regime,
            regime_id=rid,
            state_probabilities=probs,
            transition_matrix=[[0.8, 0.1, 0.1], [0.1, 0.8, 0.1], [0.1, 0.1, 0.8]],
            is_trending=rid == 0,
            is_high_volatility=rid == 1,
            confidence=probs[rid],
            provenance="fallback",
            converged=False,
        )
