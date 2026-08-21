"""Data quality and integrity validation engine for historical trading datasets."""

from dataclasses import dataclass
from typing import List, Tuple

import pandas as pd

__all__ = [
    "DataValidationError",
    "validate_ohlcv",
    "validate_funding",
    "detect_duplicate_timestamps",
    "check_price_consistency",
]


class DataValidationError(ValueError):
    """Raised when data quality or consistency checks fail."""
    pass


def detect_duplicate_timestamps(df: pd.DataFrame) -> List[pd.Timestamp]:
    """Detects duplicate timestamps in dataframe index or timestamp column."""
    if isinstance(df.index, pd.DatetimeIndex):
        dups = df.index[df.index.duplicated()].unique().tolist()
        return dups
    if "timestamp" in df.columns:
        dups = df["timestamp"][df["timestamp"].duplicated()].unique().tolist()
        return dups
    return []


def check_price_consistency(df: pd.DataFrame) -> List[str]:
    """Checks OHLC price bounds and relationships.

    Invariants:
      - Low <= Open <= High
      - Low <= Close <= High
      - High >= Low
      - Volume >= 0
    """
    errors: List[str] = []

    req_cols = {"open", "high", "low", "close"}
    lower_cols = {col.lower() for col in df.columns}
    if not req_cols.issubset(lower_cols):
        errors.append(f"Missing required OHLC columns. Found: {list(df.columns)}")
        return errors

    # Standardize column casing
    df_clean = df.rename(columns=str.lower)

    high_less_low = (df_clean["high"] < df_clean["low"]).sum()
    if high_less_low > 0:
        errors.append(f"Found {high_less_low} rows where High < Low")

    open_out_bounds = ((df_clean["open"] > df_clean["high"]) | (df_clean["open"] < df_clean["low"])).sum()
    if open_out_bounds > 0:
        errors.append(f"Found {open_out_bounds} rows where Open is outside [Low, High]")

    close_out_bounds = ((df_clean["close"] > df_clean["high"]) | (df_clean["close"] < df_clean["low"])).sum()
    if close_out_bounds > 0:
        errors.append(f"Found {close_out_bounds} rows where Close is outside [Low, High]")

    if "volume" in df_clean.columns:
        neg_vol = (df_clean["volume"] < 0).sum()
        if neg_vol > 0:
            errors.append(f"Found {neg_vol} rows with negative volume")

    return errors


def validate_ohlcv(df: pd.DataFrame) -> None:
    """Validates an OHLCV dataframe, raising DataValidationError if invalid."""
    if df.empty:
        raise DataValidationError("OHLCV DataFrame is empty")

    dups = detect_duplicate_timestamps(df)
    if dups:
        raise DataValidationError(f"Found duplicate timestamps: {dups[:5]}")

    errors = check_price_consistency(df)
    if errors:
        raise DataValidationError(f"OHLCV Price consistency violations: {'; '.join(errors)}")


def validate_funding(df: pd.DataFrame) -> None:
    """Validates a funding rates dataframe, raising DataValidationError if invalid."""
    if df.empty:
        raise DataValidationError("Funding DataFrame is empty")

    req_cols = {"funding_rate", "mark_price"}
    lower_cols = {col.lower() for col in df.columns}
    if not req_cols.issubset(lower_cols):
        raise DataValidationError(f"Missing required funding columns. Found: {list(df.columns)}")

    dups = detect_duplicate_timestamps(df)
    if dups:
        raise DataValidationError(f"Found duplicate funding timestamps: {dups[:5]}")

    df_clean = df.rename(columns=str.lower)
    neg_mark = (df_clean["mark_price"] <= 0).sum()
    if neg_mark > 0:
        raise DataValidationError(f"Found {neg_mark} rows with non-positive mark price")
