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
        # Wave-6 RECT-ALPHA (VB-032): spread the state means/variances to a
        # plausible geometry around typical input scale (return sd ~0.01, vol
        # feature ~0.01). The former covars=1e-6 + far-away means routed every
        # observation into a single state — a degenerate posterior that the new
        # sanity gate rightly refuses; these stubs exist to exercise the
        # convergence-retry machinery, not to certify degenerate fits.
        self.means_ = np.array([
            [0.020, 0.010],
            [-0.020, 0.012],
            [0.001, 0.008],
        ])
        self.covars_ = np.full((3, 2), 1e-4)
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


# --------------------------------------------------------------------- #
# Wave-6 RECT-ALPHA                                                     #
# --------------------------------------------------------------------- #

def test_vb013_align_states_refuses_non_canonical_component_count():
    clf = HMMRegimeClassifier(n_components=4)
    with pytest.raises(ValueError, match="n_components"):
        clf._align_states(np.zeros((4, 2)))


def test_vb013_high_vol_positive_return_state_must_not_steal_bear_slot():
    """Regression for the unit-mixing defect: raw '-return + vol' scoring let
    a high-vol MILDLY-POSITIVE-return state outrank the deeply-negative
    low-vol state for the bear slot once vol units dwarf return units."""
    clf = HMMRegimeClassifier(n_components=3)
    means = np.array([
        [0.001, 0.95],   # X: near-zero return, huge vol (chop candidate)
        [0.060, 0.50],   # Z: highest return -> bull
        [-0.040, 0.90],  # Y: deeply negative return -> true bear
    ])
    mapping = clf._align_states(means)
    assert mapping == {1: 0, 2: 1, 0: 2}
    # Bijection onto canonical slots.
    assert sorted(mapping.values()) == [0, 1, 2]


def test_vb013_bear_scoring_scale_invariant_across_feature_units():
    clf = HMMRegimeClassifier(n_components=3)
    rng = np.random.default_rng(31)
    means = np.column_stack([
        rng.normal(0, 0.02, 3),      # mean returns
        rng.uniform(0.005, 0.05, 3),  # rolling vols
    ])
    base = clf._align_states(means)
    inflated = means.copy()
    inflated[:, 1] *= 1000.0          # same states, vol quoted in other units
    assert clf._align_states(inflated) == base


class _AlwaysConverges(_StubGaussianHMM):
    def fit(self, X):
        self.monitor_.converged = True
        return self


class _DegenerateCollapse(_AlwaysConverges):
    """Fits 'successfully' but places every observation in hidden state 0:
    states 1/2 sit astronomically far from the data with tiny covariance, so
    their filtered mass underflows to ~0 at every step without ever raising."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.means_ = np.array([
            [0.001, 0.01],   # matches typical sample scale
            [50.0, 50.0],    # impossibly far in return dimension
            [-50.0, 50.0],
        ])


def test_vb032_degenerate_one_state_posterior_gates_to_fallback(monkeypatch):
    monkeypatch.setattr("hmmlearn.hmm.GaussianHMM", _DegenerateCollapse)
    clf = HMMRegimeClassifier(min_history_length=30, random_state=42)
    rng = np.random.default_rng(13)
    res = clf.fit_predict(rng.normal(0.001, 0.01, 120), is_returns=True)

    assert res.provenance == "fallback"
    assert res.converged is False
    # Degenerate fits must never enter the model cache.
    assert clf._cached_model is None
    assert clf._cached_feature_rows is None


def test_vb015_cache_avoids_refit_on_append_only_history(monkeypatch):
    monkeypatch.setattr("hmmlearn.hmm.GaussianHMM", _AlwaysConverges)
    fits = {"n": 0}

    class _Counting(_AlwaysConverges):
        def fit(self, X):
            fits["n"] += 1
            return super().fit(X)

    monkeypatch.setattr("hmmlearn.hmm.GaussianHMM", _Counting)

    rng = np.random.default_rng(23)
    rets = rng.normal(0.001, 0.01, 60)
    prices = list(np.exp(np.cumsum(rets)) * 100.0)

    clf = HMMRegimeClassifier(min_history_length=30)
    r1 = clf.fit_predict(prices)
    assert fits["n"] == 1
    assert r1.provenance == "model"

    # Append-only growth: prefix of returns unchanged -> cached fit reused.
    more = np.exp(np.cumsum(rng.normal(0.001, 0.01, 10))) * prices[-1]
    r2 = clf.fit_predict(prices + list(more))
    assert fits["n"] == 1                      # NO refit
    assert r2.provenance == "model"

    # Diverging history (mutated price) invalidates the prefix -> refit.
    mutated = list(prices)
    mutated[10] *= 2.0
    r3 = clf.fit_predict(mutated)
    assert fits["n"] == 2
    assert r3.provenance == "model"

    # Explicit invalidation forces a fresh fit even on identical data.
    clf.invalidate_model_cache()
    clf.fit_predict(mutated)
    assert fits["n"] == 3


def test_vb045_single_history_guard_symmetric_for_prices_vs_returns(monkeypatch):
    """Both input modes admitting N usable returns must pass/fail the history
    guard identically. Under the old double guard, 30 RETURNS were rejected
    while 31 PRICES (= 30 returns) were admitted."""
    monkeypatch.setattr("hmmlearn.hmm.GaussianHMM", _AlwaysConverges)
    feature_rows = []

    class _Spy(_AlwaysConverges):
        def fit(self, X):
            feature_rows.append(len(X))
            return super().fit(X)

    monkeypatch.setattr("hmmlearn.hmm.GaussianHMM", _Spy)

    rng = np.random.default_rng(19)
    rets = rng.normal(0.001, 0.01, 30)
    # Prepend a base so len(prices) == 31: np.diff(np.log(prices)) reproduces
    # exactly the same 30 returns the direct-returns path feeds in.
    prices = 100.0 * np.exp(np.concatenate([[0.0], np.cumsum(rets)]))

    clf_r = HMMRegimeClassifier(min_history_length=30)
    clf_p = HMMRegimeClassifier(min_history_length=30)
    res_r = clf_r.fit_predict(rets, is_returns=True)
    res_p = clf_p.fit_predict(prices)

    # Both reached the model (old code: returns-input fell back pre-fit).
    assert feature_rows == [26, 26]
    assert res_r.provenance == "model"
    assert res_p.provenance == "model"


def test_vb081_forward_posterior_finite_and_exact_at_long_horizon():
    """Regression guard for the end-normalized forward recursion: posteriors
    stay finite, sum to 1, and agree EXACTLY with hmmlearn's own forward pass
    even at heartbeat-buffer length (T~600), proving the shared-offset
    subtraction keeps ratios safe at long horizons."""
    np.random.seed(29)
    returns = np.concatenate([
        np.random.normal(-0.02, 0.05, 300),
        np.random.normal(0.01, 0.004, 300),
    ])
    clf = HMMRegimeClassifier(min_history_length=30)
    X = clf._build_features(returns)
    model, converged = clf._fit_with_convergence_check(X)
    assert converged and model is not None

    post = clf._forward_filtered_posterior(model, X)
    assert np.isfinite(post).all()
    latest = post[-1]
    assert abs(float(latest.sum()) - 1.0) < 1e-9
    # Row-wise equivalence with hmmlearn's forward-backward final step.
    theirs = model.predict_proba(X)[-1]
    np.testing.assert_allclose(latest, theirs, atol=1e-8)
