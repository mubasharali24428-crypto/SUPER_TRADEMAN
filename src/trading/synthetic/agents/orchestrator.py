"""Agent Orchestrator for Multi-Agent LOB Population Management."""

import random
from typing import Dict, List, Optional

from trading.observability.logger import get_logger
from trading.synthetic.agents.base_agent import Agent
from trading.synthetic.agents.personas import (
    AdversarialSpoofer,
    CoordinatedPredatorSwarm,
    HFTMarketMaker,
    InstitutionalVWAP,
    LiquidityVampire,
    RetailMomentum,
)
from trading.synthetic.lob import SyntheticLOB

__all__ = ["AgentOrchestrator"]

logger = get_logger("trading.synthetic.agents.orchestrator")


class AgentOrchestrator:
    """Manages population of agents, steps simulation synchronous with LOB."""

    def __init__(self, lob: Optional[SyntheticLOB] = None, seed: Optional[int] = 42):
        if seed is not None:
            random.seed(seed)

        self.lob = lob or SyntheticLOB()
        self.agents: List[Agent] = []
        self._initialize_population()

    def _initialize_population(self) -> None:
        self.agents.append(HFTMarketMaker("hft_1"))
        self.agents.append(HFTMarketMaker("hft_2"))
        self.agents.append(InstitutionalVWAP("vwap_1"))
        self.agents.append(RetailMomentum("retail_1"))
        self.agents.append(AdversarialSpoofer("spoofer_1"))
        self.agents.append(CoordinatedPredatorSwarm("swarm_1"))
        self.agents.append(LiquidityVampire("vampire_1"))

    def step(self) -> int:
        """Executes a single simulation step across all registered agents."""
        orders_generated = 0
        for agent in self.agents:
            orders = agent.decide_action(self.lob)
            for ord_obj in orders:
                self.lob.place_order(ord_obj)
                orders_generated += 1
        return orders_generated

    def trigger_coordinated_attack(self, attack_type: str = "predator_swarm") -> int:
        """Triggers coordinated multi-agent attack campaign."""
        logger.warning(f"[COORDINATED_ATTACK_TRIGGERED] Executing attack campaign: {attack_type}")
        count = 0
        if attack_type == "predator_swarm":
            swarm = [Agent for Agent in self.agents if isinstance(Agent, CoordinatedPredatorSwarm)]
            for s in swarm:
                orders = s.decide_action(self.lob)
                for o in orders:
                    self.lob.place_order(o)
                    count += 1
        return count
