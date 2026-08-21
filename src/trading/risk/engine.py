import logging
from datetime import timedelta

from trading.risk.models import (
    AccountState,
    ApprovedExit,
    ApprovedOrder,
    ExitDecision,
    ExitSignal,
    RiskConfig,
    RiskDecision,
    Signal,
    _ISSUER,
)

logger = logging.getLogger("trading.risk")


class RiskEngine:
    """Pure, deterministic gate between strategy signals and order execution.

    No signal is ever approved on probabilistic judgment; every check here is a
    hard rule. See CLAUDE-facing spec rules a-l for the source of each check.
    """

    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()

    def evaluate(self, signal: Signal, account: AccountState) -> RiskDecision:
        cfg = self.config

        if account.kill_switch:
            return self._reject(signal, "manual kill switch is active")

        if account.peak_equity > 0:
            drawdown = (account.peak_equity - account.equity) / account.peak_equity
            if drawdown >= cfg.max_drawdown:
                return self._reject(
                    signal, f"max drawdown kill switch: {drawdown:.1%} >= {cfg.max_drawdown:.1%}"
                )

        if account.daily_pnl_pct <= -cfg.daily_loss_limit:
            return self._reject(signal, f"daily loss circuit breaker: {account.daily_pnl_pct:.1%}")

        losses = account.consecutive_losses.get(signal.asset_class, 0)
        last_loss = account.last_loss_at.get(signal.asset_class)
        if losses >= cfg.consecutive_loss_limit and last_loss is not None:
            cooldown_ends = last_loss + timedelta(hours=cfg.cooldown_hours)
            if signal.timestamp < cooldown_ends:
                return self._reject(
                    signal,
                    f"{signal.asset_class} paused: {losses} consecutive losses, "
                    f"cooldown until {cooldown_ends.isoformat()}",
                )

        if signal.suggested_stop is None:
            return self._reject(signal, "signal missing required stop-loss")

        risk_per_unit = abs(signal.entry_price - signal.suggested_stop)
        if risk_per_unit == 0:
            return self._reject(signal, "stop-loss equals entry price")

        if signal.suggested_target is None:
            return self._reject(signal, "signal missing target; cannot verify reward:risk")

        reward_per_unit = abs(signal.suggested_target - signal.entry_price)
        reward_risk = reward_per_unit / risk_per_unit
        if reward_risk < cfg.min_reward_risk:
            return self._reject(
                signal, f"reward:risk {reward_risk:.2f} below minimum {cfg.min_reward_risk:.2f}"
            )

        same_class_positions = [p for p in account.open_positions if p.asset_class == signal.asset_class]
        if len(same_class_positions) >= cfg.max_positions_per_asset_class:
            return self._reject(
                signal,
                f"{signal.asset_class} at max concurrent positions ({cfg.max_positions_per_asset_class})",
            )

        correlated_risk = sum(
            p.risk_pct
            for p in account.open_positions
            if account.correlations.get(frozenset({p.asset, signal.asset}), 0.0) > cfg.correlation_threshold
        )
        if correlated_risk > 0:
            return self._reject(
                signal,
                f"correlation guard: existing correlated position(s) already use {correlated_risk:.1%} "
                f"risk; combined cluster would exceed the per-trade risk cap",
            )

        effective_risk_pct = cfg.risk_pct
        if account.weekly_pnl_pct <= -cfg.weekly_loss_limit:
            effective_risk_pct *= cfg.weekly_loss_reduction

        pre_event = (
            account.minutes_to_next_major_event is not None
            and account.minutes_to_next_major_event <= cfg.pre_event_hours * 60
        )
        if pre_event:
            effective_risk_pct *= cfg.pre_event_reduction

        if signal.garch_vol_scale is not None:
            effective_risk_pct *= signal.garch_vol_scale

        heat_cap = cfg.max_heat_high_vol if account.high_volatility else cfg.max_heat
        current_heat = sum(p.risk_pct for p in account.open_positions)
        if current_heat + effective_risk_pct > heat_cap:
            return self._reject(
                signal,
                f"portfolio heat {current_heat + effective_risk_pct:.1%} would exceed cap {heat_cap:.1%}",
            )

        position_size = (account.equity * effective_risk_pct) / risk_per_unit
        approved_order = ApprovedOrder(
            asset=signal.asset,
            asset_class=signal.asset_class,
            side=signal.side,
            entry_price=signal.entry_price,
            stop_price=signal.suggested_stop,
            target_price=signal.suggested_target,
            position_size=position_size,
            risk_pct=effective_risk_pct,
            issuer=_ISSUER,
        )
        return self._approve(signal, approved_order)

    def evaluate_exit_signal(self, signal: ExitSignal, account: AccountState) -> ExitDecision:
        """Gate for proposals to close an existing position early (e.g. from an
        LLM anomaly/sentiment component). Deliberately does NOT check kill
        switch, drawdown, daily loss, or any other halt: those rules exist to
        block new risk-taking, and blocking a de-risking action with a
        risk-halting rule would be self-defeating. The only questions here are
        "does this position exist" and "is this proposal credible enough to
        act on" -- never "should risk be reduced right now", which is always yes.
        """
        cfg = self.config

        has_position = any(p.asset == signal.asset for p in account.open_positions)
        if not has_position:
            return self._reject_exit(signal, f"no open position in {signal.asset} to exit")

        if signal.confidence < cfg.min_exit_confidence:
            return self._reject_exit(
                signal,
                f"confidence {signal.confidence:.2f} below minimum {cfg.min_exit_confidence:.2f}",
            )

        approved_exit = ApprovedExit(
            asset=signal.asset,
            asset_class=signal.asset_class,
            reason=signal.reason,
            issuer=_ISSUER,
        )
        return self._approve_exit(signal, approved_exit)

    def _reject_exit(self, signal: ExitSignal, reason: str) -> ExitDecision:
        logger.warning("exit_decision rejected: %s | signal=%r", reason, signal)
        return ExitDecision(approved=False, reason=reason, signal=signal, approved_exit=None)

    def _approve_exit(self, signal: ExitSignal, approved_exit: ApprovedExit) -> ExitDecision:
        logger.info("exit_decision approved | signal=%r | exit=%r", signal, approved_exit)
        return ExitDecision(approved=True, reason="approved", signal=signal, approved_exit=approved_exit)

    def _reject(self, signal: Signal, reason: str) -> RiskDecision:
        logger.warning("risk_decision rejected: %s | signal=%r", reason, signal)
        return RiskDecision(approved=False, reason=reason, signal=signal, approved_order=None)

    def _approve(self, signal: Signal, approved_order: ApprovedOrder) -> RiskDecision:
        logger.info("risk_decision approved | signal=%r | order=%r", signal, approved_order)
        return RiskDecision(approved=True, reason="approved", signal=signal, approved_order=approved_order)
