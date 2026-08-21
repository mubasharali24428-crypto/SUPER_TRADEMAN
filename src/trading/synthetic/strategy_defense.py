"""Strategy Defense State Machine with Advanced Sniper Constraints, Ammunition Management, and Microstructure Exit."""

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from trading.observability.logger import get_logger
from trading.risk.models import ApprovedExit, Position, Side, _ISSUER
from trading.synthetic.ecology import LiquidityEvent
from trading.synthetic.regime_validator import (
    AdaptiveEWMVTracker,
    MicrostructureMetrics,
    MicrostructureRegime,
)
from trading.synthetic.stale_protection import RestingOrder, StaleQuoteProtection

__all__ = [
    "StrategyDefenseState",
    "StrategyDefensePosture",
    "TradeSignal",
    "TakerOrder",
    "PassiveExitOrder",
    "SniperMode",
    "MicroAlphaEngine",
    "EWMVTracker",
    "MicrostructureStrategyDefender",
]

logger = get_logger("trading.synthetic.strategy_defense")


class StrategyDefenseState:
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    DEFENSIVE = "DEFENSIVE"
    HALTED = "HALTED"
    RECOVERING = "RECOVERING"


@dataclass
class TradeSignal:
    direction: str  # "BUY" or "SELL"
    edge_zscore: float
    mean_reversion_direction: str
    confidence: float
    is_valid: bool = True
    order_flow_toxicity_pct: float = 0.96  # Percentile of aggressive sell volume
    sustained_ticks: int = 20  # Temporal persistence count


@dataclass
class TakerOrder:
    order_id: str
    side: str
    qty: float
    tif: str = "IOC"
    max_hold_ms: int = 5000
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000.0)


@dataclass
class PassiveExitOrder:
    order_id: str
    side: str  # "SELL" for closing LONG
    price: float
    qty: float
    tag: str = "Tier_0_Ephemeral"
    is_active: bool = True
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000.0)


@dataclass
class StrategyDefensePosture:
    state: str
    size_multiplier: float
    spread_multiplier: float
    block_new_entries: bool
    tightened_stop_price: Optional[float]
    trigger_emergency_flatten: bool
    passive_quoting_enabled: bool = True
    sniper_mode_armed: bool = False
    current_volatility: float = 0.0


