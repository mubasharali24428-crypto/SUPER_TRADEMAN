import numpy as np
import pytest

from trading.risk.hmm_regime import HMMRegimeClassifier, HMMRegimeResult


def test_hmm_fallback_short_data():
    classifier = HMMRegimeClassifier(min_history_length=40)
    prices = [100.0, 101.0, 100.5]
    res = classifier.fit_predict(prices)

    assert isinstance(res, HMMRegimeResult)
    assert res.current_regime in HMMRegimeClassifier.REGIME_NAMES
    assert len(res.state_probabilities) == 3
    assert pytest.approx(sum(res.state_probabilities), abs=1e-3) == 1.0


def test_hmm_fit_predict_bullish_trend():
    np.random.seed(42)
    # Regime 1: Sideways (40 samples)
    part1 = np.random.normal(0.0001, 0.002, 40)
    # Regime 2: Strong Bullish momentum (60 samples)
    part2 = np.random.normal(0.025, 0.005, 60)
    returns = np.concatenate([part1, part2])

    classifier = HMMRegimeClassifier(min_history_length=30)
    res = classifier.fit_predict(returns, is_returns=True)

    assert isinstance(res, HMMRegimeResult)
    assert res.current_regime == "trending_bull"
    assert res.regime_id == 0
    assert res.is_trending is True
    assert pytest.approx(sum(res.state_probabilities), abs=1e-3) == 1.0


def test_hmm_fit_predict_volatile_bear():
    np.random.seed(99)
    # Regime 1: Sideways/Low Vol (50 samples)
    part1 = np.random.normal(0.0005, 0.005, 50)
    # Regime 2: Severe volatile bear crash (50 samples)
    part2 = np.random.normal(-0.03, 0.08, 50)
    returns = np.concatenate([part1, part2])

    classifier = HMMRegimeClassifier(min_history_length=30)
    res = classifier.fit_predict(returns, is_returns=True)

    assert isinstance(res, HMMRegimeResult)
    assert res.current_regime == "volatile_bear"
    assert res.regime_id == 1
    assert res.is_high_volatility is True


def test_hmm_state_probabilities_and_transition_matrix():
    np.random.seed(123)
    returns = np.random.normal(0.001, 0.015, 80)

    classifier = HMMRegimeClassifier(min_history_length=30)
    res = classifier.fit_predict(returns, is_returns=True)

    assert len(res.transition_matrix) == 3
    assert len(res.transition_matrix[0]) == 3
    assert 0.0 <= res.confidence <= 1.0
