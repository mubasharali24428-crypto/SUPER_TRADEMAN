import numpy as np
import pytest

from trading.risk.copula import CopulaDependencyEngine, CopulaDependencyResult


def test_copula_fallback_short_series():
    engine = CopulaDependencyEngine(min_samples=50)
    res = engine.analyze_pair_dependency("BTC/USDT", "ETH/USDT", [100.0, 101.0], [2000.0, 2010.0])

    assert isinstance(res, CopulaDependencyResult)
    assert res.asset_pair == ("BTC/USDT", "ETH/USDT")
    assert res.recommended_cluster_cap in (0.04, 0.08)


def test_copula_co_dependent_crash_detection():
    np.random.seed(42)
    # Generate strongly co-dependent joint crash series
    common_shock = np.random.normal(-0.04, 0.03, 100)
    ra = common_shock + np.random.normal(0, 0.005, 100)
    rb = common_shock + np.random.normal(0, 0.005, 100)

    engine = CopulaDependencyEngine(tail_dependence_threshold=0.50, min_samples=40)
    res = engine.analyze_pair_dependency("BTC/USDT", "ETH/USDT", ra, rb)

    assert isinstance(res, CopulaDependencyResult)
    assert res.linear_correlation > 0.80
    assert res.lower_tail_dependence > 0.50
    assert res.is_co_dependent_crash is True
    assert res.recommended_cluster_cap == 0.04  # Tightened cap for co-dependent assets