class SniperMode:
    """Active ONLY during RECOVERING state with strict ammunition management and microstructure exit."""

    def __init__(
        self,
        max_size_multiplier: float = 0.10,
        inventory_quota_max: float = 0.50,
        required_persistence_ticks: int = 10,
        min_toxicity_pct: float = 0.95,
        retracement_bounce_pct: float = 0.015,
        base_cooldown_sec: float = 5.0,
    ):
        self.max_size_multiplier = max_size_multiplier
        self.inventory_quota_max = inventory_quota_max
        self.required_persistence_ticks = required_persistence_ticks
        self.min_toxicity_pct = min_toxicity_pct
        self.retracement_bounce_pct = retracement_bounce_pct
        self.base_cooldown_sec = base_cooldown_sec

        self.is_armed = False
        self.accumulated_inventory_pct = 0.0
        self.last_entry_price: Optional[float] = None
        self.last_trade_ts: float = 0.0
        self.active_passive_exit: Optional[PassiveExitOrder] = None

    def evaluate_opportunity(
        self,
        signal: TradeSignal,
        stress_score: float,
        current_price: float = 0.0,
        now_ts: Optional[float] = None,
        vol_ratio: float = 1.0,
    ) -> bool:
        """Evaluates entry against temporal persistence, order flow toxicity, and ammunition constraints (Task 2.1 & 2.2)."""
        now = now_ts if now_ts is not None else time.time()

        if not self.is_armed or not signal.is_valid:
            return False

        # 1. Base Calm Condition
        if stress_score >= 0.10 or signal.edge_zscore <= 3.0 or signal.direction != signal.mean_reversion_direction:
            return False

        # 2. Temporal Persistence Constraint (Task 2.1)
        if signal.sustained_ticks < self.required_persistence_ticks:
            logger.debug(f"[SNIPER_VETO_PERSISTENCE] Sustained ticks {signal.sustained_ticks} < {self.required_persistence_ticks}.")
            return False

        # 3. Order Flow Toxicity Constraint (Task 2.1)
        if signal.order_flow_toxicity_pct < self.min_toxicity_pct:
            logger.debug(f"[SNIPER_VETO_TOXICITY] Toxicity percentile {signal.order_flow_toxicity_pct:.2f} < {self.min_toxicity_pct:.2f}.")
            return False

        # 4. Inventory Quota Constraint (Task 2.2)
        if (self.accumulated_inventory_pct + self.max_size_multiplier) > self.inventory_quota_max:
            logger.debug(f"[SNIPER_VETO_INVENTORY_QUOTA] Accumulated {self.accumulated_inventory_pct:.2f} would exceed quota {self.inventory_quota_max:.2f}.")
            return False

        # 5. Retracement Reload Hysteresis (Task 2.2)
        if self.last_entry_price is not None and current_price > 0:
            # Must bounce by retracement_bounce_pct before re-arming
            bounce = (current_price - self.last_entry_price) / max(self.last_entry_price, 1e-6)
            if bounce < self.retracement_bounce_pct:
                logger.debug(f"[SNIPER_VETO_RELOAD] Bounce {bounce*100:.2f}% < required {self.retracement_bounce_pct*100:.2f}%.")
                return False

        # 6. Volatility-Scaled Cooldown (Task 2.2): stretches during high session vol,
        # shrinks during low session vol. vol_ratio=1.0 (default) preserves the flat cooldown.
        effective_cooldown_sec = self.base_cooldown_sec * max(vol_ratio, 0.1)
        if (now - self.last_trade_ts) < effective_cooldown_sec:
            return False

        return True

    def execute_sniper_trade(
        self,
        signal: TradeSignal,
        base_size: float,
        current_price: float = 0.0,
        now_ts: Optional[float] = None,
    ) -> TakerOrder:
        now = now_ts if now_ts is not None else time.time()
        sniper_size = round(base_size * self.max_size_multiplier, 4)
        order = TakerOrder(
            order_id=f"sniper_{int(now*1000.0)}",
            side=signal.direction,
            qty=sniper_size,
            tif="IOC",
            max_hold_ms=5000,
            timestamp_ms=now * 1000.0,
        )
        self.accumulated_inventory_pct += self.max_size_multiplier
        if current_price > 0:
            self.last_entry_price = current_price
        self.last_trade_ts = now
        logger.info(f"[SNIPER_TRADE_EXECUTED] Fired {order.side} {order.qty} IOC (TotalInv={self.accumulated_inventory_pct:.2f}).")
        return order

    def post_passive_exit(self, price: float, qty: float, side: str = "SELL") -> PassiveExitOrder:
        """Posts passive exit tagged as Tier_0_Ephemeral (Task 2.3)."""
        exit_order = PassiveExitOrder(
            order_id=f"exit_ephemeral_{int(time.time()*1000.0)}",
            side=side,
            price=price,
            qty=qty,
            tag="Tier_0_Ephemeral",
            is_active=True,
        )
        self.active_passive_exit = exit_order
        logger.info(f"[PASSIVE_EXIT_POSTED] Placed {exit_order.tag} {exit_order.side} {exit_order.qty} at {exit_order.price:.2f}.")
        return exit_order

    def evaluate_microstructure_exit_drift(self, current_micro_price: float, sigma: float) -> bool:
        """Asymmetric stale cancel: kills passive exit if micro-price drifts against it by > 0.25 * sigma (Task 2.3)."""
        if self.active_passive_exit is None or not self.active_passive_exit.is_active:
            return False

        order = self.active_passive_exit
        # For SELL exit, drift against it means micro-price drops below order.price - 0.25 * sigma
        drift_against = (order.price - current_micro_price) if order.side == "SELL" else (current_micro_price - order.price)
        drift_threshold = 0.25 * max(sigma, 1e-4)

        if drift_against > drift_threshold:
            order.is_active = False
            self.active_passive_exit = None
            logger.info(f"[TIER0_EPHEMERAL_CANCEL] Passive exit canceled due to micro-drift ({drift_against:.3f} > {drift_threshold:.3f}).")
            return True

        return False

    def on_liquidity_event_killswitch(self, event: LiquidityEvent) -> bool:
        """Instantly kills passive exit if ecology broadcasts a severe quote withdrawal or predator sweep (Task 2.3)."""
        if self.active_passive_exit is None or not self.active_passive_exit.is_active:
            return False

        if event.depletion_ratio >= 0.40 or event.event_type in ("QUOTE_WITHDRAWAL", "AGGRESSIVE_SWEEP"):
            self.active_passive_exit.is_active = False
            self.active_passive_exit = None
            logger.info(f"[PREDATOR_WAKE_KILLSWITCH] Ecology depletion {event.depletion_ratio:.2f}. Killed Tier_0_Ephemeral passive exit.")
            return True

        return False


