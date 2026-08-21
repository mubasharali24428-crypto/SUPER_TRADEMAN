"""Unit tests for Task 1: Frontier 4 Upgrades (Ultra-Slow Structural Baseline and Conditional Annealing)."""

from unittest import mock

import pytest

from trading.synthetic.regime_validator import (
    AdaptiveStressScaler,
    MacroVolatilityBaseline,
    RegimeValidator,
)


def test_new_normal_annealing():
    """Prove the baseline slowly shifts up when the market settles into a higher volatility 'new normal'."""
    macro_vol = MacroVolatilityBaseline(
        lambda_slow=0.01,
        lambda_ultra_slow=0.005,
        warmup_ticks=10,
        convergence_threshold=0.20,
        annealing_rate=0.05,
    )
    macro_vol.baseline_vol = 0.001

    # Simulate price ticks in a new higher volatility regime (0.004 return per tick)
    p = 50000.0
    for i in range(100):
        step = 200.0 if i % 2 == 0 else -200.0
        p += step
        macro_vol.update(p)
        macro_vol.conditional_anneal(current_state="NORMAL", is_defense_locked=False)

    # Baseline must have annealed upwards towards higher structural volatility
    assert macro_vol.baseline_vol > 0.001
    assert macro_vol.annealing_applied_count > 0


def test_socratic_lockout_preserved_during_annealing():
    """Prove that if the state flips to DEFENSIVE, annealing instantly halts and k_dynamic freezes."""
    macro_vol = MacroVolatilityBaseline(warmup_ticks=10)
    macro_vol.baseline_vol = 0.001
    macro_vol.ewma_vol = 0.003
    macro_vol.ewma_ultra_slow = 0.003
    macro_vol.convergence_ema = 0.05  # below convergence_threshold

    scaler = AdaptiveStressScaler(k_base=2.0, macro_vol_tracker=macro_vol)

    # When DEFENSIVE lockout is active
    scaler.set_defense_lockout(True)
    baseline_before = macro_vol.baseline_vol

    # Attempt to anneal
    annealed = macro_vol.conditional_anneal(current_state="DEFENSIVE", is_defense_locked=True)
    assert not annealed
    assert macro_vol.baseline_vol == baseline_before
    # k_dynamic must remain frozen at base value 2.0
    assert scaler.k_dynamic == 2.0


def test_ema_absorbs_minor_shocks():
    """Prove that a minor volatility spike during recovery does not reset the convergence counter to zero."""
    macro_vol = MacroVolatilityBaseline(
        lambda_slow=0.01,
        lambda_ultra_slow=0.005,
        warmup_ticks=10,
        convergence_threshold=0.20,
    )
    macro_vol.ewma_vol = 0.002
    macro_vol.ewma_ultra_slow = 0.002
    macro_vol.convergence_ema = 0.02  # Well converged

    # Minor secondary shock (divergence spike)
    p = 50000.0
    p += 300.0  # Minor shock
    macro_vol.update(p)

    # Decaying memory filters the shock; convergence_ema increases smoothly rather than blowing up or resetting
    assert macro_vol.convergence_ema < 0.15


# --- Category 4 spec suite additions ---
# Note: test_ema_absorbs_minor_shocks (spec #61) already exists above and is not duplicated here.


def test_socratic_lockout_defensive_freeze():
    macro = mock.Mock(spec=MacroVolatilityBaseline)
    scaler = AdaptiveStressScaler(k_base=2.0, macro_vol_tracker=macro)

    # Mock the state machine having entered DEFENSIVE.
    scaler.set_defense_lockout(True)
    scaler.update_baseline(current_price=105.0, current_state="DEFENSIVE")

    macro.update.assert_not_called()
    macro.conditional_anneal.assert_not_called()
    assert scaler.k_dynamic == pytest.approx(2.0)


def test_socratic_lockout_halted_freeze():
    macro = mock.Mock(spec=MacroVolatilityBaseline)
    scaler = AdaptiveStressScaler(k_base=2.0, macro_vol_tracker=macro)

    # Mock the state machine having entered HALTED.
    scaler.set_defense_lockout(True)
    scaler.update_baseline(current_price=105.0, current_state="HALTED")

    macro.update.assert_not_called()
    macro.conditional_anneal.assert_not_called()
    assert scaler.k_dynamic == pytest.approx(2.0)


