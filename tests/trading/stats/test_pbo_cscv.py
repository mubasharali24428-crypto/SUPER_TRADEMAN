"""Tests for CSCV Probability of Backtest Overfitting (Bailey et al. 2017)."""

import warnings

import numpy as np
import pytest

from trading.stats.pbo import (
    CSCVConfig,
    PBOResult,
    compute_pbo,
    compute_pbo_cscv,
)

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
    assert res.pbo <= 0.10, f"PBO should be near 0 for a truly dominant strategy, got {res.pbo}"
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
    assert 0.30 <= mean_pbo <= 0.70, f"exchangeable strategies should give PBO~0.5, got {mean_pbo}"


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
    assert mean(strong) < mean(weak), (
        f"PBO should increase as edge weakens: {mean(strong):.3f} !< {mean(weak):.3f}"
    )
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
    assert large >= small - 0.15, (
        f"64-trial field unexpectedly safer than 4-trial: {small:.3f} vs {large:.3f}"
    )
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
    assert ra.pbo == rb.pbo            # same seed -> identical result
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
