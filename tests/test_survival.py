import pytest

from trading.risk.models import AccountState, RiskConfig
from trading.risk.survival import SurvivalEngine, SurvivalTier
from trading.risk.garch import GARCHForecastResult
from trading.risk.hmm_regime import HMMRegimeResult
from trading.risk.evt import EVTRiskResult
from trading.risk.tier_state import TierState, save_state, load_state


def test_survival_normal_tier():
    engine = SurvivalEngine()
    account = AccountState(equity=10000.0, peak_equity=10000.0)
    status = engine.evaluate_survival_status(account)

    assert status.tier == SurvivalTier.NORMAL
    assert status.allow_new_entries is True
    assert status.effective_risk_multiplier > 0.0
    assert status.min_confidence_floor <= 0.60


def test_survival_caution_tier_on_losses():
    engine = SurvivalEngine()
    account = AccountState(
        equity=9500.0,
        peak_equity=10000.0,
        consecutive_losses={"crypto": 2},
    )
    status = engine.evaluate_survival_status(account)

    assert status.tier == SurvivalTier.CAUTION
    assert status.allow_new_entries is True
    assert status.effective_risk_multiplier <= 0.50
    assert status.min_confidence_floor >= 0.70


def test_survival_mode_on_daily_loss():
    engine = SurvivalEngine()
    account = AccountState(
        equity=9000.0,
        peak_equity=10000.0,
        daily_pnl_pct=-0.03,  # -3% exceeds 2.5% daily loss limit
    )
    status = engine.evaluate_survival_status(account)

    assert status.tier == SurvivalTier.SURVIVAL
    assert status.allow_new_entries is False


def test_survival_cooldown_on_kill_switch_or_max_dd():
    engine = SurvivalEngine(RiskConfig(max_drawdown=0.15))
    account = AccountState(equity=8000.0, peak_equity=10000.0)  # 20% DD > 15% Max DD
    status = engine.evaluate_survival_status(account)

    assert status.tier == SurvivalTier.COOLDOWN
    assert status.allow_new_entries is False
    assert status.effective_risk_multiplier == 0.0


# ---------------------------------------------------------------------------
# WAVE4 EX1b additions: NORMAL cap, hysteresis automaton, tier persistence
# ---------------------------------------------------------------------------

def _garch(scale: float = 1.0, high_vol: bool = False) -> GARCHForecastResult:
    return GARCHForecastResult(
        conditional_volatility=0.02 * scale,
        annualized_volatility=0.30,
        omega=1e-6,
        alpha=0.08,
        beta=0.90,
        persistence=0.98,
        unconditional_volatility=0.02,
        is_high_volatility=high_vol,
        volatility_scale_factor=scale,
    )


def test_wave4_normal_multiplier_never_exceeds_one():
    """NORMAL tier must never amplify risk above base capacity (was 1.25x)."""
    engine = SurvivalEngine()
    account = AccountState(equity=10000.0, peak_equity=10000.0)
    hot_garch = _garch(scale=1.4)   # would push raw multiplier to 1.25x pre-fix
    status = engine.evaluate_survival_status(account, garch_res=hot_garch)

    assert status.tier == SurvivalTier.NORMAL
    assert status.effective_risk_multiplier == 1.0


def test_wave4_escalation_is_immediate():
    """A breach escalates the held tier on the very first cycle (no dwell)."""
    engine = SurvivalEngine(min_dwell_cycles=3)
    healthy = AccountState(equity=10000.0, peak_equity=10000.0)

    assert engine.evaluate_survival_status(healthy).tier is SurvivalTier.NORMAL

    # One bad day -> SURVIVAL immediately.
    breached = AccountState(equity=9000.0, peak_equity=10000.0, daily_pnl_pct=-0.03)
    assert engine.evaluate_survival_status(breached).tier is SurvivalTier.SURVIVAL

    # Kill switch -> COOLDOWN immediately.
    killed = AccountState(equity=9000.0, peak_equity=10000.0, kill_switch=True)
    assert engine.evaluate_survival_status(killed).tier is SurvivalTier.COOLDOWN


