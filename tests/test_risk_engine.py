from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from trading.risk.engine import RiskEngine
from trading.risk.models import (
    AccountState,
    ApprovedExit,
    ApprovedOrder,
    ExitSignal,
    Position,
    RiskConfig,
    Side,
    Signal,
)

NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_signal(**overrides) -> Signal:
    base = Signal(
        asset="BTC/USDT",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=100.0,
        confidence=0.7,
        timestamp=NOW,
        rationale="test signal",
        suggested_stop=95.0,
        suggested_target=112.0,  # reward 12 / risk 5 = 2.4 R:R
    )
    return replace(base, **overrides)


def make_account(**overrides) -> AccountState:
    base = AccountState(equity=100_000.0, peak_equity=100_000.0)
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_approves_valid_signal_and_sizes_correctly():
    decision = RiskEngine().evaluate(make_signal(), make_account())
    assert decision.approved
    # position_size = (equity * risk_pct) / abs(entry - stop) = (100000 * 0.01) / 5
    assert decision.approved_order.position_size == pytest.approx(200.0)
    assert decision.approved_order.risk_pct == pytest.approx(0.01)


def test_rejects_missing_stop_loss():
    decision = RiskEngine().evaluate(make_signal(suggested_stop=None), make_account())
    assert not decision.approved
    assert "stop-loss" in decision.reason


def test_rejects_insufficient_reward_risk():
    # stop=95 -> risk 5, target=105 -> reward 5, R:R = 1.0 < 2.0
    decision = RiskEngine().evaluate(make_signal(suggested_target=105.0), make_account())
    assert not decision.approved
    assert "reward:risk" in decision.reason


def test_rejects_missing_target():
    decision = RiskEngine().evaluate(make_signal(suggested_target=None), make_account())
    assert not decision.approved
    assert "target" in decision.reason


def test_rejects_when_portfolio_heat_cap_exceeded():
    open_positions = [
        Position("ETH/USDT", "crypto", Side.LONG, 100, 95, risk_pct=0.055),
    ]
    decision = RiskEngine().evaluate(make_signal(), make_account(open_positions=open_positions))
    assert not decision.approved
    assert "heat" in decision.reason


def test_reduces_heat_cap_during_high_volatility():
    open_positions = [
        Position("ETH/USDT", "crypto", Side.LONG, 100, 95, risk_pct=0.035),
    ]
    account = make_account(open_positions=open_positions, high_volatility=True)
    decision = RiskEngine().evaluate(make_signal(), account)
    assert not decision.approved
    assert "heat" in decision.reason

    # the identical heat load is fine once volatility regime is normal
    account_normal_vol = make_account(open_positions=open_positions, high_volatility=False)
    assert RiskEngine().evaluate(make_signal(), account_normal_vol).approved


def test_correlation_guard_rejects_combined_cluster():
    open_positions = [Position("ETH/USDT", "crypto", Side.LONG, 100, 95, risk_pct=0.01)]
    correlations = {frozenset({"ETH/USDT", "BTC/USDT"}): 0.85}
    account = make_account(open_positions=open_positions, correlations=correlations)
    decision = RiskEngine().evaluate(make_signal(asset="BTC/USDT"), account)
    assert not decision.approved
    assert "correlation" in decision.reason


def test_rejects_when_asset_class_at_max_positions():
    open_positions = [
        Position(f"ALT{i}/USDT", "crypto", Side.LONG, 100, 95, risk_pct=0.005) for i in range(5)
    ]
    decision = RiskEngine().evaluate(make_signal(), make_account(open_positions=open_positions))
    assert not decision.approved
    assert "max concurrent positions" in decision.reason


def test_daily_loss_circuit_breaker_blocks_new_trades():
    decision = RiskEngine().evaluate(make_signal(), make_account(daily_pnl_pct=-0.03))
    assert not decision.approved
    assert "daily loss" in decision.reason


def test_weekly_loss_halves_position_size_instead_of_rejecting():
    decision = RiskEngine().evaluate(make_signal(), make_account(weekly_pnl_pct=-0.06))
    assert decision.approved
    assert decision.approved_order.risk_pct == pytest.approx(0.005)


