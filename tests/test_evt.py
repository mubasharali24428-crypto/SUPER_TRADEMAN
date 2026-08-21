import numpy as np
import pytest

from trading.risk.evt import EVTRiskEngine, EVTRiskResult


def test_evt_fallback_short_series():
    engine = EVTRiskEngine(min_samples=50)
    prices = [100.0, 101.0, 99.0]
    res = engine.estimate_tail_risk(prices)

    assert isinstance(res, EVTRiskResult)
    assert res.cvar_99 >= res.var_99
    assert res.var_99 >= res.var_95
    assert 0.20 <= res.recommended_risk_scale <= 1.25


def test_evt_heavy_tail_estimation():
    np.random.seed(42)
    # Generate Student's t heavy-tailed returns (df=3 has fat tails)
    heavy_returns = np.random.standard_t(df=3, size=250) * 0.02

    engine = EVTRiskEngine(threshold_quantile=0.85, min_samples=40)
    res = engine.estimate_tail_risk(heavy_returns, is_returns=True)

    assert isinstance(res, EVTRiskResult)
    assert res.var_95 > 0.0
    assert res.var_99 >= res.var_95
    assert res.cvar_99 >= res.var_99
    assert res.num_exceedances > 0
    assert 0.20 <= res.recommended_risk_scale <= 1.25