def test_wave4_deescalation_requires_dwell_cycles():
    """Recovery steps down one level only after N consecutive below-tier cycles."""
    engine = SurvivalEngine(min_dwell_cycles=3)
    healthy = AccountState(equity=10000.0, peak_equity=10000.0)

    engine.evaluate_survival_status(healthy)                       # NORMAL
    engine.evaluate_survival_status(_caution_account()).tier       # -> CAUTION instant
    assert engine._current_tier() is SurvivalTier.CAUTION

    # Healthy cycles below CAUTION: dwell counter climbs, tier holds.
    assert engine.evaluate_survival_status(healthy).tier is SurvivalTier.CAUTION
    assert engine.tier_state.below_count == 1
    assert engine.evaluate_survival_status(healthy).tier is SurvivalTier.CAUTION
    assert engine.tier_state.below_count == 2
    # Third consecutive below-tier cycle completes the required dwell -> one step down.
    assert engine.evaluate_survival_status(healthy).tier is SurvivalTier.NORMAL
    assert engine.tier_state.below_count == 0


def _caution_account() -> AccountState:
    return AccountState(equity=9500.0, peak_equity=10000.0, consecutive_losses={"crypto": 2})


def test_wave4_dwell_counter_resets_on_rebreach():
    """A re-breach during the dwell window resets the de-escalation count."""
    engine = SurvivalEngine(min_dwell_cycles=3)
    healthy = AccountState(equity=10000.0, peak_equity=10000.0)

    engine.evaluate_survival_status(_caution_account())            # -> CAUTION
    engine.evaluate_survival_status(healthy)                       # below_count=1
    engine.evaluate_survival_status(_caution_account())            # re-breach: hold + reset
    assert engine._current_tier() is SurvivalTier.CAUTION
    assert engine.tier_state.below_count == 0
    assert engine.evaluate_survival_status(healthy).tier is SurvivalTier.CAUTION
    assert engine.tier_state.below_count == 1  # counting restarted


def test_wave4_tier_state_save_reload_roundtrip(tmp_path):
    path = str(tmp_path / "tier.json")
    state = TierState(tier="cooldown", entered_cycle=42, below_count=0)
    assert save_state(state, path) is True
    loaded = load_state(path)
    assert loaded == state


def test_wave4_restart_mid_cooldown_restores_tier(tmp_path):
    """Restart cannot launder a defended tier back to NORMAL."""
    path = str(tmp_path / "tier.json")

    first = SurvivalEngine(state_path=path)
    first.evaluate_survival_status(AccountState(equity=8000.0, peak_equity=10000.0))  # COOLDOWN
    assert first._current_tier() is SurvivalTier.COOLDOWN
    assert load_state(path).tier == "cooldown"

    # "Restart": brand-new engine pointed at the same state file.
    second = SurvivalEngine(state_path=path)
    assert second._current_tier() is SurvivalTier.COOLDOWN
    healthy = AccountState(equity=10000.0, peak_equity=10000.0)
    # Still defended despite healthy account until dwell elapses...
    for _ in range(2):
        assert second.evaluate_survival_status(healthy).tier is SurvivalTier.COOLDOWN
    # ...then single step down to SURVIVAL, not straight to NORMAL.
    assert second.evaluate_survival_status(healthy).tier is SurvivalTier.SURVIVAL


def test_wave4_corrupt_state_file_fails_open_to_normal(tmp_path, caplog):
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json!!", encoding="utf-8")
    with caplog.at_level("WARNING", logger="trading.risk.tier_state"):
        engine = SurvivalEngine(state_path=str(path))
    assert engine._current_tier() is SurvivalTier.NORMAL
    assert any("Could not load risk tier state" in r.message for r in caplog.records)


def test_wave4_missing_state_file_is_silent_default(tmp_path):
    engine = SurvivalEngine(state_path=str(tmp_path / "does_not_exist.json"))
    assert engine._current_tier() is SurvivalTier.NORMAL


# ---------------------------------------------------------------------------
# SQUAD DM-1 wave-1 (F-0342): Decimal dual-run epsilon-flip detector
# ---------------------------------------------------------------------------

import logging  # noqa: E402
from decimal import Decimal  # noqa: E402

from trading.risk.survival import _epsilon_flips, _log_epsilon_flips  # noqa: E402


