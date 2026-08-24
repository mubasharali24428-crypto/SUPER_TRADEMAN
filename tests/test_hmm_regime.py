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


# --------------------------------------------------------------------- #
# Sub-06 rigor: convergence checks, causal live posterior, honest warm-up #
# --------------------------------------------------------------------- #

def test_model_fit_reports_provenance_and_convergence():
    np.random.seed(7)
    returns = np.concatenate([
        np.random.normal(0.0001, 0.002, 60),
        np.random.normal(0.02, 0.005, 60),
    ])
    clf = HMMRegimeClassifier(min_history_length=30)
    res = clf.fit_predict(returns, is_returns=True)
    assert res.provenance == "model"
    assert res.converged is True
    assert abs(sum(res.state_probabilities) - 1.0) < 1e-6


def test_forward_posterior_matches_prefix_score_samples():
    """The hand-rolled forward recursion must agree with hmmlearn's own
    forward-backward evaluated on growing prefixes: at the LAST step of any
    prefix, the smoothed posterior equals the filtered one."""
    np.random.seed(11)
    returns = np.concatenate([
        np.random.normal(-0.02, 0.05, 80),
        np.random.normal(0.01, 0.004, 80),
    ])
    clf = HMMRegimeClassifier(min_history_length=30)
    X = clf._build_features(returns)
    model, converged = clf._fit_with_convergence_check(X)
    assert converged
    assert model is not None

    for t in (len(X) - 50, len(X) - 10, len(X)):
        ours = clf._forward_filtered_posterior(model, X[:t])[-1]
        theirs = model.predict_proba(X[:t])[-1]
        np.testing.assert_allclose(ours, theirs, atol=1e-8)


def test_features_drop_warmup_instead_of_seeding():
    clf = HMMRegimeClassifier(min_history_length=30)
    rng = np.random.default_rng(3)
    returns = rng.normal(0.0, 0.01, 120)
    X = clf._build_features(returns)
    window = 5
    assert len(X) == len(returns) - window + 1  # warm-up dropped, not seeded
    # First feature row's vol column == std of the FIRST FULL window.
    assert X[0, 1] == pytest.approx(float(np.std(returns[:window])))


class _StubGaussianHMM:
    """Deterministic stand-in: fails EM convergence on its first fit only."""
    fits = 0
    random_states_used = []

    def __init__(self, n_components=3, covariance_type="diag", n_iter=100,
                 random_state=42, init_params="stmc"):
        _StubGaussianHMM.random_states_used.append(random_state)
        self.n_components = n_components
        self.means_ = np.array([[0.02, 0.004], [-0.03, 0.06], [0.0001, 0.002]])
        self.covars_ = np.full((3, 2), 1e-6)
        self.transmat_ = np.full((3, 3), 1.0 / 3.0)
        self.startprob_ = np.full(3, 1.0 / 3.0)

        class _Monitor:
            converged = False
            iter = 100

        self.monitor_ = _Monitor()

    def fit(self, X):
        _StubGaussianHMM.fits += 1
        self.monitor_.converged = _StubGaussianHMM.fits > 1  # succeed on retry
        return self


def test_nonconvergence_retries_once_then_recovers(monkeypatch):
    monkeypatch.setattr("hmmlearn.hmm.GaussianHMM", _StubGaussianHMM)
    _StubGaussianHMM.fits = 0
    _StubGaussianHMM.random_states_used = []

    clf = HMMRegimeClassifier(min_history_length=30, random_state=42)
    rng = np.random.default_rng(5)
    res = clf.fit_predict(rng.normal(0.001, 0.01, 120), is_returns=True)

    # Exactly two attempts, second with a different random_state.
    assert len(_StubGaussianHMM.random_states_used) == 2
    assert _StubGaussianHMM.random_states_used[1] == \
        _StubGaussianHMM.random_states_used[0] + 1
    assert res.converged is True
    assert res.provenance == "model"


def test_persistent_nonconvergence_returns_last_known_fallback(monkeypatch):
    class _NeverConverges(_StubGaussianHMM):
        def fit(self, X):
            _StubGaussianHMM.fits += 1
            self.monitor_.converged = False  # never converges
            return self

    monkeypatch.setattr("hmmlearn.hmm.GaussianHMM", _NeverConverges)
    _StubGaussianHMM.fits = 0

    clf = HMMRegimeClassifier(min_history_length=30, random_state=42)
    rng = np.random.default_rng(9)

    # First call: no prior successful model -> heuristic fallback, flagged.
    res1 = clf.fit_predict(rng.normal(0.001, 0.01, 120), is_returns=True)
    assert res1.provenance == "fallback"
    assert res1.converged is False

    # Second call with the RECOVERABLE stub: converges immediately.
    monkeypatch.setattr("hmmlearn.hmm.GaussianHMM", _StubGaussianHMM)
    _StubGaussianHMM.fits = 10
    ok = clf.fit_predict(rng.normal(0.001, 0.01, 120), is_returns=True)
    assert ok.provenance == "model"

    # Third call: back to never-converging -> LAST-KNOWN regime, flagged.
    monkeypatch.setattr("hmmlearn.hmm.GaussianHMM", _NeverConverges)
    stale = clf.fit_predict(rng.normal(-0.05, 0.09, 120), is_returns=True)
    assert stale.provenance == "fallback"
    assert stale.converged is False
    assert stale.current_regime == ok.current_regime  # last-known kept


def test_short_history_fallback_is_flagged():
    clf = HMMRegimeClassifier(min_history_length=40)
    res = clf.fit_predict([100.0, 101.0, 100.5])
    assert res.provenance == "fallback"
    assert res.converged is False
