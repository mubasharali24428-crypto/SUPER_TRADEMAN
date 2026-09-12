"""Tests for CPCV cross-validation split generator."""

import numpy as np
import pandas as pd
import pytest

from trading.stats.cross_validation import (CPCVConfig, TrainTestSplit,
                                            apply_split, generate_cpcv_splits)


def _daily_df(n: int) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame({"close": np.arange(n, dtype=float)}, index=dates)


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
        total_purge = (
            cfg.purge_days
            + cfg.max_holding_days
            + cfg.label_horizon_days
            + cfg.signal_lookback_days
            + cfg.feature_lookback_days
        )
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


# --------------------------------------------------------------------- #
# Sub-06 rigor: embargo applied on the TEST-side boundary                #
# --------------------------------------------------------------------- #


def test_embargo_applied_to_test_side_boundary():
    # Same data/config differing ONLY in embargo_days: the embargoed config's
    # test block must start LATER (embargo samples trimmed from the left of
    # each test block), while the training side is untouched by embargo.
    df = _daily_df(120)
    base = dict(
        n_folds=4,
        purge_days=2,
        max_holding_days=3,
        min_train_size=10,
        min_test_size=5,
        signal_lookback_days=0,
    )
    no_emb = generate_cpcv_splits(df, CPCVConfig(embargo_days=0, **base))
    emb = generate_cpcv_splits(df, CPCVConfig(embargo_days=4, **base))

    assert len(no_emb) == len(emb) > 0
    for s_plain, s_emb in zip(no_emb, emb):
        # Test-side effect: embargoed test starts strictly later.
        assert np.min(s_emb.test_idx) > np.min(s_plain.test_idx)
        assert s_emb.test_idx[0] == s_plain.test_idx[0] + 4
        # Train side unchanged by embargo.
        np.testing.assert_array_equal(s_emb.train_idx, s_plain.train_idx)
        # And the gap from train end to test start grew by >= embargo_days.
        gap_plain = int(np.min(s_plain.test_idx) - np.max(s_plain.train_idx))
        gap_emb = int(np.min(s_emb.test_idx) - np.max(s_emb.train_idx))
        assert gap_emb >= gap_plain + 4


def test_purge_gap_at_least_signal_lookback_days():
    # Contract: for every split, the temporal purge gap between the last
    # training sample and the first TEST sample must be >= signal_lookback_days
    # (plus the rest of the purge window).
    df = _daily_df(150)
    cfg = CPCVConfig(
        n_folds=5,
        purge_days=3,
        embargo_days=2,
        max_holding_days=5,
        signal_lookback_days=10,
        feature_lookback_days=0,
        label_horizon_days=0,
        min_train_size=10,
        min_test_size=5,
    )
    splits = generate_cpcv_splits(df, cfg)
    assert splits
    dates = df.index
    for split in splits:
        last_train_time = dates[np.max(split.train_idx)]
        first_test_time = dates[np.min(split.test_idx)]
        gap_days = (first_test_time - last_train_time) / pd.Timedelta(days=1)
        assert gap_days >= cfg.signal_lookback_days, (
            f"purge gap {gap_days}d violated signal_lookback_days "
            f"{cfg.signal_lookback_days}d"
        )


def test_embargo_never_shrinks_test_below_min_size():
    # A huge embargo must drop whole folds rather than emit undersized tests.
    df = _daily_df(120)
    cfg = CPCVConfig(
        n_folds=4,
        purge_days=1,
        embargo_days=500,
        max_holding_days=1,
        min_train_size=10,
        min_test_size=5,
    )
    with pytest.raises(ValueError, match="Could not generate any valid"):
        generate_cpcv_splits(df, cfg)


# --------------------------------------------------------------------- #
# Wave-6 RECT-ALPHA (VB-024): no fabricated calendars                   #
# --------------------------------------------------------------------- #


def test_vb024_integer_index_with_day_windows_raises():
    """Intraday/integer-indexed data must NOT silently inherit a fabricated
    daily calendar that turns day-based purges into a few bars."""
    df = pd.DataFrame({"close": np.arange(120, dtype=float)})  # RangeIndex
    cfg = CPCVConfig(n_folds=4, purge_days=1, max_holding_days=1)
    with pytest.raises(ValueError, match="no real timestamps"):
        generate_cpcv_splits(df, cfg)


def test_vb024_integer_index_all_zero_windows_still_yields_splits():
    """Position-based splitting remains available when every day-based window
    is zero — the only regime where no calendar is needed."""
    df = pd.DataFrame({"close": np.arange(120, dtype=float)})
    cfg = CPCVConfig(
        n_folds=4,
        purge_days=0,
        max_holding_days=0,
        label_horizon_days=0,
        signal_lookback_days=0,
        feature_lookback_days=0,
        embargo_days=0,
    )
    splits = generate_cpcv_splits(df, cfg)
    assert splits
    for split in splits:
        assert np.all(split.train_idx < np.min(split.test_idx))
        assert set(split.train_idx).isdisjoint(set(split.test_idx))


def test_vb024_timestamp_column_accepted_without_datetime_index():
    df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2025-01-01", periods=120, freq="D"),
            "close": np.arange(120, dtype=float),
        }
    )
    cfg = CPCVConfig(n_folds=4, purge_days=2, max_holding_days=1)
    splits = generate_cpcv_splits(df, cfg)
    assert splits