def test_max_drawdown_triggers_kill_switch():
    account = make_account(equity=82_000.0, peak_equity=100_000.0)  # 18% drawdown >= 17.5% default
    decision = RiskEngine().evaluate(make_signal(), account)
    assert not decision.approved
    assert "drawdown" in decision.reason


def test_consecutive_loss_pause_blocks_asset_class_during_cooldown():
    account = make_account(
        consecutive_losses={"crypto": 4},
        last_loss_at={"crypto": NOW - timedelta(hours=1)},  # within 4h default cooldown
    )
    decision = RiskEngine().evaluate(make_signal(), account)
    assert not decision.approved
    assert "paused" in decision.reason

    account_after_cooldown = make_account(
        consecutive_losses={"crypto": 4},
        last_loss_at={"crypto": NOW - timedelta(hours=5)},  # cooldown expired
    )
    assert RiskEngine().evaluate(make_signal(), account_after_cooldown).approved


def test_pre_event_derisking_halves_position_size():
    decision = RiskEngine().evaluate(make_signal(), make_account(minutes_to_next_major_event=90))
    assert decision.approved
    assert decision.approved_order.risk_pct == pytest.approx(0.005)


def test_manual_kill_switch_blocks_everything():
    decision = RiskEngine().evaluate(make_signal(), make_account(kill_switch=True))
    assert not decision.approved
    assert "kill switch" in decision.reason


def test_approved_order_cannot_be_constructed_outside_risk_engine():
    with pytest.raises(PermissionError):
        ApprovedOrder(
            asset="BTC/USDT",
            asset_class="crypto",
            side=Side.LONG,
            entry_price=100.0,
            stop_price=95.0,
            target_price=112.0,
            position_size=200.0,
            risk_pct=0.01,
            issuer=object(),  # not the Risk Engine's private token
        )


def test_risk_config_rejects_risk_pct_above_hard_cap():
    with pytest.raises(ValueError):
        RiskConfig(risk_pct=0.05)


def make_exit_signal(**overrides) -> ExitSignal:
    base = ExitSignal(
        asset="BTC/USDT",
        asset_class="crypto",
        reason="sentiment turned sharply negative",
        source="llm",
        confidence=0.8,
        timestamp=NOW,
    )
    return replace(base, **overrides)


def _account_with_btc_position(**overrides) -> AccountState:
    position = Position("BTC/USDT", "crypto", Side.LONG, entry_price=100.0, stop_price=95.0, risk_pct=0.01)
    return make_account(open_positions=[position], **overrides)


def test_exit_signal_approved_when_position_exists_and_confident():
    decision = RiskEngine().evaluate_exit_signal(make_exit_signal(), _account_with_btc_position())
    assert decision.approved
    assert decision.approved_exit.asset == "BTC/USDT"


def test_exit_signal_rejected_when_no_matching_position():
    decision = RiskEngine().evaluate_exit_signal(make_exit_signal(), make_account())  # no open positions
    assert not decision.approved
    assert "no open position" in decision.reason


def test_exit_signal_rejected_when_confidence_too_low():
    decision = RiskEngine().evaluate_exit_signal(
        make_exit_signal(confidence=0.2), _account_with_btc_position()
    )
    assert not decision.approved
    assert "confidence" in decision.reason


def test_exit_signal_approved_even_when_kill_switch_active():
    # De-risking must never be blocked by the mechanisms designed to reduce risk.
    decision = RiskEngine().evaluate_exit_signal(
        make_exit_signal(), _account_with_btc_position(kill_switch=True)
    )
    assert decision.approved


def test_exit_signal_approved_even_during_drawdown_and_daily_loss_halt():
    account = _account_with_btc_position(equity=80_000.0, peak_equity=100_000.0, daily_pnl_pct=-0.05)
    decision = RiskEngine().evaluate_exit_signal(make_exit_signal(), account)
    assert decision.approved


def test_approved_exit_cannot_be_constructed_outside_risk_engine():
    with pytest.raises(PermissionError):
        ApprovedExit(
            asset="BTC/USDT",
            asset_class="crypto",
            reason="test",
            issuer=object(),  # not the Risk Engine's private token
        )
