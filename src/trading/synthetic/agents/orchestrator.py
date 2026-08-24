"""Agent Orchestrator for Multi-Agent LOB Population Management.

Determinism + resilience contract (SUB-07):
* The orchestrator owns an explicit ``numpy.random.Generator`` built from the
  ``seed`` argument (or an injected ``rng``).  It **never** seeds process-global
  RNG state, so concurrent simulations in one process stay independent and
  runs are reproducible with a fixed seed.
* Each agent's step is wrapped individually: an agent that raises is
  quarantined (skipped this step, counted via ``agent_quarantined`` metric +
  warning log) instead of killing the whole simulation step.
"""

from typing import Any, Dict, List, Optional

import numpy as np

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
from trading.synthetic.lob import BookIntegrityViolation, SyntheticLOB

__all__ = ["AgentOrchestrator"]

logger = get_logger("trading.synthetic.agents.orchestrator")


def _resolve_rng(seed: Optional[int], rng: Optional[Any]) -> np.random.Generator:
    if isinstance(rng, np.random.Generator):
        return rng
    if seed is None:
        return np.random.default_rng()  # OS entropy; explicit, not silent-global
    return np.random.default_rng(seed)


class AgentOrchestrator:
    """Manages population of agents, steps simulation synchronous with LOB."""

    def __init__(
        self,
        lob: Optional[SyntheticLOB] = None,
        seed: Optional[int] = None,
        rng: Optional[np.random.Generator] = None,
    ):
        """Args:
        lob: shared order book; a fresh SyntheticLOB is created when omitted.
        seed: explicit int seed for the orchestrator's Generator.  ``None``
            means fresh OS entropy — randomness is *explicit*, never inherited
            from global state (previously ``seed=42`` silently seeded the
            process-global ``random`` module).
        rng: pre-built ``numpy.random.Generator`` taking precedence over
            ``seed``.
        """
        self.rng = _resolve_rng(seed, rng)
        self.lob = lob or SyntheticLOB()
        self.agents: List[Agent] = []
        # agent_id -> {"errors": int, "last_error": str}; non-empty == quarantined
        self.quarantined: Dict[str, Dict[str, Any]] = {}
        self.agent_quarantine_count = 0
        self._initialize_population()

    def _initialize_population(self) -> None:
        self.agents.append(HFTMarketMaker("hft_1"))
        self.agents.append(HFTMarketMaker("hft_2"))
        self.agents.append(InstitutionalVWAP("vwap_1"))
        self.agents.append(RetailMomentum("retail_1"))
        self.agents.append(AdversarialSpoofer("spoofer_1"))
        self.agents.append(CoordinatedPredatorSwarm("swarm_1"))
        self.agents.append(LiquidityVampire("vampire_1"))

    def _run_agent(self, agent: Agent) -> int:
        """Step one agent, placing its orders. Returns orders placed.

        Quarantine semantics:
        * Unexpected exceptions -> the agent is quarantined (skipped) and the
          rest of the population continues; ``agent_quarantined`` warning +
          counter are emitted.
        * :class:`BookIntegrityViolation` from the LOB is an *order rejection*,
          not agent misbehavior: counted/logged, agent stays in the rotation.
        """
        try:
            orders = agent.decide_action(self.lob)
            placed = 0
            for ord_obj in orders:
                try:
                    self.lob.place_order(ord_obj)
                    placed += 1
                except BookIntegrityViolation:
                    logger.warning(
                        "[ORDER_REJECTED_INTEGRITY] %s order crossed the book; rejected",
                        agent.agent_id,
                        extra={
                            "event": "book_integrity_violation",
                            "metric": "book_integrity_violations_total",
                            "agent_id": agent.agent_id,
                        },
                    )
            return placed
        except Exception as exc:  # noqa: BLE001 - quarantine any agent failure
            self.quarantined[agent.agent_id] = {
                "errors": self.quarantined.get(agent.agent_id, {}).get("errors", 0) + 1,
                "last_error": f"{type(exc).__name__}: {exc}",
            }
            self.agent_quarantine_count += 1
            logger.warning(
                "[AGENT_QUARANTINED] %s failed (%s); skipped for this step",
                agent.agent_id, exc,
                extra={
                    "event": "agent_quarantined",
                    "metric": "agent_quarantined_total",
                    "agent_id": agent.agent_id,
                },
            )
            return 0

    def step(self) -> int:
        """Executes a single simulation step across all registered agents."""
        orders_generated = 0
        for agent in self.agents:
            orders_generated += self._run_agent(agent)
        return orders_generated

    def trigger_coordinated_attack(self, attack_type: str = "predator_swarm") -> int:
        """Triggers coordinated multi-agent attack campaign."""
        logger.warning(f"[COORDINATED_ATTACK_TRIGGERED] Executing attack campaign: {attack_type}")
        count = 0
        if attack_type == "predator_swarm":
            swarm = [a for a in self.agents if isinstance(a, CoordinatedPredatorSwarm)]
            for s in swarm:
                count += self._run_agent(s)
        return count
