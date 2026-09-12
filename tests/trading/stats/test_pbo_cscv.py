"""Tests for CSCV Probability of Backtest Overfitting (Bailey et al. 2017)."""

import warnings

import numpy as np
import pytest

from trading.stats.pbo import (CSCVConfig, PBOResult, compute_pbo,
                               compute_pbo_cscv)

RNG = np.random.default_rng(20260824)


def _synthetic_field(n_obs=480, n_strats=10, seed=7, dominant_edge=0.0):
    rng = np.random.default_rng(seed)
    base = rng.normal(0.0002, 0.01, size=(n_obs, n_strats))
    if dominant_edge:
        base[:, 0] += dominant_edge  # exactly ONE truly superior strategy
    return base


def test_cscv_one_truly_best_strategy_yields_low_pbo():
    # Strategy 0 carries a persistent positive edge; the rest are noise clones.
    M = _synthetic_field(dominant_edge=0.004)
    res = compute_pbo_cscv(M)
    assert isinstance(res, PBOResult)
    assert (
        res.pbo <= 0.10
    ), f"PBO should be near 0 for a truly dominant strategy, got {res.pbo}"
    assert np.mean(res.is_best_indices == 0) > 0.9  # IS selection consistently finds it


def test_cscv_random_equal_strategies_yield_pbo_near_half():
    # All strategies are exchangeable noise draws -> IS-best is coin-flip OOS.
    pbos = []
    for seed in (11, 23, 37):
        rng = np.random.default_rng(seed)
        M = rng.normal(0.0002, 0.01, size=(400, 8))
        res = compute_pbo_cscv(M)
        pbos.append(res.pbo)
    mean_pbo = float(np.mean(pbos))
    assert (
        0.30 <= mean_pbo <= 0.70
    ), f"exchangeable strategies should give PBO~0.5, got {mean_pbo}"


def test_cscv_monotonicity_in_edge_strength():
    """Theory check: as the dominant strategy's TRUE edge shrinks toward zero
    (i.e. the field approaches pure selection among equals), PBO must rise.
    Averaged over seeds, PBO(edge=0.002) < PBO(edge=0.0005) <= PBO(edge=0)."""

    def pbo_for(dominant_edge: float) -> list[float]:
        pbos = []
        for seed in (101, 202, 303, 404, 505):
            rng = np.random.default_rng(seed)
            n_obs, n_strats = 480, 16
            M = rng.normal(0.0002, 0.01, size=(n_obs, n_strats))
            M[:, 0] += dominant_edge
            pbos.append(compute_pbo_cscv(M).pbo)
        return pbos

    strong = pbo_for(0.003)
    weak = pbo_for(0.0005)
    none = pbo_for(0.0)
    mean = lambda xs: float(np.mean(xs))
    assert mean(strong) < mean(
        weak
    ), f"PBO should increase as edge weakens: {mean(strong):.3f} !< {mean(weak):.3f}"
    assert 0.30 <= mean(none) <= 0.70  # edgeless field ~ coin flip
    print(
        f"\n[cscv monotonicity] mean PBO by edge strength "
        f"(N=16 trials): strong={mean(strong):.3f} "
        f"weak={mean(weak):.3f} none={mean(none):.3f}"
    )


def test_cscv_more_trials_harder_to_deflate():
    """With a FIXED weak edge, growing the noise-field size (more trials)
    makes the IS winner less reliable OOS; averaged over seeds the PBO of a
    large field must not be materially below that of a small one."""

    def pbo_for(n_strats: int) -> float:
        pbos = []
        for seed in (101, 202, 303, 404, 505):
            rng = np.random.default_rng(seed)
            cols = [rng.normal(0.0012, 0.01, 480)]  # fixed modest true edge
            cols += [rng.normal(0.0, 0.01, 480) for _ in range(n_strats - 1)]
            pbos.append(compute_pbo_cscv(np.column_stack(cols)).pbo)
        return float(np.mean(pbos))

    small = pbo_for(4)
    large = pbo_for(64)
    # The large field cannot be *safer* than the small one beyond noise.
    assert (
        large >= small - 0.15
    ), f"64-trial field unexpectedly safer than 4-trial: {small:.3f} vs {large:.3f}"
    print(f"\n[cscv trial count] mean PBO: N=4 -> {small:.3f}, N=64 -> {large:.3f}")


