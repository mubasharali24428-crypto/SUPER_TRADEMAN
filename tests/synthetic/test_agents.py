"""Tests for Behavioral Personas and Agent Orchestrator."""

import numpy as np
import pytest

from trading.synthetic.agents.base_agent import Agent
from trading.synthetic.agents.orchestrator import AgentOrchestrator
from trading.synthetic.agents.personas import (AdversarialSpoofer,
                                               CoordinatedPredatorSwarm,
                                               HFTMarketMaker,
                                               InstitutionalVWAP,
                                               LiquidityVampire,
                                               RetailMomentum)
from trading.synthetic.lob import BookIntegrityViolation, SyntheticLOB


class _AlwaysSlipRng:
    """Duck-typed RNG double that always loses the friction roll (slips)."""

    def random(self) -> float:
        return 1.0


def test_agent_orchestrator_step_and_attack():
    # Always-slip rng makes the swarm's crossing dump deterministically
    # rejected via the integrity hook instead of resting crossed.
    lob = SyntheticLOB(base_fill_probability=0.5, rng=_AlwaysSlipRng())
    orchestrator = AgentOrchestrator(lob=lob, seed=42)

    orders_count = orchestrator.step()
    assert orders_count > 0

    # SUB-07: the swarm's below-bid dump crosses the book and is rejected by
    # integrity checking (logged/counted via hook, agent NOT quarantined) —
    # the campaign still triggers and every swarm member was exercised.
    attack_count = orchestrator.trigger_coordinated_attack("predator_swarm")
    assert attack_count >= 0
    assert lob.integrity_violation_count >= 1
    assert "swarm_1" not in orchestrator.quarantined


def test_individual_personas_decisions():
    lob = SyntheticLOB()

    hft = HFTMarketMaker("hft_test")
    hft_orders = hft.decide_action(lob)
    assert len(hft_orders) == 2

    vwap = InstitutionalVWAP("vwap_test")
    vwap_orders = vwap.decide_action(lob)
    assert len(vwap_orders) == 1

    retail = RetailMomentum("retail_test")
    retail_orders = retail.decide_action(lob)
    assert len(retail_orders) == 1

    spoofer = AdversarialSpoofer("spoofer_test")
    spoofer_orders = spoofer.decide_action(lob)
    assert len(spoofer_orders) == 1

    vampire = LiquidityVampire("vampire_test")
    vampire_orders = vampire.decide_action(lob)
    assert len(vampire_orders) == 1


# ---------------------------------------------------------------------------
# SUB-07: explicit RNG ownership + per-agent quarantine
# ---------------------------------------------------------------------------


class _ExplodingAgent(Agent):
    """Agent whose decide_action always raises."""

    def __init__(self, agent_id="boom"):
        super().__init__(agent_id, "EXPLODING")

    def decide_action(self, lob):
        raise RuntimeError("boom: deliberate test failure")

    def update_beliefs(self, market_data):
        pass


class _QuietAgent(Agent):
    """Agent that places one passive order per step."""

    def __init__(self, agent_id="quiet"):
        super().__init__(agent_id, "QUIET")

    def decide_action(self, lob):
        from trading.synthetic.lob import LimitOrder

        bb, ba = lob.get_best_bid_ask()
        return [
            LimitOrder(
                f"{self.agent_id}_b", "buy", bb - 1.0, 1.0, 1000.0, self.agent_id
            )
        ]

    def update_beliefs(self, market_data):
        pass


def test_failing_agent_quarantined_others_continue():
    lob = SyntheticLOB(initial_price=100.0)
    orch = AgentOrchestrator(lob=lob, seed=7)
    boom = _ExplodingAgent("boom_1")
    quiet = _QuietAgent("quiet_1")
    orch.agents = [boom, quiet]  # replace default population

    placed = orch.step()
    assert placed == 1  # quiet agent still ran despite boom raising
    assert "boom_1" in orch.quarantined
    assert "RuntimeError" in orch.quarantined["boom_1"]["last_error"]
    assert orch.agent_quarantine_count == 1
    # Quiet agent's order actually reached the book.
    assert any(o.agent_id == "quiet_1" for o in lob.bids)


def test_quarantined_agent_can_recover_next_step():
    class _FlakyAgent(_QuietAgent):
        def __init__(self, agent_id="flaky"):
            super().__init__(agent_id)
            self.fail_first = True

        def decide_action(self, lob):
            if self.fail_first:
                self.fail_first = False
                raise ValueError("transient")
            return super().decide_action(lob)

    orch = AgentOrchestrator(lob=SyntheticLOB(initial_price=100.0), seed=3)
    flaky = _FlakyAgent()
    orch.agents = [flaky]
    assert orch.step() == 0  # quarantined this step
    assert "flaky" in orch.quarantined
    assert orch.step() == 1  # recovered next step
    assert orch.quarantined["flaky"]["errors"] == 1


def test_orchestrator_rng_is_local_not_global():
    """Constructing with a seed must not touch process-global random state."""
    import random as _random

    _random.seed(12345)
    before = _random.getstate()

    AgentOrchestrator(seed=42)
    AgentOrchestrator(seed=42)

    after = _random.getstate()
    assert before == after  # global RNG untouched


def test_orchestrator_same_seed_same_draws():
    o1 = AgentOrchestrator(seed=99)
    o2 = AgentOrchestrator(seed=99)
    d1 = [o1.rng.random() for _ in range(5)]
    d2 = [o2.rng.random() for _ in range(5)]
    assert d1 == d2


def test_injected_generator_takes_precedence():
    gen = np.random.default_rng(5)
    orch = AgentOrchestrator(rng=gen)
    assert orch.rng is gen
