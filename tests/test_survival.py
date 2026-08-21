import pytest

from trading.risk.models import AccountState, RiskConfig
from trading.risk.survival import SurvivalEngine, SurvivalTier
from trading.risk.garch import GARCHForecastResult
from trading.risk.hmm_regime import HMMRegimeResult
from trading.risk.evt import EVTRiskResult


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
