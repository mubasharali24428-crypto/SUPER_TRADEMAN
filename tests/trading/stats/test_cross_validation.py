"""Tests for CPCV cross-validation split generator."""

import numpy as np
import pandas as pd
import pytest

from trading.stats.cross_validation import (
    CPCVConfig,
    TrainTestSplit,
    apply_split,
    generate_cpcv_splits,
)


def test_generate_cpcv_splits_basic():
    # 100 days of synthetic daily candles
    dates = pd.date_range("2025-01-01", periods=100, freq="D")
    df = pd.DataFrame({"close": np.random.randn(100)}, index=dates)

    cfg = CPCVConfig(
        n_folds=5,
        purge_days=2,
        embargo_days=1,
        max_holding_days=3,
        min_train_size=10,
        min_test_size=5,
    )

    splits = generate_cpcv_splits(df, cfg)
    assert len(splits) > 0

    for split in splits:
        assert isinstance(split, TrainTestSplit)
        train_idx = split.train_idx
        test_idx = split.test_idx

        # Ensure no overlap
        assert len(set(train_idx).intersection(set(test_idx))) == 0

        # Causal property: all train indices must be strictly less than min test index
        assert np.all(train_idx < np.min(test_idx))

        # Check total purge distance
        total_purge = cfg.purge_days + cfg.max_holding_days + cfg.label_horizon_days + cfg.signal_lookback_days + cfg.feature_lookback_days
        min_test_time = dates[np.min(test_idx)]
        max_train_time = dates[np.max(train_idx)]
        assert (min_test_time - max_train_time) > pd.Timedelta(days=total_purge)


def test_apply_split():
    dates = pd.date_range("2025-01-01", periods=50, freq="D")
    df = pd.DataFrame({"val": np.arange(50)}, index=dates)

    split = TrainTestSplit(
        train_idx=np.arange(0, 30),
        test_idx=np.arange(30, 50),
    )

    test_df = apply_split(df, split)
    assert len(test_df) == 20
    assert test_df["val"].iloc[0] == 30
    assert test_df["val"].iloc[-1] == 49


def test_generate_cpcv_splits_short_df_raises():
    df = pd.DataFrame({"val": range(10)})
    cfg = CPCVConfig(min_train_size=20, min_test_size=10)
    with pytest.raises(ValueError, match="Dataframe length"):
        generate_cpcv_splits(df, cfg)
