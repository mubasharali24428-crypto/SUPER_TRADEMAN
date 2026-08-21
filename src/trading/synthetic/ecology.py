"""Synthetic Market Ecology with Reactive Liquidity Agents and Predator Cascade Dynamics."""

import collections
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

from trading.observability.logger import get_logger

__all__ = [
    "LiquidityEvent",
    "LiquidityBaselineTracker",
    "BaseEcologyAgent",
    "ReactiveMarketMakerAgent",
    "ToxicFlowPredatorAgent",
    "SyntheticAgentRegistry",
]

logger = get_logger("trading.synthetic.ecology")


@dataclass
class LiquidityEvent:
    event_type: str  # "QUOTE_WITHDRAWAL", "AGGRESSIVE_SWEEP", "BOOK_RELOAD"
    withdrawn_qty: float
    current_depth: float
    baseline_depth: float
    depletion_ratio: float
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000.0)


class LiquidityBaselineTracker:
    """Tracks rolling median depth baseline robust to short-term spikes."""

    def __init__(self, window_seconds: int = 300):
        self.window_ms = window_seconds * 1000.0
        self.depth_samples: Deque[Dict[str, float]] = collections.deque(maxlen=3000)

    def record_depth(self, timestamp_ms: float, bid_qty: float, ask_qty: float) -> None:
        self.depth_samples.append({
            "ts": timestamp_ms,
            "depth": bid_qty + ask_qty,
        })

    def get_baseline_depth(self, current_ts: float) -> float:
        valid = [
            s["depth"]
            for s in self.depth_samples
            if current_ts - s["ts"] <= self.window_ms
        ]
        if not valid:
            return 10.0  # Default initial depth fallback
        return float(statistics.median(valid))

    def reset(self) -> None:
        """Clears accumulated depth samples for a new trading session."""
        self.depth_samples.clear()


class BaseEcologyAgent:
    """Base class for behavioral ecology agents responding to market liquidity shocks."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id

    def on_liquidity_change(self, event: LiquidityEvent) -> List[Dict[str, Any]]:
        return []

    def reset(self) -> None:
        """Resets per-session agent state. No-op by default."""
        pass


class ReactiveMarketMakerAgent(BaseEcologyAgent):
    """Widening spread dynamically when depth is depleted to model liquidity vacuum cascades."""

    def __init__(
        self,
        agent_id: str = "reactive_mm_01",
        base_spread_bps: float = 5.0,
        herd_sensitivity: float = 0.40,
    ):
        super().__init__(agent_id)
        self.base_spread_bps = base_spread_bps
        self.current_spread_bps = base_spread_bps
        self.herd_sensitivity = herd_sensitivity
        self.active_quotes: Dict[str, Any] = {}

    def on_liquidity_change(self, event: LiquidityEvent) -> List[Dict[str, Any]]:
        actions = []
        if event.depletion_ratio > self.herd_sensitivity:
            widening_factor = 1.0 + (event.depletion_ratio * 2.0)
            self.current_spread_bps = self.base_spread_bps * widening_factor
            actions.append({
                "action": "WIDEN_SPREAD",
                "agent_id": self.agent_id,
                "new_spread_bps": self.current_spread_bps,
                "widening_factor": widening_factor,
            })
            logger.debug(f"[{self.agent_id}] Liquidity depleted ({event.depletion_ratio:.2f}). Widened spread to {self.current_spread_bps:.2f} bps.")
        else:
            self.current_spread_bps = self.base_spread_bps
        return actions

    def reset(self) -> None:
        self.current_spread_bps = self.base_spread_bps
        self.active_quotes = {}


class ToxicFlowPredatorAgent(BaseEcologyAgent):
    """Informed directional flow predator that aggressively sweeps remaining liquidity upon depletion."""

    def __init__(
        self,
        agent_id: str = "predator_01",
        aggression_threshold: float = 0.50,
        max_sweep_size: float = 5.0,
        direction: str = "SELL",
    ):
        super().__init__(agent_id)
        self.aggression_threshold = aggression_threshold
        self.max_sweep_size = max_sweep_size
        self.direction = direction
        self.is_hunting = False
        self.is_halted = False

    def on_liquidity_change(self, event: LiquidityEvent) -> List[Dict[str, Any]]:
        if self.is_halted:
            return []

        actions = []
        if event.depletion_ratio > self.aggression_threshold:
            self.is_hunting = True
            sweep_qty = min(event.current_depth * 0.80, self.max_sweep_size)
            if sweep_qty > 0:
                actions.append({
                    "action": "IOC_SWEEP",
                    "agent_id": self.agent_id,
                    "side": self.direction,
                    "qty": round(sweep_qty, 4),
                    "tif": "IOC",
                })
                logger.info(f"[{self.agent_id}] Thin book detected ({event.depletion_ratio:.2f}). Fired IOC sweep for {sweep_qty:.2f} units.")
        else:
            self.is_hunting = False
        return actions

    def reset(self) -> None:
        self.is_hunting = False


class SyntheticAgentRegistry:
    """Registry coordinating background ecology agents and broadcasting liquidity cascade events."""

    def __init__(self, baseline_window_s: int = 300):
        self.agents: Dict[str, BaseEcologyAgent] = {}
        self.baseline_tracker = LiquidityBaselineTracker(window_seconds=baseline_window_s)
        self.liquidity_cascade_threshold = 0.40

    def register_agent(self, agent_id: str, agent: BaseEcologyAgent) -> None:
        self.agents[agent_id] = agent

    def broadcast_liquidity_event(self, event: LiquidityEvent) -> List[Dict[str, Any]]:
        all_actions = []
        for agent in self.agents.values():
            actions = agent.on_liquidity_change(event)
            if actions:
                all_actions.extend(actions)
        return all_actions

    def reset_session(self) -> None:
        """Resets baseline tracker and all registered agents for a new trading session."""
        self.baseline_tracker.reset()
        for agent in self.agents.values():
            agent.reset()
