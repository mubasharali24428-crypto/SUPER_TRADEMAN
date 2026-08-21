"""Portfolio-Level Governor: aggregate message-rate limiting and systemic risk halting across per-symbol OMS Actors."""

import time
from typing import Dict, Optional

from trading.observability.logger import get_logger
from trading.synthetic.oms_engine import OMSActorEngine, OMSEventTier, OrderStatus

__all__ = ["PortfolioGovernor"]

logger = get_logger("trading.synthetic.portfolio_governor")


class PortfolioGovernor:
    """Coordinates every registered per-symbol OMSActorEngine: tracks aggregate outbound
    message rate, throttles Tier 2 (aggressive) events near the global cap, aggregates
    real-time portfolio risk exposure, and trips a global HALT (cancelling resting orders
    across every symbol) on a portfolio-level risk breach."""

    def __init__(
        self,
        max_messages_per_sec: float = 100.0,
        throttle_threshold_pct: float = 0.80,
        max_portfolio_risk_pct: float = 0.15,
        halt_cooldown_sec: float = 60.0,
    ):
        self.max_messages_per_sec = max_messages_per_sec
        self.throttle_threshold_pct = throttle_threshold_pct
        self.max_portfolio_risk_pct = max_portfolio_risk_pct
        self.halt_cooldown_sec = halt_cooldown_sec

        self.actors: Dict[str, OMSActorEngine] = {}
        self.actor_states: Dict[str, str] = {}
        self.message_counts: Dict[str, int] = {}
        self.window_start_ts: float = 0.0
        self.symbol_risk_pct: Dict[str, float] = {}
        self.is_globally_halted: bool = False
        self.halt_triggered_ts: Optional[float] = None

    def register_actor(self, symbol: str, actor: OMSActorEngine) -> None:
        self.actors[symbol] = actor
        self.actor_states[symbol] = "ACTIVE"
        self.message_counts[symbol] = 0
        self.symbol_risk_pct[symbol] = 0.0

    def record_message(self, symbol: str, now_ts: Optional[float] = None) -> None:
        """Records one outbound message for the symbol within a rolling 1-second window."""
        now = now_ts if now_ts is not None else time.time()
        if now - self.window_start_ts >= 1.0:
            self.window_start_ts = now
            self.message_counts = {s: 0 for s in self.actors}
        self.message_counts[symbol] = self.message_counts.get(symbol, 0) + 1

    @property
    def aggregate_message_rate(self) -> int:
        return sum(self.message_counts.values())

    @property
    def is_throttled(self) -> bool:
        """True once aggregate throughput crosses throttle_threshold_pct of the hard cap."""
        return self.aggregate_message_rate >= (self.max_messages_per_sec * self.throttle_threshold_pct)

    def allow_event(self, tier: OMSEventTier) -> bool:
        """Governs whether an event of the given tier may dispatch right now. Tier 0 always bypasses."""
        if tier == OMSEventTier.TIER_0_SAFETY:
            return True
        if self.is_globally_halted:
            return False
        if tier == OMSEventTier.TIER_2_EXECUTION and self.is_throttled:
            return False
        return True

    @property
    def portfolio_risk_exposure_pct(self) -> float:
        return sum(self.symbol_risk_pct.values())

    def update_portfolio_risk(self, symbol: str, risk_pct: float) -> bool:
        """Updates one symbol's risk contribution and re-aggregates. Returns True if this
        update breached the portfolio-level limit and triggered a global halt."""
        self.symbol_risk_pct[symbol] = risk_pct
        if self.portfolio_risk_exposure_pct >= self.max_portfolio_risk_pct:
            self.trigger_global_halt()
            return True
        return False

    def trigger_global_halt(self, now_ts: Optional[float] = None) -> None:
        """Halts every registered Actor, transitions each to DEFENSIVE, and cancels its resting orders."""
        now = now_ts if now_ts is not None else time.time()
        self.is_globally_halted = True
        self.halt_triggered_ts = now
        for symbol, actor in self.actors.items():
            actor.is_halted = True
            self.actor_states[symbol] = "DEFENSIVE"
            for order_id, order in list(actor.orders.items()):
                if order["status"] in (OrderStatus.ACTIVE, OrderStatus.NEW):
                    actor.request_cancel(order_id)
        logger.critical(
            f"[GOVERNOR_GLOBAL_HALT] Portfolio risk {self.portfolio_risk_exposure_pct:.2%} "
            f"breached {self.max_portfolio_risk_pct:.2%}. Halted {len(self.actors)} symbols."
        )

    def can_resume(self, now_ts: Optional[float] = None) -> bool:
        if not self.is_globally_halted:
            return True
        now = now_ts if now_ts is not None else time.time()
        return (now - self.halt_triggered_ts) >= self.halt_cooldown_sec

    def reset(self, now_ts: Optional[float] = None) -> bool:
        """Resumes every Actor once the cooldown has elapsed. Returns False (no-op) if still cooling down."""
        if not self.can_resume(now_ts=now_ts):
            return False
        self.is_globally_halted = False
        self.halt_triggered_ts = None
        self.symbol_risk_pct = {s: 0.0 for s in self.actors}
        for symbol, actor in self.actors.items():
            actor.is_halted = False
            actor.circuit_breaker_tripped = False
            self.actor_states[symbol] = "ACTIVE"
        return True
