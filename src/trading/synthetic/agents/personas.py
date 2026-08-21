"""Behavioral Agent Personas for Synthetic LOB Simulation."""

import random
import time
from typing import Any, Dict, List

from trading.synthetic.agents.base_agent import Agent
from trading.synthetic.lob import LimitOrder, SyntheticLOB

__all__ = [
    "HFTMarketMaker",
    "InstitutionalVWAP",
    "RetailMomentum",
    "AdversarialSpoofer",
    "CoordinatedPredatorSwarm",
    "LiquidityVampire",
]


class HFTMarketMaker(Agent):
    """Quotes tight spreads around micro-price, rapidly canceling and updating."""

    def __init__(self, agent_id: str = "hft_mm_1"):
        super().__init__(agent_id, "HFT_MARKET_MAKER")

    def update_beliefs(self, market_data: Dict[str, Any]) -> None:
        pass

    def decide_action(self, lob: SyntheticLOB) -> List[LimitOrder]:
        mp = lob.get_micro_price()
        now_ms = time.time() * 1000.0
        return [
            LimitOrder(f"{self.agent_id}_b", "buy", mp - 0.5, 1.0, now_ms, self.agent_id),
            LimitOrder(f"{self.agent_id}_a", "sell", mp + 0.5, 1.0, now_ms, self.agent_id),
        ]


class InstitutionalVWAP(Agent):
    """Executes passive, small slice orders over time."""

    def __init__(self, agent_id: str = "vwap_inst_1"):
        super().__init__(agent_id, "INSTITUTIONAL_VWAP")

    def update_beliefs(self, market_data: Dict[str, Any]) -> None:
        pass

    def decide_action(self, lob: SyntheticLOB) -> List[LimitOrder]:
        b, a = lob.get_best_bid_ask()
        now_ms = time.time() * 1000.0
        return [LimitOrder(f"{self.agent_id}_buy", "buy", b, 0.5, now_ms, self.agent_id)]


class RetailMomentum(Agent):
    """Chases momentum breakouts with aggressive market orders."""

    def __init__(self, agent_id: str = "retail_fomo_1"):
        super().__init__(agent_id, "RETAIL_MOMENTUM")

    def update_beliefs(self, market_data: Dict[str, Any]) -> None:
        pass

    def decide_action(self, lob: SyntheticLOB) -> List[LimitOrder]:
        b, a = lob.get_best_bid_ask()
        now_ms = time.time() * 1000.0
        # Aggressive buy at best ask
        return [LimitOrder(f"{self.agent_id}_mkt", "buy", a + 5.0, 1.5, now_ms, self.agent_id)]


class AdversarialSpoofer(Agent):
    """Places fake large depth orders to manipulate signals, then cancels them."""

    def __init__(self, agent_id: str = "spoofer_1"):
        super().__init__(agent_id, "ADVERSARIAL_SPOOFER")
        self.active_spoof_ids: List[str] = []

    def update_beliefs(self, market_data: Dict[str, Any]) -> None:
        pass

    def decide_action(self, lob: SyntheticLOB) -> List[LimitOrder]:
        # Cancel old spoof orders
        for sid in self.active_spoof_ids:
            lob.cancel_order(sid)
        self.active_spoof_ids.clear()

        b, a = lob.get_best_bid_ask()
        now_ms = time.time() * 1000.0
        spoof_id = f"{self.agent_id}_spoof_{int(now_ms)}"
        self.active_spoof_ids.append(spoof_id)

        # Place fake large buy bid just below best bid
        return [LimitOrder(spoof_id, "buy", b - 0.2, 50.0, now_ms, self.agent_id)]


class CoordinatedPredatorSwarm(Agent):
    """Group of coordinated agents executing cascading sell dumps."""

    def __init__(self, agent_id: str = "swarm_predator_1"):
        super().__init__(agent_id, "PREDATOR_SWARM")

    def update_beliefs(self, market_data: Dict[str, Any]) -> None:
        pass

    def decide_action(self, lob: SyntheticLOB) -> List[LimitOrder]:
        b, a = lob.get_best_bid_ask()
        now_ms = time.time() * 1000.0
        # Aggressive dump sell below best bid
        return [LimitOrder(f"{self.agent_id}_dump", "sell", b - 10.0, 5.0, now_ms, self.agent_id)]


class LiquidityVampire(Agent):
    """Front-runs or pulls liquidity when large orders approach."""

    def __init__(self, agent_id: str = "vampire_1"):
        super().__init__(agent_id, "LIQUIDITY_VAMPIRE")

    def update_beliefs(self, market_data: Dict[str, Any]) -> None:
        pass

    def decide_action(self, lob: SyntheticLOB) -> List[LimitOrder]:
        b, a = lob.get_best_bid_ask()
        now_ms = time.time() * 1000.0
        # Front-run buy by 0.01
        return [LimitOrder(f"{self.agent_id}_frontrun", "buy", b + 0.01, 0.2, now_ms, self.agent_id)]