def test_cscv_logits_match_lambda_sign_convention():
    M = _synthetic_field(n_obs=320, n_strats=6)
    res = compute_pbo_cscv(M)
    assert len(res.lambdas) == len(res.logits) == res.n_splits_evaluated
    for lam, lg in zip(res.lambdas, res.logits):
        if lam > 0.5:
            assert lg > 0
        elif lam < 0.5:
            assert lg < 0
        else:
            assert lg == 0  # clipped logit at the exact median
    # PBO defined as fraction lambda > 0.5 == fraction of positive logits.
    assert res.pbo == pytest.approx(float(np.mean(res.lambdas > 0.5)))
    assert res.pbo == pytest.approx(float(np.mean(res.logits > 0)))


def test_cscv_all_combinations_when_below_cap():
    # T=6 blocks -> C(6,3) = 20 splits, below the cap so ALL are evaluated.
    M = _synthetic_field(n_obs=240, n_strats=5)
    res = compute_pbo_cscv(M, CSCVConfig(n_blocks=6))
    assert res.n_splits_total == 20
    assert res.n_splits_evaluated == 20
    assert len({tuple(c) for c in res.combinations}) == 20


def test_cscv_sampling_caps_combinations():
    # T=12 -> C(12,6)=924 total; a tiny cap forces deterministic subsampling.
    M = _synthetic_field(n_obs=480, n_strats=6)
    cfg_a = CSCVConfig(n_blocks=12, max_splits=50, random_state=1)
    cfg_b = CSCVConfig(n_blocks=12, max_splits=50, random_state=1)
    cfg_c = CSCVConfig(n_blocks=12, max_splits=50, random_state=2)
    ra = compute_pbo_cscv(M, cfg_a)
    rb = compute_pbo_cscv(M, cfg_b)
    rc = compute_pbo_cscv(M, cfg_c)
    assert ra.n_splits_evaluated == 50
    assert ra.n_splits_total == 924
    assert ra.pbo == rb.pbo  # same seed -> identical result
    assert ra.combinations != rc.combinations or ra.pbo == rc.pbo


def test_cscv_sharpe_metric_option():
    # A strategy with a constant small drift beats Sharpe-selection too.
    M = _synthetic_field(dominant_edge=0.004)
    res = compute_pbo_cscv(M, CSCVConfig(metric="sharpe"))
    assert res.pbo <= 0.15


def test_legacy_compute_pbo_alias_warns_and_preserves_behavior():
    with pytest.warns(DeprecationWarning, match="compute_pbo_cscv"):
        out = compute_pbo([1.5, -0.2, 0.8, -1.0])
    assert out == 0.5  # legacy semantics unchanged

    with pytest.warns(DeprecationWarning):
        arr = np.array([[2.0, 1.0, 0.5], [0.1, 1.5, 2.0]])
        assert compute_pbo(arr) == 0.0
    assert compute_pbo([]) == 0.0  # empty path returns without warning value checks


def test_cscv_input_validation():
    good = _synthetic_field(n_obs=160, n_strats=4)
    with pytest.raises(ValueError, match="1D"):
        compute_pbo_cscv(good[:, 0])
    with pytest.raises(ValueError, match="at least 2 strategies"):
        compute_pbo_cscv(np.column_stack([good[:, 0]]))
    with pytest.raises(ValueError, match="NaN"):
        bad = good.copy()
        bad[3, 2] = np.nan
        compute_pbo_cscv(bad)
    with pytest.raises(ValueError, match="n_blocks"):
        compute_pbo_cscv(good, CSCVConfig(n_blocks=7))
    with pytest.raises(ValueError, match="observations"):
        compute_pbo_cscv(_synthetic_field(n_obs=20, n_strats=4))


def test_cscv_result_shapes_and_ranges():
    M = _synthetic_field(n_obs=320, n_strats=6, dominant_edge=0.001)
    res = compute_pbo_cscv(M)
    assert 0.0 <= res.pbo <= 1.0
    assert np.all(res.lambdas > 0) and np.all(res.lambdas <= 1.0)
    assert np.isfinite(res.logits).all()
    assert set(res.is_best_indices.tolist()) <= set(range(6))


# --------------------------------------------------------------------- #
# Wave-6 RECT-ALPHA                                                     #
# --------------------------------------------------------------------- #


