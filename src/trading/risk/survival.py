import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

from trading.core.money import decimal_from_float
from trading.risk.models import AccountState, RiskConfig
from trading.risk.garch import GARCHForecastResult
from trading.risk.hmm_regime import HMMRegimeResult
from trading.risk.evt import EVTRiskResult
from trading.risk.tier_state import (
    DEFAULT_SCOPE,
    UNKNOWN_TIER,
    TierState,
    load_state,
    save_state,
)

logger = logging.getLogger("trading.risk.survival")

# De-escalation hysteresis: the raw evaluation must sit BELOW the current tier
# for this many consecutive cycles before stepping down one level. Escalation
# remains instantaneous. Overridable per-engine via constructor; kept as a
# module constant because RiskConfig is frozen and shared.
MIN_DWELL_CYCLES = 3


class SurvivalTier(Enum):
    NORMAL = "normal"          # Standard operations (1.0x risk capacity, hard-capped)
    CAUTION = "caution"        # Throttled operations (0.5x risk capacity, high confidence required)
    SURVIVAL = "survival"      # Capital defense mode (de-risking only, no new risk taken)
    COOLDOWN = "cooldown"      # Circuit breaker active / system paused


_TIER_ORDER = (
    SurvivalTier.NORMAL,
    SurvivalTier.CAUTION,
    SurvivalTier.SURVIVAL,
    SurvivalTier.COOLDOWN,
)


def _severity(tier: SurvivalTier) -> int:
    return _TIER_ORDER.index(tier)


# ---------------------------------------------------------------------------
# SQUAD DM-1 wave-1 (F-0342): Decimal dual-run for limit-boundary comparisons.
#
# The stored AccountState floats are NOT converted (blast radius); instead the
# drawdown / daily-loss percentages are recomputed exactly in Decimal from the
# SAME stored floats and compared against the same limits. Where the float
# comparison breaches a limit but the exact one does not -- the epsilon-flip
# class behind F-0342 -- a DRAWDOWN_EPSILON_FLIP event is logged with both
# values. Behavior is unchanged: these are observations only, never gate inputs.
# ---------------------------------------------------------------------------


def _epsilon_flips(
    account: AccountState,
    max_drawdown: float,
    daily_loss_limit: float,
    fallback_anchor: Optional[float] = None,
) -> list[dict]:
    """Return one entry per limit boundary where the exact Decimal path crosses
    but the legacy float path does not (F-0342 epsilon-flip class).

    Direction matters: this catches FALSE NEGATIVES of the float gate -- the
    account has genuinely reached a limit boundary while float rounding hides
    it -- which is the dangerous miss, not the cosmetic false alarm.

    R2 / VB-001: the daily-loss denominator is ``day_start_settled_equity``
    when recorded. When it is None (no day boundary observed yet) the exact
    side previously fed current equity in as BOTH numerator and denominator,
    structurally zeroing pnl_exact and blinding the detector; instead the
    prior cycle's settled equity carried in TierState (``fallback_anchor``)
    is used, and when even that is unavailable the daily-loss check is
    skipped with an explicit ANCHOR_UNAVAILABLE event rather than silently
    computing a meaningless 0.0.
    """
    flips: list[dict] = []

    if account.peak_equity > 0:
        dd_float = (account.peak_equity - account.equity) / account.peak_equity
        peak_d = decimal_from_float(account.peak_equity)
        eq_d = decimal_from_float(account.equity)
        dd_exact = (peak_d - eq_d) / peak_d
        lim_d = Decimal(str(max_drawdown))
        if (dd_exact >= lim_d) and not (dd_float >= max_drawdown):
            flips.append(
                {
                    "kind": "drawdown",
                    "float_value": dd_float,
                    "decimal_value": dd_exact,
                    "limit": max_drawdown,
                }
            )

    denom = account.day_start_settled_equity
    if denom is None:
        # R2 / VB-001: never substitute current equity -- it cancels out.
        denom = fallback_anchor
    if denom is None or denom <= 0:
        logger.info(
            "ANCHOR_UNAVAILABLE: no day-start (or carried) settled equity "
            "anchor yet; exact-decimal daily-loss dual-run skipped"
        )
        return flips

    # VB-046/VB-071: BOTH float and Decimal sides from (equity, denom) at same instant.
    # Eliminates vintage-mismatch false positives in F-0342 evidence base.
    pnl_float = (account.equity - denom) / denom
    pnl_exact = (
        decimal_from_float(account.equity) - decimal_from_float(denom)
    ) / decimal_from_float(denom)
    lim_d = Decimal(str(daily_loss_limit))
    breach_float = pnl_float <= -daily_loss_limit
    breach_exact = pnl_exact <= -lim_d
    if breach_exact and not breach_float:
        flips.append(
            {
                "kind": "daily_loss",
                "float_value": pnl_float,
                "decimal_value": pnl_exact,
                "limit": daily_loss_limit,
            }
        )
    return flips