def test_socratic_lockout_prevents_premature_deescalation():
    macro = MacroVolatilityBaseline(warmup_ticks=2)
    macro.baseline_vol = 0.001
    macro.ewma_vol = 0.001
    scaler = AdaptiveStressScaler(k_base=2.0, macro_vol_tracker=macro)

    # Genuinely elevated stress from real order-flow inputs.
    stress_before_lockout = scaler.calculate_stress(lwr_ext=3.0, ofi_norm=-0.5)

    scaler.set_defense_lockout(True)
    # Self-induced feedback: the defense posture's own quote withdrawal inflates session
    # vol, which would normally stretch k_dynamic and depress the score -- but the lockout
    # freezes k_dynamic at k_base regardless of what macro_vol_tracker now reports.
    macro.ewma_vol = 0.01  # 10x spike, self-induced by the defense's own footprint
    stress_during_lockout = scaler.calculate_stress(lwr_ext=3.0, ofi_norm=-0.5)

    assert stress_during_lockout == pytest.approx(stress_before_lockout)
    assert scaler.k_dynamic == pytest.approx(2.0)  # frozen at k_base, not stretched by the spike


def test_ultra_slow_baseline_independence():
    macro = MacroVolatilityBaseline(lambda_slow=0.05, lambda_ultra_slow=0.0001)
    macro.update(100.0)
    macro.update(110.0)  # bootstrap: both EWMAs jump to the same ~0.10 structural regime

    # Long stretch of calm session ticks afterward.
    price = 110.0
    for _ in range(200):
        price *= 1.0001
        macro.update(price)

    # The faster session EWMA (lambda=0.05) decays back toward the new calm level quickly;
    # the ultra-slow structural baseline (lambda=0.0001) barely moves off its anchor.
    assert macro.ewma_vol < macro.ewma_ultra_slow


def test_convergence_ema_decaying_memory():
    macro = MacroVolatilityBaseline(lambda_slow=0.5, lambda_ultra_slow=0.01)
    macro.update(100.0)
    macro.update(101.0)  # bootstrap tick: ewma_vol == ewma_ultra_slow -> divergence 0
    assert macro.convergence_ema == pytest.approx(0.0)

    macro.update(103.0)  # lambda_slow (0.5) adapts far faster than lambda_ultra_slow (0.01) -> they diverge
    raw_divergence = abs(macro.ewma_vol - macro.ewma_ultra_slow) / macro.ewma_ultra_slow
    # Decaying memory: convergence_ema is 95% prior memory (0.0) + 5% this tick's raw
    # divergence, not a straight snap to the raw value.
    assert macro.convergence_ema == pytest.approx(0.05 * raw_divergence)
    assert macro.convergence_ema < raw_divergence


def test_conditional_annealing_blocked_in_stress():
    macro = MacroVolatilityBaseline(convergence_threshold=0.20)
    macro.baseline_vol = 0.001
    macro.ewma_ultra_slow = 0.001
    macro.convergence_ema = 0.05  # well converged, would anneal if permitted

    assert not macro.conditional_anneal(current_state="DEFENSIVE", is_defense_locked=False)
    assert not macro.conditional_anneal(current_state="HALTED", is_defense_locked=False)
    assert not macro.conditional_anneal(current_state="NORMAL", is_defense_locked=True)


def test_conditional_annealing_normal_state():
    macro = MacroVolatilityBaseline(convergence_threshold=0.20, annealing_rate=0.05)
    macro.baseline_vol = 0.001
    macro.ewma_ultra_slow = 0.002
    macro.convergence_ema = 0.05  # < convergence_threshold

    assert macro.conditional_anneal(current_state="NORMAL", is_defense_locked=False)
    assert macro.baseline_vol == pytest.approx(0.95 * 0.001 + 0.05 * 0.002)


def test_conditional_annealing_recovering_state():
    macro = MacroVolatilityBaseline(convergence_threshold=0.20, annealing_rate=0.05)
    macro.baseline_vol = 0.001
    macro.ewma_ultra_slow = 0.002
    macro.convergence_ema = 0.05

    assert macro.conditional_anneal(current_state="RECOVERING", is_defense_locked=False)
    assert macro.baseline_vol == pytest.approx(0.95 * 0.001 + 0.05 * 0.002)


def test_new_normal_baseline_drift():
    macro = MacroVolatilityBaseline(convergence_threshold=0.20, annealing_rate=0.05)
    macro.baseline_vol = 0.001
    macro.ewma_ultra_slow = 0.005  # persistently higher structural vol -- the "new normal"
    macro.convergence_ema = 0.05  # stays well converged/stable throughout

    baselines = [macro.baseline_vol]
    for _ in range(20):
        macro.conditional_anneal(current_state="NORMAL", is_defense_locked=False)
        baselines.append(macro.baseline_vol)

    # Baseline drifts monotonically upward, converging toward the new structural level.
    assert all(b2 >= b1 for b1, b2 in zip(baselines, baselines[1:]))
    assert baselines[-1] > baselines[0]
    assert baselines[-1] < macro.ewma_ultra_slow  # asymptotic approach, not overshoot
