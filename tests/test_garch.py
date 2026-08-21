import math
import numpy as np
import pytest

from trading.risk.garch import GARCHVolatilityModel, GARCHForecastResult


def test_garch_fallback_on_short_series():
    model = GARCHVolatilityModel(min_history_length=30)
    prices = [100.0, 101.0, 100.5, 102.0]  # Too short for GARCH fitting
    res = model.fit_forecast(prices)

    assert isinstance(res, GARCHForecastResult)
    assert res.volatility_scale_factor >= 0.25
    assert res.volatility_scale_factor <= 1.50
    assert res.conditional_volatility > 0.0


def test_garch_fit_forecast_on_synthetic_data():
    np.random.seed(42)
    # Generate 150 candles of log returns with alternating volatility
    returns = np.random.normal(0, 0.02, 150)
    returns[50:100] = np.random.normal(0, 0.06, 50)  # Volatility spike

    model = GARCHVolatilityModel(target_annual_vol=0.40, high_vol_threshold_ann=0.60)
    res = model.fit_forecast(returns, is_returns=True)

    assert isinstance(res, GARCHForecastResult)
    assert 0.0 < res.alpha < 1.0
    assert 0.0 < res.beta < 1.0
    assert res.persistence > 0.0
    assert res.conditional_volatility > 0.0
    assert res.annualized_volatility > 0.0
    assert 0.25 <= res.volatility_scale_factor <= 1.50


def test_garch_high_volatility_detection():
    np.random.seed(99)
    # Generate extreme volatility return series (~100% annual vol)
    high_vol_returns = np.random.normal(0, 0.08, 100)

    model = GARCHVolatilityModel(high_vol_threshold_ann=0.50)
    res = model.fit_forecast(high_vol_returns, is_returns=True)

    assert res.is_high_volatility is True
    assert res.volatility_scale_factor <= 1.0  # Should reduce position risk size