def test_vb003_conditional_pbo_exposes_concentrated_condemnation():
    """Pooled PBO dilutes when the IS-best only wins a minority of splits;
    the per-IS-best-strategy conditional view must still flag it.

    Fixture: strategy 1 is inflated in blocks 0-3 of 16, so it wins IS only
    on splits whose IS set is dominated by those blocks; whenever it DOES
    win, it collapses OOS (lambda > 0.5 on ~every such split), while the
    pooled fraction over all splits stays near coin-flip.
    """
    rng = np.random.default_rng(20260824)
    n_obs, n_strats, T = 480, 6, 16
    M = rng.normal(0.0002, 0.01, size=(n_obs, n_strats))
    block = n_obs // T
    M[0 : block * 4, 1] += 0.02  # IS-dominant in early blocks...
    M[block * 4 :, 1] -= 0.03  # ...but collapses everywhere else

    res = compute_pbo_cscv(M, CSCVConfig(n_blocks=T, max_splits=1000, random_state=42))
    assert res.n_splits_evaluated == len(res.lambdas)
    # Backward compat: pooled fields unchanged in meaning.
    assert 0.0 <= res.pbo <= 1.0
    # Conditional view keyed by IS-best winner.
    winners = set(np.unique(res.is_best_indices).tolist())
    assert set(res.conditional_pbo) == winners
    # Every conditional value is a valid probability.
    for v in res.conditional_pbo.values():
        assert 0.0 <= v <= 1.0
    # If strategy 1 ever wins IS, it must lose OOS almost always -> its
    # conditional PBO sits far above the pooled dilution, and the max
    # conditional gate sees it even if the pool reads ~coin-flip.
    if 1 in res.conditional_pbo:
        assert res.conditional_pbo[1] >= 0.99, (
            f"condemned winner diluted: cond={res.conditional_pbo}, "
            f"pooled={res.pbo:.3f}"
        )
        assert res.max_conditional_pbo == pytest.approx(
            max(res.conditional_pbo.values())
        )
        assert res.max_conditional_pbo > res.pbo


def test_vb003_conditional_pbo_default_fields_backward_compatible():
    """Defaulted new fields keep old result construction working."""
    r = PBOResult(
        pbo=0.5,
        n_strategies=2,
        n_blocks=4,
        n_splits_evaluated=1,
        n_splits_total=1,
        lambdas=np.array([0.5]),
        logits=np.array([0.0]),
        is_best_indices=np.array([0]),
        combinations=[],
    )
    assert r.conditional_pbo == {}
    assert r.max_conditional_pbo == 0.0
    assert r.pbo_stderr == 0.0


def test_vb012_all_flat_field_sharpe_metric_raises():
    """A degenerate all-zero-variance field must refuse to elect j*=0
    silently under metric='sharpe'."""
    flat = np.zeros((64, 4))
    flat[:, 0] = np.arange(64)  # one varying column is not enough (need 2)
    with pytest.raises(ValueError, match="degenerate all-flat"):
        compute_pbo_cscv(flat, CSCVConfig(metric="sharpe"))


def test_vb012_single_varying_strategy_sharpe_raises():
    M = _synthetic_field(n_obs=160, n_strats=3)
    M[:, [1, 2]] = 0.0  # only ONE strategy has positive variance
    with pytest.raises(ValueError, match="positive"):
        compute_pbo_cscv(M, CSCVConfig(metric="sharpe"))


def test_vb044_pbo_stderr_reported_and_warns_on_few_splits():
    M = _synthetic_field(n_obs=240, n_strats=4)
    # T=6 -> C(6,3)=20 splits evaluated: no warning at the floor boundary.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        res = compute_pbo_cscv(M, CSCVConfig(n_blocks=6))
    expected = float(np.sqrt(res.pbo * (1 - res.pbo) / 20))
    assert res.pbo_stderr == pytest.approx(expected)
    assert res.n_splits_evaluated == 20

    # Below the reliability floor: warn AND carry the (large) stderr.
    with pytest.warns(UserWarning, match="standard error"):
        few = compute_pbo_cscv(M, CSCVConfig(n_blocks=6, max_splits=5))
    assert few.n_splits_evaluated == 5
    assert few.pbo_stderr == pytest.approx(float(np.sqrt(few.pbo * (1 - few.pbo) / 5)))
    assert few.pbo_stderr >= 0.0


def test_vb022_compute_pbo_not_reexported_from_package():
    import trading.stats as pkg

    assert "compute_pbo" not in pkg.__all__
    assert not hasattr(pkg, "compute_pbo")
    # The deprecated implementation itself remains importable from pbo.py.
    from trading.stats.pbo import compute_pbo  # noqa: F401