class MicroAlphaEngine:
    """Micro-horizon alpha engine capturing extreme post-crash price dislocations."""

    def __init__(self, signal_horizon_ms: int = 30000):
        self.signal_horizon_ms = signal_horizon_ms
        self.pre_crash_micro_price: Optional[float] = None

    def on_halt_triggered(self, micro_price_at_halt: float) -> None:
        self.pre_crash_micro_price = micro_price_at_halt
        logger.info(f"[ALPHA_ENGINE_CRASH_ANCHOR] Pre-crash micro-price anchored at {micro_price_at_halt:.2f}.")

    def calculate_signal(
        self,
        current_micro_price: float,
        stress_score: float,
        sigma: float,
        sustained_ticks: int = 20,
        toxicity_pct: float = 0.96,
    ) -> TradeSignal:
        if self.pre_crash_micro_price is None or self.pre_crash_micro_price <= 0:
            return TradeSignal("NONE", 0.0, "NONE", 0.0, is_valid=False)

        dislocation = (current_micro_price - self.pre_crash_micro_price) / self.pre_crash_micro_price
        effective_sigma = max(sigma, 1e-4)
        z_score = abs(dislocation) / effective_sigma

        # Mean-reversion condition: price down > 3% from pre-crash and stress has dropped to calm
        if dislocation < -0.03 and stress_score < 0.10:
            return TradeSignal(
                direction="BUY",
                edge_zscore=round(z_score, 2),
                mean_reversion_direction="BUY",
                confidence=round(1.0 - stress_score, 3),
                is_valid=True,
                order_flow_toxicity_pct=toxicity_pct,
                sustained_ticks=sustained_ticks,
            )
        elif dislocation > 0.03 and stress_score < 0.10:
            return TradeSignal(
                direction="SELL",
                edge_zscore=round(z_score, 2),
                mean_reversion_direction="SELL",
                confidence=round(1.0 - stress_score, 3),
                is_valid=True,
                order_flow_toxicity_pct=toxicity_pct,
                sustained_ticks=sustained_ticks,
            )

        return TradeSignal("NONE", 0.0, "NONE", 0.0, is_valid=False)


EWMVTracker = AdaptiveEWMVTracker


