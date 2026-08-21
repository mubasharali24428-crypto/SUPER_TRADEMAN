"""Tests for Behavioral Personas and Agent Orchestrator."""

import pytest

from trading.synthetic.agents.orchestrator import AgentOrchestrator
from trading.synthetic.agents.personas import (
    AdversarialSpoofer,
    CoordinatedPredatorSwarm,
    HFTMarketMaker,
    InstitutionalVWAP,
    LiquidityVampire,
    RetailMomentum,
)
from trading.synthetic.lob import SyntheticLOB


def test_agent_orchestrator_step_and_attack():
    lob = SyntheticLOB()
    orchestrator = AgentOrchestrator(lob=lob, seed=42)

    orders_count = orchestrator.step()
    assert orders_count > 0

    attack_count = orchestrator.trigger_coordinated_attack("predator_swarm")
    assert attack_count >= 1


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
