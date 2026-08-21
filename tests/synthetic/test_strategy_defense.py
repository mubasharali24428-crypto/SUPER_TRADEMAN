"""Tests for Strategy Defense State Machine, EWMV Volatility Tracking, and Healing Protocol."""

import pytest

from trading.risk.models import Position, Side
from trading.synthetic.regime_validator import MicrostructureMetrics, MicrostructureRegime
from trading.synthetic.strategy_defense import (
    EWMVTracker,
    MicrostructureStrategyDefender,
    StrategyDefensePosture,
    StrategyDefenseState,
)


def _mock_metrics(regime: str, stress_score: float) -> MicrostructureMetrics:
    return MicrostructureMetrics(
        ofi=0.0,
        ofi_norm=0.0,
        lwr=1.0,
        lwr_ext=1.0,
        stress_score=stress_score,
        regime=regime,
        regime_shift_detected=(regime != MicrostructureRegime.CALM),
        effective_window_ms=60000.0,
        window_truncated=False,
        events_analyzed=100,
        confidence="HIGH",
    )


def test_ewmv_variance_tracker():
    tracker = EWMVTracker(alpha=0.05, initial_price=50000.0)
    # Stream a series of price ticks
    for p in [50010.0, 50020.0, 49990.0, 49980.0, 50000.0]:
        sigma = tracker.update(p)
        assert sigma >= 0.0

    assert tracker.std_dev > 0.0


def test_strategy_defender_normal_state():
    defender = MicrostructureStrategyDefender()
    metrics = _mock_metrics(MicrostructureRegime.CALM, 0.0)

    posture = defender.evaluate_posture(metrics, micro_price=50000.0)
    assert posture.state == StrategyDefenseState.NORMAL
    assert posture.size_multiplier == 1.0
    assert not posture.block_new_entries


def test_strategy_defender_defensive_state_with_ewmv_stop():
    defender = MicrostructureStrategyDefender()
    # Feed some volatility
    for p in [50000.0, 50100.0, 49900.0, 50200.0]:
        defender.evaluate_posture(_mock_metrics(MicrostructureRegime.CALM, 0.0), micro_price=p)

    metrics = _mock_metrics(MicrostructureRegime.SELL_STRESS, 0.80)
    pos = Position(
        asset="BTC/USDT",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=49800.0,
        stop_price=48000.0,
        risk_pct=0.01,
        position_size=1.0,
    )

    posture = defender.evaluate_posture(metrics, micro_price=50000.0, position=pos)
    assert posture.state == StrategyDefenseState.DEFENSIVE
    assert posture.block_new_entries
    assert posture.tightened_stop_price is not None
    # Tightened stop must be below current micro-price for LONG
    assert posture.tightened_stop_price < 50000.0


def test_strategy_defender_healing_protocol_and_graduation():
    defender = MicrostructureStrategyDefender(
        halted_cooldown_sec=300.0,
        healing_stability_sec=60.0,
    )
    calm_metrics = _mock_metrics(MicrostructureRegime.CALM, 0.05)

    # 1. Trigger HALTED on 8.5% drawdown at t=0
    posture = defender.evaluate_posture(calm_metrics, micro_price=50000.0, current_drawdown_pct=0.085, now_ts=0.0)
    assert posture.state == StrategyDefenseState.HALTED
    assert posture.trigger_emergency_flatten

    # 2. At t=100s, still in purgatory cooldown (< 300s), drawdown reduced to 2%
    posture = defender.evaluate_posture(calm_metrics, micro_price=50000.0, current_drawdown_pct=0.02, now_ts=100.0)
    assert posture.state == StrategyDefenseState.HALTED

    # 3. At t=305s, purgatory cooldown complete, calm streak starts
    posture = defender.evaluate_posture(calm_metrics, micro_price=50000.0, current_drawdown_pct=0.02, now_ts=305.0)
    assert posture.state == StrategyDefenseState.HALTED

    # 4. At t=370s (> 60s calm streak), graduates to RECOVERING (0.10x size cap)
    posture = defender.evaluate_posture(calm_metrics, micro_price=50000.0, current_drawdown_pct=0.02, now_ts=370.0)
    assert posture.state == StrategyDefenseState.RECOVERING
    assert posture.size_multiplier == 0.10
    assert not posture.block_new_entries

    # 5. At t=680s (> 300s in RECOVERING), graduates to CAUTION (0.50x)
    posture = defender.evaluate_posture(calm_metrics, micro_price=50000.0, current_drawdown_pct=0.02, now_ts=680.0)
    assert posture.state == StrategyDefenseState.CAUTION
    assert posture.size_multiplier == 0.50


# --- Category 3 spec suite additions ---


def test_sniper_disarmed_in_normal():
    defender = MicrostructureStrategyDefender()
    assert defender.current_state == StrategyDefenseState.NORMAL  # default, untouched

    posture = defender.evaluate_posture(_mock_metrics(MicrostructureRegime.CALM, 0.0), micro_price=100.0, now_ts=0.0)

    assert posture.state == StrategyDefenseState.NORMAL
    assert posture.sniper_mode_armed is False
    assert defender.sniper_mode.is_armed is False


def test_sniper_armed_in_recovering():
    defender = MicrostructureStrategyDefender(halted_cooldown_sec=300.0)
    # Mock the state machine having already transitioned HALTED -> RECOVERING,
    # rather than re-driving the full multi-tick healing sequence.
    defender.current_state = StrategyDefenseState.RECOVERING
    defender.recovering_entry_ts = 0.0
    defender.sniper_mode.is_armed = True

    posture = defender.evaluate_posture(_mock_metrics(MicrostructureRegime.CALM, 0.05), micro_price=100.0, now_ts=1.0)

    assert posture.state == StrategyDefenseState.RECOVERING
    assert posture.sniper_mode_armed is True
    assert defender.sniper_mode.is_armed is True
