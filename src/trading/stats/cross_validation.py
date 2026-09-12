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


def generate_cpcv_splits(df: pd.DataFrame, cfg: CPCVConfig) -> list[TrainTestSplit]:
    """Generates causal purged walk-forward cross-validation splits.

    For each test block k in range(1, n_folds):
      - Test set is block k.
      - Training set consists of data points strictly before test_start minus
        the total purge window (purge_days + max_holding_days + label_horizon_days).

    This guarantees strict causality: no future information leaks into the training set.

    Additionally, ``embargo_days`` is applied on the TEST-side boundary: the
    first ``embargo_days`` samples of each test block are dropped, so that
    labels/positions formed near the end of the training window (which need
    future data beyond the cutoff) cannot influence test evaluation either.
    """
    if len(df) < cfg.min_train_size + cfg.min_test_size:
        raise ValueError(
            f"Dataframe length ({len(df)}) is less than minimum required "
            f"({cfg.min_train_size} train + {cfg.min_test_size} test)"
        )

    n_samples = len(df)
    n_folds = max(2, cfg.n_folds)

    # Determine timestamps or index positions.
    #
    # Wave-6 VB-024: no fallback calendar. The previous behavior invented a
    # synthetic DAILY date_range for frames without real timestamps, so a
    # 5-minute-bar frame was silently treated as one bar = one day and the
    # day-unit purge/embargo windows removed only a handful of BARS instead
    # of days of label horizon — voiding the leakage guarantee while the
    # disjointness check still "passed" trivially. Callers must supply real
    # timestamps (DatetimeIndex, or 'timestamp'/'time' column) whenever any
    # day-based window is configured; integer-only data is accepted only when
    # every day-based window is zero, in which case splits are position-based.
    has_real_timestamps = (
        isinstance(df.index, pd.DatetimeIndex)
        or "timestamp" in df.columns
        or "time" in df.columns
    )
    if has_real_timestamps:
        if isinstance(df.index, pd.DatetimeIndex):
            timestamps = df.index
        elif "timestamp" in df.columns:
            timestamps = pd.to_datetime(df["timestamp"])
        else:
            timestamps = pd.to_datetime(df["time"])
    else:
        day_windows = (
            cfg.purge_days,
            cfg.max_holding_days,
            cfg.label_horizon_days,
            cfg.signal_lookback_days,
            cfg.feature_lookback_days,
            cfg.embargo_days,
        )
        window_names = (
            "purge_days",
            "max_holding_days",
            "label_horizon_days",
            "signal_lookback_days",
            "feature_lookback_days",
            "embargo_days",
        )
        configured = {
            name: val for name, val in zip(window_names, day_windows) if val > 0
        }
        if configured:
            raise ValueError(
                "generate_cpcv_splits: dataframe has no real timestamps "
                "(needs a DatetimeIndex or a 'timestamp'/'time' column) but "
                f"day-based windows are configured {configured}. A fabricated "
                "daily calendar would misalign purge/embargo with actual bar "
                "frequency — supply real timestamps or set all *_days to 0."
            )
        timestamps = None

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
        test_end_idx = n_samples if fold == n_folds - 1 else (fold + 1) * block_size

        test_indices = np.arange(test_start_idx, test_end_idx)

        # Embargo on the TEST side: drop the first embargo_days samples of the
        # test block. Positions opened at the end of the training window may
        # still be resolving during those samples; excluding them keeps the
        # test evaluation clean of train-adjacent label spillover.
        if cfg.embargo_days > 0 and len(test_indices) > cfg.min_test_size:
            test_indices = test_indices[cfg.embargo_days :]

        if len(test_indices) < cfg.min_test_size:
            continue

        # Causal purge: train must end before (test_start_time - purge_delta).
        # Wave-6 VB-024: integer-indexed frames (only legal when every
        # day-based window is 0) skip the time arithmetic entirely — with a
        # zero purge window every position strictly before the test block
        # qualifies as training data.
        if timestamps is not None:
            test_start_time = timestamps[test_start_idx]
            cutoff_time = test_start_time - purge_delta
            train_mask = np.array(
                [t < cutoff_time for t in timestamps[:test_start_idx]]
            )
            train_indices = np.arange(test_start_idx)[train_mask]
        else:
            train_indices = np.arange(test_start_idx)

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
