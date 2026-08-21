"""Tests for Data Quality Engine."""

import numpy as np
import pandas as pd
import pytest

from trading.data.quality import (
    DataValidationError,
    check_price_consistency,
    detect_duplicate_timestamps,
    validate_funding,
    validate_ohlcv,
)


def test_valid_ohlcv():
    dates = pd.date_range("2025-01-01", periods=10, freq="D")
    df = pd.DataFrame(
        {
            "open": [100.0] * 10,
            "high": [105.0] * 10,
            "low": [95.0] * 10,
            "close": [102.0] * 10,
            "volume": [1000.0] * 10,
        },
        index=dates,
    )
    validate_ohlcv(df)  # Should not raise


def test_invalid_ohlcv_high_less_than_low():
    dates = pd.date_range("2025-01-01", periods=2, freq="D")
    df = pd.DataFrame(
        {
            "open": [100.0, 100.0],
            "high": [90.0, 105.0],  # high 90 < low 95 on row 0
            "low": [95.0, 95.0],
            "close": [92.0, 102.0],
            "volume": [1000.0, 1000.0],
        },
        index=dates,
    )
    with pytest.raises(DataValidationError, match="High < Low"):
        validate_ohlcv(df)


def test_invalid_funding_negative_mark_price():
    dates = pd.date_range("2025-01-01", periods=2, freq="D")
    df = pd.DataFrame(
        {
            "funding_rate": [0.0001, 0.0002],
            "mark_price": [-100.0, 50000.0],
        },
        index=dates,
    )
    with pytest.raises(DataValidationError, match="non-positive mark price"):
        validate_funding(df)
