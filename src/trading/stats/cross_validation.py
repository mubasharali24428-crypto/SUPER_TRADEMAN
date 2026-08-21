"""Causal Combinatorial Purged Cross-Validation (CPCV) split generator.

Implements leakage-free time-series cross-validation respecting purge and
embargo windows to prevent statistical overfitting.
"""

from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
import pandas as pd

__all__ = [
    "CPCVConfig",
    "TrainTestSplit",
    "generate_cpcv_splits",
    "apply_split",
]


class TrainTestSplit(NamedTuple):
    train_idx: np.ndarray
    test_idx: np.ndarray


@dataclass(frozen=True)
class CPCVConfig:
    n_folds: int = 5
    purge_days: int = 1
    embargo_days: int = 1
    max_holding_days: int = 5
    signal_lookback_days: int = 0
    feature_lookback_days: int = 0
    label_horizon_days: int = 0
    min_train_size: int = 10
    min_test_size: int = 5


def generate_cpcv_splits(
    df: pd.DataFrame, cfg: CPCVConfig
) -> list[TrainTestSplit]:
    """Generates causal purged walk-forward cross-validation splits.

    For each test block k in range(1, n_folds):
      - Test set is block k.
      - Training set consists of data points strictly before test_start minus
        the total purge window (purge_days + max_holding_days + label_horizon_days).

    This guarantees strict causality: no future information leaks into the training set.
    """
    if len(df) < cfg.min_train_size + cfg.min_test_size:
        raise ValueError(
            f"Dataframe length ({len(df)}) is less than minimum required "
            f"({cfg.min_train_size} train + {cfg.min_test_size} test)"
        )

    n_samples = len(df)
    n_folds = max(2, cfg.n_folds)

    # Determine timestamps or index positions
    if isinstance(df.index, pd.DatetimeIndex):
        timestamps = df.index
    elif "timestamp" in df.columns:
        timestamps = pd.to_datetime(df["timestamp"])
    elif "time" in df.columns:
        timestamps = pd.to_datetime(df["time"])
    else:
        # Fallback: synthetic daily timestamps if integer indexed
        timestamps = pd.date_range(
            start="2020-01-01", periods=n_samples, freq="D"
        )

    total_purge_days = (
        cfg.purge_days
        + cfg.max_holding_days
        + cfg.label_horizon_days
        + cfg.signal_lookback_days
        + cfg.feature_lookback_days
    )
    purge_delta = pd.Timedelta(days=total_purge_days)
    embargo_delta = pd.Timedelta(days=cfg.embargo_days)

    block_size = n_samples // n_folds
    splits: list[TrainTestSplit] = []

    # For causal walk-forward CPCV, fold 0 is initial training baseline.
    # Folds 1..n_folds-1 serve as test blocks.
    for fold in range(1, n_folds):
        test_start_idx = fold * block_size
        test_end_idx = (
            n_samples if fold == n_folds - 1 else (fold + 1) * block_size
        )

        test_indices = np.arange(test_start_idx, test_end_idx)
        if len(test_indices) < cfg.min_test_size:
            continue

        test_start_time = timestamps[test_start_idx]

        # Causal purge: train must end before (test_start_time - purge_delta)
        cutoff_time = test_start_time - purge_delta

        # Find all valid train indices strictly before cutoff_time
        train_mask = np.array([t < cutoff_time for t in timestamps[:test_start_idx]])
        train_indices = np.arange(test_start_idx)[train_mask]

        if len(train_indices) >= cfg.min_train_size:
            splits.append(
                TrainTestSplit(
                    train_idx=train_indices.astype(np.int64),
                    test_idx=test_indices.astype(np.int64),
                )
            )

    if not splits:
        raise ValueError(
            "Could not generate any valid CPCV splits with current parameters and data length."
        )

    return splits


def apply_split(df: pd.DataFrame, split: TrainTestSplit) -> pd.DataFrame:
    """Returns the test slice of the dataframe given a TrainTestSplit."""
    return df.iloc[split.test_idx].copy()
