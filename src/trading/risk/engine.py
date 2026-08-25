import logging
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from weakref import WeakKeyDictionary

from trading.core.money import decimal_from_float, quantize_to_step
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


class _QuantizedRiskDecision(RiskDecision):
    """SQUAD DM-1 wave-1 dual-run carrier (F-0342 Decimal program).

    Adds ``size_decimal_candidate`` WITHOUT editing models.py (outside this
    squad's ownership): ``models.RiskDecision`` stays frozen and intact, and
    because dataclass ``__eq__``/``repr`` see only the inherited fields,
    downstream consumers comparing or printing decisions are unaffected.
    Constructed only when instrument quantization is active; every other
    approval returns the plain legacy ``RiskDecision``.

    R2 / VB-002 RESOLUTION: the carrier is RETIRED from the return path.
    CPython's builtin ``type()`` reads the internal type field and cannot be
    overridden from Python (an instance ``__class__`` property changes
    attribute access only -- and would corrupt pickle, which trusts
    ``obj.__class__``). Exact-type identity for every approval is therefore
    restored BY CONSTRUCTION: ``RiskEngine.evaluate`` now returns the plain
    legacy ``RiskDecision`` on ALL paths, and the Decimal candidate travels
    OUT-OF-BAND via ``RiskEngine.get_size_candidate(order)`` (the other
    remediation this finding's fix hint offers). The class remains importable
    for backward compatibility; nothing constructs it anymore.
    """

    __slots__ = ("size_decimal_candidate",)

    def __init__(
        self,
        approved: bool,
        reason: str,
        signal: Signal,
        approved_order: ApprovedOrder | None,
        size_decimal_candidate: Decimal | None,
    ):
        super().__init__(
            approved=approved,
            reason=reason,
            signal=signal,
            approved_order=approved_order,
        )
        # Parent is a frozen dataclass; bypass its __setattr__ for the slot.
        object.__setattr__(self, "size_decimal_candidate", size_decimal_candidate)


class RiskEngine:
    """Pure, deterministic gate between strategy signals and order execution.

    No signal is ever approved on probabilistic judgment; every check here is a
    hard rule. See CLAUDE-facing spec rules a-l for the source of each check.
    """

    def __init__(self, config: RiskConfig | None = None, instruments: Mapping[str, str] | None = None):
        self.config = config or RiskConfig()
        # SQUAD DM-1 wave-1 (F-0342 dual-run): optional per-instrument step-size
        # map, e.g. {"BTC/USDT": "0.001"}. None / empty / asset-miss all mean
        # "quantization inactive" -> behavior identical to pre-wave legacy.
        self.instruments = dict(instruments) if instruments else None
        # R2 / VB-002: decisions stay plain RiskDecision on every path; the
        # quantized candidate travels out-of-band, keyed by the ApprovedOrder
        # identity. WeakKeyDictionary -> entries die with their orders.
        self._size_candidates: WeakKeyDictionary[ApprovedOrder, Decimal | None] = WeakKeyDictionary()

    def get_size_candidate(self, order: ApprovedOrder) -> Decimal | None:
        """Out-of-band Decimal size candidate for a quantized approval.

        R2 / VB-002: returns the quantized ``size_decimal_candidate`` recorded
        when this engine approved ``order``; None for unknown orders and for
        approvals where no step was configured (quantization inactive). This
        keeps every decision an exact-type ``RiskDecision`` while preserving
        the dual-run's Decimal visibility.
        """
        return self._size_candidates.get(order)

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

        # --- SQUAD DM-1 wave-1: Decimal dual-run at the single size point ----
        # Legacy float size above stays EXACTLY as before and remains the value
        # consumed downstream. When an instrument step is configured, a Decimal
        # candidate is computed in parallel purely to make divergence visible.
        size_decimal_candidate = None
        size_decimal_exact_grid = None  # R2 / VB-035: independently recomputed
        step_str = self.instruments.get(signal.asset) if self.instruments else None
        if step_str:
            try:
                size_decimal_candidate = quantize_to_step(position_size, step_str)
            except (ValueError, InvalidOperation) as exc:
                logger.warning(
                    "SIZE_QUANTIZE_ERROR: asset=%s size=%r step=%r err=%s -- "
                    "returning legacy unquantized decision",
                    signal.asset, position_size, step_str, exc,
                )
                size_decimal_candidate = None
            else:
                try:
                    # R2 / VB-035: the divergence that matters is float vs
                    # Decimal ARITHMETIC in the sizing formula itself (e.g.
                    # catastrophic cancellation in entry-stop for tight
                    # stops), not the trivially bounded float->grid rounding.
                    # Recompute equity*risk/rpu from pure Decimal inputs and
                    # grid it with the SAME floor convention.
                    risk_per_unit_exact = decimal_from_float(signal.entry_price) - decimal_from_float(
                        signal.suggested_stop
                    )
                    if signal.side.value == "short":
                        risk_per_unit_exact = -risk_per_unit_exact
                    risk_per_unit_exact = abs(risk_per_unit_exact)
                    if risk_per_unit_exact.is_finite() and risk_per_unit_exact > 0:
                        size_exact = (
                            decimal_from_float(account.equity)
                            * decimal_from_float(effective_risk_pct)
                            / risk_per_unit_exact
                        )
                        size_decimal_exact_grid = quantize_to_step(size_exact, step_str)
                except (ValueError, InvalidOperation):
                    logger.warning(
                        "SIZE_DELTA_EXACT_UNAVAILABLE: asset=%s -- exact "
                        "Decimal recompute failed; only the quantization-path "
                        "check runs this cycle",
                        signal.asset,
                    )

                # Invariant A (wave-1): quantized candidate must sit within one
                # step of the legacy float size (floor convention). Beyond that
                # the two paths have genuinely diverged -> SIZE_DELTA warning.
                step_dec = Decimal(str(step_str))
                tolerance = step_dec  # |candidate - legacy| <= one full step
                delta = float(size_decimal_candidate) - position_size
                if abs(Decimal(str(delta))) > tolerance:
                    logger.warning(
                        "SIZE_DELTA: asset=%s legacy_float=%.12f decimal_candidate=%s "
                        "delta=%.12f tolerance(one_step)=%s",
                        signal.asset,
                        position_size,
                        size_decimal_candidate,
                        delta,
                        tolerance,
                    )
                # Invariant B (R2 / VB-035): the QUANTIZED order quantity must
                # equal an INDEPENDENT exact-Decimal recompute of the same
                # formula gridded onto the same exchange step. Any mismatch is
                # formula-level float-vs-Decimal divergence.
                if size_decimal_exact_grid is not None and size_decimal_exact_grid != size_decimal_candidate:
                    logger.warning(
                        "SIZE_DELTA_EXACT: asset=%s quantized_qty=%s exact_recomputed_size=%s "
                        "(both Decimals; legacy float pipeline produced %.12f)",
                        signal.asset,
                        size_decimal_candidate,
                        size_decimal_exact_grid,
                        position_size,
                    )
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
            # R2 / VB-002: candidate rides out-of-band; the decision itself is
            # the plain legacy RiskDecision (exact-type identity preserved).
            self._size_candidates[approved_order] = size_decimal_candidate
            return self._approve(signal, approved_order)

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
