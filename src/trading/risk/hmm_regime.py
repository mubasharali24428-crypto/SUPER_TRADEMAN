import logging
import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

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


class HMMRegimeClassifier:
    """Hidden Markov Model (HMM) 3-State Market Regime Classifier.

    Uses `hmmlearn.hmm.GaussianHMM` to segment asset returns and volatility into 3 states:
      0: Trending Bullish (Positive return, moderate vol)
      1: Volatile Bearish (Negative return, high vol)
      2: Choppy Sideways (Near-zero return, low/moderate vol)
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

    def fit_predict(self, prices_or_returns: Sequence[float], is_returns: bool = False) -> HMMRegimeResult:
        """Fits Gaussian HMM on observation sequence and returns state probabilities & current regime.

        Args:
            prices_or_returns: Sequence of asset prices or log-returns.
            is_returns: True if input is already log-returns.

        Returns:
            HMMRegimeResult containing regime classification, confidence, and transition matrix.
        """
        arr = np.array(prices_or_returns, dtype=float)
        if len(arr) < self.min_history_length:
            return self._fallback_regime(arr, is_returns=is_returns)

        if not is_returns:
            returns = np.diff(np.log(arr))
        else:
            returns = arr

        if len(returns) < self.min_history_length - 1:
            return self._fallback_regime(arr, is_returns=is_returns)

        try:
            from hmmlearn.hmm import GaussianHMM

            # Feature 1: Log Returns
            # Feature 2: 5-period Rolling Standard Deviation
            window = 5
            rolling_std = np.zeros_like(returns)
            for i in range(len(returns)):
                start_idx = max(0, i - window + 1)
                rolling_std[i] = np.std(returns[start_idx : i + 1]) if i > 0 else 0.01

            # Prepare feature matrix X shape (N_samples, N_features)
            X = np.column_stack([returns, rolling_std])

            model = GaussianHMM(
                n_components=self.n_components,
                covariance_type="diag",
                n_iter=100,
                random_state=self.random_state,
                init_params="stmc",
            )
            model.fit(X)

            # Predict posterior state probabilities for each time step
            probs = model.predict_proba(X)
            latest_probs = probs[-1]  # Posterior distribution at current time T

            # Map hidden state IDs to canonical regimes based on state mean return and volatility
            means = model.means_  # Shape (n_components, 2) -> [mean_return, mean_std]
            state_mapping = self._align_states(means)

            # Re-order probabilities according to canonical mapping: [bull, bear, chop]
            mapped_probs = [0.0] * self.n_components
            for orig_state, canon_idx in state_mapping.items():
                mapped_probs[canon_idx] = float(latest_probs[orig_state])

            best_canon_idx = int(np.argmax(mapped_probs))
            current_regime = self.REGIME_NAMES[best_canon_idx]
            confidence = float(mapped_probs[best_canon_idx])

            # Transition matrix
            trans_mat = model.transmat_.tolist()

            is_trending = current_regime == "trending_bull"
            is_high_vol = current_regime == "volatile_bear"

            return HMMRegimeResult(
                current_regime=current_regime,
                regime_id=best_canon_idx,
                state_probabilities=mapped_probs,
                transition_matrix=trans_mat,
                is_trending=is_trending,
                is_high_volatility=is_high_vol,
                confidence=confidence,
            )

        except Exception as exc:
            logger.warning("HMM fitting failed (%s). Using fallback regime heuristic.", exc)
            return self._fallback_regime(arr, is_returns=is_returns)

    def _align_states(self, means: np.ndarray) -> dict[int, int]:
        """Aligns unsupervised HMM state IDs to canonical indices:
        0: Bullish (highest return / vol ratio)
        1: Bearish/Volatile (lowest return / highest vol)
        2: Choppy (near zero return, moderate vol)
        """
        returns_m = means[:, 0]
        vol_m = means[:, 1]

        # Bullish: highest positive return
        bull_idx = int(np.argmax(returns_m))
        
        # Bearish: lowest return or highest volatility
        bear_scores = -returns_m + vol_m
        bear_idx = int(np.argmax(bear_scores))
        if bear_idx == bull_idx:
            # If overlap, pick second lowest return for bear
            sorted_returns = np.argsort(returns_m)
            bear_idx = int(sorted_returns[0])

        remaining = [i for i in range(self.n_components) if i not in (bull_idx, bear_idx)]
        chop_idx = remaining[0] if remaining else 2

        return {bull_idx: 0, bear_idx: 1, chop_idx: 2}

    def _fallback_regime(self, arr: np.ndarray, is_returns: bool) -> HMMRegimeResult:
        """Simple heuristic fallback regime classifier."""
        if len(arr) < 2:
            return HMMRegimeResult(
                current_regime="choppy_sideways",
                regime_id=2,
                state_probabilities=[0.33, 0.33, 0.34],
                transition_matrix=[[0.8, 0.1, 0.1], [0.1, 0.8, 0.1], [0.1, 0.1, 0.8]],
                is_trending=False,
                is_high_volatility=False,
                confidence=0.34,
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
        )