def _log_epsilon_flips(flips: list[dict], seen_set: set | None = None) -> None:
    for flip in flips:
        logger.warning(
            "DRAWDOWN_EPSILON_FLIP [%s]: exact-decimal=%s crosses limit %.16f "
            "but float=%.18f does not -- float gate misses a true boundary "
            "crossing (F-0342 class; observation only, gate behavior unchanged)",
            flip["kind"],
            str(flip["decimal_value"]),
            flip["limit"],
            flip["float_value"],
        )


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

    Tier dynamics:
      - Escalation is INSTANT: any cycle whose raw evaluation is more severe than
        the current tier moves the tier up immediately.
      - De-escalation requires hysteresis: the raw evaluation must stay strictly
        below the current tier for ``min_dwell_cycles`` consecutive cycles before
        stepping down exactly ONE level. This prevents tier flapping.
      - When ``state_path`` is provided, tier state is loaded at construction and
        atomically persisted after every evaluation, so a process restart cannot
        silently reset a defended tier back to NORMAL.
    """

    def __init__(
        self,
        config: Optional[RiskConfig] = None,
        min_dwell_cycles: Optional[int] = None,
        state_path: Optional[str] = None,
        scope: str = DEFAULT_SCOPE,
    ):
        self.config = config or RiskConfig()
        self.min_dwell_cycles = (
            int(min_dwell_cycles) if min_dwell_cycles is not None else MIN_DWELL_CYCLES
        )
        self.state_path = state_path
        # R2 / VA-016: persistence scope ('global' preserves legacy behaviour;
        # per-symbol loops pass their own scope so tiers stop cross-coupling).
        self.scope = scope or DEFAULT_SCOPE
        self.cycle_count = 0
        # VB-029: dedup epsilon flip warnings per boundary key.
        self._seen_epsilon_flips: set = set()
        if state_path:
            self.tier_state = load_state(state_path, scope=self.scope)
            try:
                SurvivalTier(self.tier_state.tier)
            except ValueError:
                if self.tier_state.tier == UNKNOWN_TIER:
                    # R2 / VA-015 fail-closed: nothing trustworthy known ->
                    # hold the CAUTION-minimum instead of resetting to NORMAL.
                    logger.warning(
                        "Tier state UNKNOWABLE for scope %s; starting at "
                        "CAUTION-minimum (never NORMAL)",
                        self.scope,
                    )
                    self.tier_state = TierState(
                        tier=SurvivalTier.CAUTION.value, last_settled_equity=self.tier_state.last_settled_equity
                    )
                else:
                    logger.warning(
                        "Invalid persisted tier '%s'; resetting to NORMAL", self.tier_state.tier
                    )
                    self.tier_state = TierState(tier="normal")
        else:
            # No persistence requested: start from a fresh in-memory state.
            self.tier_state = TierState(tier="normal")

    # ------------------------------------------------------------------
    # Raw (stateless) tier ladder — unchanged semantics, NORMAL capped at 1.0
    # ------------------------------------------------------------------
    def _evaluate_raw_status(
        self,
        account: AccountState,
        garch_res: Optional[GARCHForecastResult],
        hmm_res: Optional[HMMRegimeResult],
        evt_res: Optional[EVTRiskResult],
        fallback_anchor: Optional[float] = None,
    ) -> AccountSurvivalStatus:
        cfg = self.config

        # SQUAD DM-1 wave-1 (F-0342): observation-only Decimal dual-run. Logged
        # before any gate fires so the divergence record exists even when the
        # float path rejects; never influences tier selection.
        _log_epsilon_flips(
            _epsilon_flips(
                account, cfg.max_drawdown, cfg.daily_loss_limit, fallback_anchor=fallback_anchor
            ),
            seen_set=getattr(self, "_seen_epsilon_flips", set()),
        )

        if account.kill_switch:
            return AccountSurvivalStatus(
                tier=SurvivalTier.COOLDOWN,                effective_risk_multiplier=0.0,                allow_new_entries=False,                min_confidence_floor=1.0,                active_regime=hmm_res.current_regime if hmm_res else "unknown",                garch_vol_forecast=garch_res.conditional_volatility if garch_res else 0.02,                evt_tail_var_99=evt_res.cvar_99 if evt_res else 0.05,                survival_rationale="Manual or emergency kill switch is engaged",            )

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

        # 4. NORMAL Operating Tier -- multiplier hard-capped at 1.0 (never amplified).
        base_multiplier = 1.0
        if garch_res:
            base_multiplier *= garch_res.volatility_scale_factor
        if evt_res:
            base_multiplier *= evt_res.recommended_risk_scale
        base_multiplier = float(min(base_multiplier, 1.0))

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

    # ------------------------------------------------------------------
    # Hysteresis automaton around the raw ladder
    # ------------------------------------------------------------------
    def _current_tier(self) -> SurvivalTier:
        try:
            return SurvivalTier(self.tier_state.tier)
        except ValueError:
            if self.tier_state.tier == UNKNOWN_TIER:
                # R2 / VA-015: unknown must never be read as NORMAL by callers
                # of the raw accessor; evaluate_survival_status maps it onto
                # CAUTION before use, but keep the accessor honest too.
                return SurvivalTier.CAUTION
            return SurvivalTier.NORMAL

    def _tier_template(self, tier: SurvivalTier, raw: AccountSurvivalStatus) -> AccountSurvivalStatus:
        """Per-tier operational constants for the *held* tier."""
        if tier is SurvivalTier.COOLDOWN:
            mult, entries, floor, why = 0.0, False, 1.0, "Cooldown: circuit breaker engaged"
        elif tier is SurvivalTier.SURVIVAL:
            mult, entries, floor, why = 0.0, False, 0.85, "Survival mode: capital defense, no new entries"
        elif tier is SurvivalTier.CAUTION:
            mult, entries, floor, why = 0.50, True, 0.70, "Caution mode: throttled risk capacity"
        else:
            mult, entries, floor, why = 1.00, True, 0.55, "Nominal operating conditions"
        return AccountSurvivalStatus(
            tier=tier,
            effective_risk_multiplier=mult,
            allow_new_entries=entries,
            min_confidence_floor=floor,
            active_regime=raw.active_regime,
            garch_vol_forecast=raw.garch_vol_forecast,
            evt_tail_var_99=raw.evt_tail_var_99,
            survival_rationale=(
                raw.survival_rationale if raw.tier is tier
                else f"{why} [hysteresis; raw eval: {raw.tier.value}; below_count={self.tier_state.below_count}/{self.min_dwell_cycles}]"
            ),
        )

    def evaluate_survival_status(
        self,
        account: AccountState,
        garch_res: Optional[GARCHForecastResult] = None,
        hmm_res: Optional[HMMRegimeResult] = None,
        evt_res: Optional[EVTRiskResult] = None,
    ) -> AccountSurvivalStatus:
        """Evaluates comprehensive account health and computes operational survival tier.

        Public signature is unchanged. Each call advances the internal cycle
        counter by one and runs the tier automaton (instant escalation,
        dwell-gated single-step de-escalation, optional persistence).
        """
        self.cycle_count += 1
        # R2 / VA-015: an UNKNOWABLE held tier (state store down, no cache) is
        # consumed as a CAUTION-minimum -- never as NORMAL.
        current = self._current_tier()
        if current is SurvivalTier.NORMAL and self.tier_state.tier == UNKNOWN_TIER:
            logger.warning(
                "Held tier '%s' for scope %s is not trustworthy (state store "
                "unavailable); treating as CAUTION-minimum this cycle",
                UNKNOWN_TIER,
                self.scope,
            )
            self.tier_state = TierState(tier=SurvivalTier.CAUTION.value)
            current = self._current_tier()

        # R2 / VB-001: carry the prior settled equity so the exact-decimal
        # daily-loss dual-run keeps a usable denominator on sessions where
        # day_start_settled_equity was never recorded.
        prior_anchor = self.tier_state.last_settled_equity
        raw = self._evaluate_raw_status(account, garch_res, hmm_res, evt_res, fallback_anchor=prior_anchor)

        raw_sev = _severity(raw.tier)
        cur_sev = _severity(current)

        new_tier = current
        below_count = self.tier_state.below_count
        reason = "hold"

        if raw_sev > cur_sev:
            # Escalation is immediate.
            new_tier = raw.tier
            below_count = 0
            reason = f"escalation: {raw.survival_rationale}"
        elif raw_sev < cur_sev:
            below_count += 1
            if below_count >= self.min_dwell_cycles:
                new_tier = _TIER_ORDER[max(cur_sev - 1, 0)]
                below_count = 0
                reason = f"de-escalation: {self.min_dwell_cycles} consecutive below-tier cycles elapsed"
            else:
                reason = f"hold: below-tier {below_count}/{self.min_dwell_cycles}"
        else:
            below_count = 0
            reason = f"hold: raw eval matches tier ({raw.survival_rationale})"

        prev_tier = current
        # R2 / VB-001: record this cycle's settled equity so the NEXT cycle's
        # exact-decimal daily-loss check has a denominator even when no day
        # boundary has been observed yet.
        carried_anchor = (
            account.day_start_settled_equity
            if account.day_start_settled_equity is not None
            else account.equity
        )
        self.tier_state = TierState(
            tier=new_tier.value,
            entered_cycle=self.cycle_count if new_tier is not prev_tier else self.tier_state.entered_cycle,
            below_count=below_count,
            last_settled_equity=carried_anchor,
        )
        if new_tier is not prev_tier:
            logger.info("%s -> %s reason=%s", prev_tier.value, new_tier.value, reason)
            if self.state_path:
                save_state(self.tier_state, self.state_path)
        elif self.state_path:
            # Persist below-count progress so restarts do not cheat the dwell.
            save_state(self.tier_state, self.state_path)

        return self._tier_template(new_tier, raw)
