"""Tests for EWMA Pairwise Correlation Matrix."""

import numpy as np
import pandas as pd
import pytest

from trading.risk.correlation import CorrelationMatrix


def test_correlation_matrix_updates_and_high_corr_pairs():
    np.random.seed(42)
    # Generate 50 days of returns for BTC and ETH (highly correlated) and SOL (independent)
    btc_returns = np.random.randn(50) * 0.02
    eth_returns = btc_returns * 0.9 + np.random.randn(50) * 0.005
    sol_returns = np.random.randn(50) * 0.03

    df = pd.DataFrame({"BTC": btc_returns, "ETH": eth_returns, "SOL": sol_returns})

    matrix_obj = CorrelationMatrix(min_periods=10)
    corr_df = matrix_obj.update_returns(df)

    assert not corr_df.empty
    btc_eth_corr = matrix_obj.get_correlation("BTC", "ETH")
    assert btc_eth_corr >= 0.80

    high_pairs = matrix_obj.get_high_correlation_pairs(threshold=0.80)
    assert len(high_pairs) >= 1
    assert ("BTC", "ETH", pytest.approx(btc_eth_corr, abs=0.01)) in high_pairs or ("ETH", "BTC", pytest.approx(btc_eth_corr, abs=0.01)) in high_pairs