class MicrostructureStrategyDefender:
    """Manages 5-state strategy risk adaptation, sniper mode execution, and stale quote protection."""

    def __init__(
        self,
        recovery_cooldown_sec: float = 30.0,
        halted_cooldown_sec: float = 300.0,
        healing_stability_sec: float = 60.0,
        ewmv_alpha: float = 0.05,
    ):
        self.current_state = StrategyDefenseState.NORMAL
        self.recovery_cooldown_sec = recovery_cooldown_sec
        self.halted_cooldown_sec = halted_cooldown_sec
        self.healing_stability_sec = healing_stability_sec
        
        self.last_elevated_state_ts: float = 0.0
        self.halted_entry_ts: float = 0.0
        self.calm_streak_start_ts: Optional[float] = None
        self.recovering_entry_ts: float = 0.0
        
        self.volatility_tracker = AdaptiveEWMVTracker(lambda_base=ewmv_alpha)
        self.stale_protection = StaleQuoteProtection(sigma_multiplier=1.5, min_distance_bps=5.0)
        self.sniper_mode = SniperMode(max_size_multiplier=0.10)
        self.alpha_engine = MicroAlphaEngine()

    def evaluate_stale_quotes(self, current_micro_price: float) -> List[RestingOrder]:
        """Evaluates and pulls drifted resting child orders before matching cycle."""
        if self.current_state == StrategyDefenseState.HALTED:
            return []

        sigma = self.volatility_tracker.sigma
        return self.stale_protection.evaluate(current_micro_price, sigma)

    def evaluate_posture(
        self,
        metrics: MicrostructureMetrics,
        micro_price: float,
        current_drawdown_pct: float = 0.0,
        position: Optional[Position] = None,
        now_ts: Optional[float] = None,
    ) -> StrategyDefensePosture:
        """Evaluates tactical posture, EWMV volatility stop adjustment, and healing transitions."""
        now = now_ts if now_ts is not None else time.time()
        sigma = self.volatility_tracker.update(micro_price)

        # 1. Catastrophic drawdown check -> Enters or stays in HALTED
        if current_drawdown_pct >= 0.08:
            if self.current_state != StrategyDefenseState.HALTED:
                self.current_state = StrategyDefenseState.HALTED
                self.halted_entry_ts = now
                self.calm_streak_start_ts = None
                self.sniper_mode.is_armed = False
                self.alpha_engine.on_halt_triggered(micro_price)
                logger.error(f"[DEFENSE_HALTED] Drawdown {current_drawdown_pct*100:.2f}% >= 8.0%. Triggering emergency flatten.")
            
            return StrategyDefensePosture(
                state=StrategyDefenseState.HALTED,
                size_multiplier=0.0,
                spread_multiplier=2.0,
                block_new_entries=True,
                tightened_stop_price=None,
                trigger_emergency_flatten=True,
                passive_quoting_enabled=False,
                sniper_mode_armed=False,
                current_volatility=sigma,
            )

        # 2. Healing protocol from HALTED
        if self.current_state == StrategyDefenseState.HALTED:
            time_in_halt = now - self.halted_entry_ts
            if time_in_halt >= self.halted_cooldown_sec:
                if metrics.stress_score < 0.20:
                    if self.calm_streak_start_ts is None:
                        self.calm_streak_start_ts = now
                    elif (now - self.calm_streak_start_ts) >= self.healing_stability_sec:
                        # Graduated to RECOVERING
                        self.current_state = StrategyDefenseState.RECOVERING
                        self.recovering_entry_ts = now
                        self.calm_streak_start_ts = None
                        self.sniper_mode.is_armed = True
                        logger.info("[DEFENSE_HEALING] Market verified calm. Transitioning HALTED -> RECOVERING (Sniper armed).")
                else:
                    self.calm_streak_start_ts = None

            if self.current_state == StrategyDefenseState.HALTED:
                return StrategyDefensePosture(
                    state=StrategyDefenseState.HALTED,
                    size_multiplier=0.0,
                    spread_multiplier=2.0,
                    block_new_entries=True,
                    tightened_stop_price=None,
                    trigger_emergency_flatten=False,
                    passive_quoting_enabled=False,
                    sniper_mode_armed=False,
                    current_volatility=sigma,
                )

        # 3. Handling RECOVERING state progression
        if self.current_state == StrategyDefenseState.RECOVERING:
            if metrics.regime_shift_detected or metrics.stress_score >= 0.40:
                # Immediate relapse to HALTED
                self.current_state = StrategyDefenseState.HALTED
                self.halted_entry_ts = now
                self.calm_streak_start_ts = None
                self.sniper_mode.is_armed = False
                logger.warning("[DEFENSE_RELAPSE] Stress spike during RECOVERING. Relapsing to HALTED.")
                return StrategyDefensePosture(
                    state=StrategyDefenseState.HALTED,
                    size_multiplier=0.0,
                    spread_multiplier=2.0,
                    block_new_entries=True,
                    tightened_stop_price=None,
                    trigger_emergency_flatten=True,
                    passive_quoting_enabled=False,
                    sniper_mode_armed=False,
                    current_volatility=sigma,
                )
            elif (now - self.recovering_entry_ts) >= self.halted_cooldown_sec:
                self.current_state = StrategyDefenseState.CAUTION
                self.last_elevated_state_ts = now
                self.sniper_mode.is_armed = False
                logger.info("[DEFENSE_GRADUATED] Successfully stabilized in RECOVERING -> Graduating to CAUTION (0.50x).")

        # 4. Standard State Transitions (NORMAL <-> CAUTION <-> DEFENSIVE)
        if self.current_state not in (StrategyDefenseState.HALTED, StrategyDefenseState.RECOVERING):
            if metrics.regime in (MicrostructureRegime.ADVERSARIAL_SHOCK, MicrostructureRegime.SELL_STRESS, MicrostructureRegime.BUY_STRESS) or metrics.stress_score >= 0.70:
                self.current_state = StrategyDefenseState.DEFENSIVE
                self.last_elevated_state_ts = now
            elif metrics.regime == MicrostructureRegime.FRAGILE_BALANCED or metrics.stress_score >= 0.35:
                if self.current_state == StrategyDefenseState.DEFENSIVE:
                    if (now - self.last_elevated_state_ts) >= self.recovery_cooldown_sec:
                        self.current_state = StrategyDefenseState.CAUTION
                else:
                    self.current_state = StrategyDefenseState.CAUTION
                    self.last_elevated_state_ts = now
            else:  # CALM
                if self.current_state in (StrategyDefenseState.DEFENSIVE, StrategyDefenseState.CAUTION):
                    if (now - self.last_elevated_state_ts) >= self.recovery_cooldown_sec:
                        self.current_state = StrategyDefenseState.NORMAL
                else:
                    self.current_state = StrategyDefenseState.NORMAL

        # 5. Output Posture configuration per state
        if self.current_state == StrategyDefenseState.DEFENSIVE:
            stop_distance = max(2.0 * sigma, 0.001 * micro_price)
            tightened_stop = None
            if position is not None:
                if position.side == Side.LONG:
                    tightened_stop = round(micro_price - stop_distance, 2)
                else:
                    tightened_stop = round(micro_price + stop_distance, 2)

            return StrategyDefensePosture(
                state=StrategyDefenseState.DEFENSIVE,
                size_multiplier=0.0,
                spread_multiplier=2.0,
                block_new_entries=True,
                tightened_stop_price=tightened_stop,
                trigger_emergency_flatten=False,
                passive_quoting_enabled=False,
                sniper_mode_armed=False,
                current_volatility=sigma,
            )

        elif self.current_state == StrategyDefenseState.RECOVERING:
            return StrategyDefensePosture(
                state=StrategyDefenseState.RECOVERING,
                size_multiplier=0.10,
                spread_multiplier=1.8,
                block_new_entries=False,
                tightened_stop_price=None,
                trigger_emergency_flatten=False,
                passive_quoting_enabled=False,
                sniper_mode_armed=True,
                current_volatility=sigma,
            )

        elif self.current_state == StrategyDefenseState.CAUTION:
            return StrategyDefensePosture(
                state=StrategyDefenseState.CAUTION,
                size_multiplier=0.50,
                spread_multiplier=1.5,
                block_new_entries=False,
                tightened_stop_price=None,
                trigger_emergency_flatten=False,
                passive_quoting_enabled=True,
                sniper_mode_armed=False,
                current_volatility=sigma,
            )

        else:  # NORMAL
            return StrategyDefensePosture(
                state=StrategyDefenseState.NORMAL,
                size_multiplier=1.0,
                spread_multiplier=1.0,
                block_new_entries=False,
                tightened_stop_price=None,
                trigger_emergency_flatten=False,
                passive_quoting_enabled=True,
                sniper_mode_armed=False,
                current_volatility=sigma,
            )