def test_epsilon_flip_detector_flags_true_decimal_breach_float_miss():
    """Boundary-crafted equity: exact Decimal drawdown reaches the 0.175 limit
    but float rounding lands just below it -> DRAWDOWN_EPSILON_FLIP."""
    # peak=250000.31, equity=206250.25575:
    #   exact dd = 0.175 (>= limit) ; float dd = 0.17499999999999996 (< limit)
    account = AccountState(equity=206250.25575, peak_equity=250000.31)

    flips = _epsilon_flips(account, max_drawdown=0.175, daily_loss_limit=0.025)
    kinds = [f["kind"] for f in flips]
    assert "drawdown" in kinds

    flip = next(f for f in flips if f["kind"] == "drawdown")
    assert flip["decimal_value"] >= Decimal("0.175")
    assert flip["float_value"] < 0.175
    # Sanity: the float gate below would NOT fire on these stored floats.
    assert not ((account.peak_equity - account.equity) / account.peak_equity) >= 0.175


def test_epsilon_flip_detector_daily_loss_boundary():
    """Exact daily-loss recompute crosses -0.025 but the stored fraction does
    not -> flagged as a daily_loss flip with both values attached."""
    account = AccountState(
        equity=9750.12675,
        peak_equity=10000.13,
        day_start_settled_equity=10000.13,  # exact pnl = -0.025 exactly
        daily_pnl_pct=-0.024999999999999977,  # what float rounding stored
    )

    flips = _epsilon_flips(account, max_drawdown=0.175, daily_loss_limit=0.025)
    kinds = [f["kind"] for f in flips]
    assert "daily_loss" in kinds

    flip = next(f for f in flips if f["kind"] == "daily_loss")
    assert flip["decimal_value"] <= Decimal("-0.025")
    assert flip["float_value"] > -0.025


def test_epsilon_flip_detector_silent_when_paths_agree():
    """Healthy / far-from-boundary accounts must produce zero flips."""
    healthy = AccountState(equity=10000.0, peak_equity=10000.0)
    assert _epsilon_flips(healthy, max_drawdown=0.175, daily_loss_limit=0.025) == []

    clear_loss = AccountState(
        equity=9000.0,
        peak_equity=10000.0,
        day_start_settled_equity=10000.0,
        daily_pnl_pct=-0.10,  # both paths agree this breaches
    )
    assert _epsilon_flips(clear_loss, max_drawdown=0.175, daily_loss_limit=0.025) == []


def test_epsilon_flip_logged_via_engine_without_changing_tier(caplog):
    """End-to-end: evaluate_survival_status logs DRAWDOWN_EPSILON_FLIP for the
    crafted boundary account while returning the SAME tier the float gates
    dictate (observation only -- behavior unchanged)."""
    engine = SurvivalEngine()
    account = AccountState(equity=206250.25575, peak_equity=250000.31)

    with caplog.at_level(logging.WARNING, logger="trading.risk.survival"):
        status = engine.evaluate_survival_status(account)

    flips = [
        r
        for r in caplog.records
        if r.getMessage().startswith("DRAWDOWN_EPSILON_FLIP") and "[drawdown]" in r.getMessage()
    ]
    assert flips, "expected a logged DRAWDOWN_EPSILON_FLIP event"
    msg = flips[0].getMessage()
    assert "exact-decimal" in msg and "float=" in msg and "observation only" in msg
    # Behavior unchanged by the observation: the float path still evaluates
    # dd < 17.5% so NO cooldown fires (that is exactly the F-0342 false
    # negative being made visible); the account still throttles to CAUTION via
    # the ordinary moderate-drawdown rule (dd >= 0.6 * max_drawdown).
    assert status.tier is SurvivalTier.CAUTION
    assert status.allow_new_entries is True


def test_epsilon_flip_log_helper_formats_both_values(caplog):
    """Direct unit coverage of the formatter: both representations present."""
    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="trading.risk.survival"):
        _log_epsilon_flips(
            [
                {
                    "kind": "drawdown",
                    "float_value": 0.17499999999999996,
                    "decimal_value": Decimal("0.175"),
                    "limit": 0.175,
                }
            ]
        )
    msgs = [r.getMessage() for r in caplog.records if r.getMessage().startswith("DRAWDOWN_EPSILON_FLIP")]
    assert len(msgs) == 1
    assert "0.17499999999999996" in msgs[0]
    assert "0.175" in msgs[0]
    assert "F-0342" in msgs[0]
