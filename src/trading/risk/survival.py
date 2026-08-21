import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from trading.risk.models import AccountState, RiskConfig
from trading.risk.garch import GARCHForecastResult
from trading.risk.hmm_regime import HMMRegimeResult
from trading.risk.evt import EVTRiskResult

logger = logging.getLogger("trading.risk.survival")


class SurvivalTier(Enum):
    NORMAL = "normal"          # Standard operations (1.0x risk capacity)
    CAUTION = "caution"        # Throttled operations (0.5x risk capacity, high confidence required)
    SURVIVAL = "survival"      # Capital defense mode (de-risking only, no new risk taken)
    COOLDOWN = "cooldown"      # Circuit breaker active / system paused


@dataclass(frozen=True)
class AccountSurvivalStatus:
    """Consolidated operational health status and sovereign risk constraints."""
    tier: SurvivalTier
    effective_risk_multiplier: float  # Multiplier applied to base risk_pct (0.0 to 1.0)
    allow_new_entries: bool           # Whether new entry orders may be proposed
    min_confidence_floor: float       # Minimum strategy confidence to clear entry gate
    active_regime: str                # Current HMM market regime
    garch_vol_forecast: float         # 1-step ahead conditional volatility
    evt_tail_var_99: float            # 99% Tail-VaR estimate
    survival_rationale: str           # Human-readable rationale for current operational tier


class SurvivalEngine:
    """Autonomous Survival & Capital Defense Engine (Automaton-Inspired).

    Monitors account equity drawdowns, consecutive losses, GARCH volatility spikes,
    HMM regime shifts, and EVT tail risk to dynamically modulate operational tiers.
    """

    def __init__(self, config: Optional[RiskConfig] = None):
        self.config = config or RiskConfig()

    def evaluate_survival_status(
        self,
        account: AccountState,
        garch_res: Optional[GARCHForecastResult] = None,
        hmm_res: Optional[HMMRegimeResult] = None,
        evt_res: Optional[EVTRiskResult] = None,
    ) -> AccountSurvivalStatus:
        """Evaluates comprehensive account health and computes operational survival tier."""
        cfg = self.config

        # 1. Check Hard Kill Switch or Max Drawdown -> COOLDOWN
        if account.kill_switch:
            return AccountSurvivalStatus(
                tier=SurvivalTier.COOLDOWN,
                effective_risk_multiplier=0.0,
                allow_new_entries=False,
                min_confidence_floor=1.0,
                active_regime=hmm_res.current_regime if hmm_res else "unknown",
                garch_vol_forecast=garch_res.conditional_volatility if garch_res else 0.02,
                evt_tail_var_99=evt_res.cvar_99 if evt_res else 0.05,
                survival_rationale="Manual or emergency kill switch is engaged",
            )

        drawdown = 0.0
        if account.peak_equity > 0:
            drawdown = (account.peak_equity - account.equity) / account.peak_equity
        
        if drawdown >= cfg.max_drawdown:
            return AccountSurvivalStatus(
                tier=SurvivalTier.COOLDOWN,
                effective_risk_multiplier=0.0,
                allow_new_entries=False,
                min_confidence_floor=1.0,
                active_regime=hmm_res.current_regime if hmm_res else "unknown",
                garch_vol_forecast=garch_res.conditional_volatility if garch_res else 0.02,
                evt_tail_var_99=evt_res.cvar_99 if evt_res else 0.05,
                survival_rationale=f"Max drawdown reached ({drawdown:.1%} >= {cfg.max_drawdown:.1%})",
            )

        # 2. Check Daily Loss Limit or severe consecutive losses -> SURVIVAL
        max_consecutive_losses = max(account.consecutive_losses.values()) if account.consecutive_losses else 0
        if account.daily_pnl_pct <= -cfg.daily_loss_limit or max_consecutive_losses >= cfg.consecutive_loss_limit:
            return AccountSurvivalStatus(
                tier=SurvivalTier.SURVIVAL,
                effective_risk_multiplier=0.0,
                allow_new_entries=False,
                min_confidence_floor=0.85,
                active_regime=hmm_res.current_regime if hmm_res else "unknown",
                garch_vol_forecast=garch_res.conditional_volatility if garch_res else 0.02,
                evt_tail_var_99=evt_res.cvar_99 if evt_res else 0.05,
                survival_rationale=f"Survival mode active: daily PnL {account.daily_pnl_pct:.1%} or {max_consecutive_losses} consecutive losses",
            )

        # 3. Check Moderate Stress Indicators -> CAUTION
        # (Drawdown >= 10%, weekly loss limit, high volatility regime, or EVT extreme tail risk)
        is_bear_or_high_vol = (hmm_res and hmm_res.is_high_volatility) or (garch_res and garch_res.is_high_volatility)
        is_weekly_stressed = account.weekly_pnl_pct <= -cfg.weekly_loss_limit
        is_moderate_dd = drawdown >= (cfg.max_drawdown * 0.60)
        is_evt_tail_spike = evt_res is not None and evt_res.cvar_99 >= 0.08

        if is_bear_or_high_vol or is_weekly_stressed or is_moderate_dd or is_evt_tail_spike or max_consecutive_losses >= 2:
            multiplier = 0.50
            if garch_res:
                multiplier *= garch_res.volatility_scale_factor
            if evt_res:
                multiplier *= evt_res.recommended_risk_scale
            multiplier = float(min(multiplier, 0.50))

            reasons = []
            if is_bear_or_high_vol: reasons.append("High volatility / Bear regime")
            if is_weekly_stressed: reasons.append("Weekly loss threshold")
            if is_moderate_dd: reasons.append(f"Drawdown {drawdown:.1%}")
            if is_evt_tail_spike: reasons.append(f"EVT Tail-VaR {evt_res.cvar_99:.1%}")
            if max_consecutive_losses >= 2: reasons.append(f"{max_consecutive_losses} consecutive losses")

            return AccountSurvivalStatus(
                tier=SurvivalTier.CAUTION,
                effective_risk_multiplier=multiplier,
                allow_new_entries=True,
                min_confidence_floor=0.70,
                active_regime=hmm_res.current_regime if hmm_res else "unknown",
                garch_vol_forecast=garch_res.conditional_volatility if garch_res else 0.02,
                evt_tail_var_99=evt_res.cvar_99 if evt_res else 0.05,
                survival_rationale=f"Caution mode: {', '.join(reasons)}",
            )

        # 4. NORMAL Operating Tier
        base_multiplier = 1.0
        if garch_res:
            base_multiplier *= garch_res.volatility_scale_factor
        if evt_res:
            base_multiplier *= evt_res.recommended_risk_scale
        base_multiplier = float(min(base_multiplier, 1.25))

        return AccountSurvivalStatus(
            tier=SurvivalTier.NORMAL,
            effective_risk_multiplier=base_multiplier,
            allow_new_entries=True,
            min_confidence_floor=0.55,
            active_regime=hmm_res.current_regime if hmm_res else "trending_bull",
            garch_vol_forecast=garch_res.conditional_volatility if garch_res else 0.02,
            evt_tail_var_99=evt_res.cvar_99 if evt_res else 0.04,
            survival_rationale="Nominal operating conditions: equity healthy and risk parameters normal",
        )
