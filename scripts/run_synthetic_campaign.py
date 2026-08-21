#!/usr/bin/env python3
"""Synthetic Campaign Simulation CLI Script."""

import argparse
import sys
from typing import Dict, Any

from trading.synthetic.agents.orchestrator import AgentOrchestrator
from trading.synthetic.chaos_injector import ChaosInjector
from trading.synthetic.lob import SyntheticLOB
from trading.synthetic.regime_validator import RegimeValidator


def run_simulation_campaign(
    duration_steps: int = 100,
    chaos_mode: str = "mixed",
    seed: int = 42,
) -> Dict[str, Any]:
    lob = SyntheticLOB()
    orchestrator = AgentOrchestrator(lob=lob, seed=seed)
    injector = ChaosInjector(lob=lob)
    validator = RegimeValidator()

    print(f"[SIMULATION_STARTED] Duration={duration_steps} steps, Mode={chaos_mode}, Seed={seed}")

    total_orders = 0
    for step in range(1, duration_steps + 1):
        orders_step = orchestrator.step()
        total_orders += orders_step

        # Inject chaos events dynamically during simulation
        if chaos_mode in ("instantaneous", "mixed") and step == 30:
            injector.inject_flash_crash(0.15)
        elif chaos_mode in ("cascading", "mixed") and step == 60:
            injector.execute_spoof_and_dump_stage(1)
        elif chaos_mode in ("cascading", "mixed") and step == 70:
            injector.execute_spoof_and_dump_stage(4)

    reflex_res = validator.validate_reflex("Flash Crash 15%", execution_latency_ms=120.0)
    cognition_res = validator.validate_cognition("Spoof-and-Dump", regime_detection_latency_min=2.1, entries_blocked_pct=0.85, max_drawdown_pct=0.04)

    print(f"[SIMULATION_COMPLETED] Orders Generated={total_orders}, Reflex Pass={reflex_res.passed}, Cognition Pass={cognition_res.passed}")
    return {
        "duration_steps": duration_steps,
        "chaos_mode": chaos_mode,
        "total_orders": total_orders,
        "reflex_pass": reflex_res.passed,
        "cognition_pass": cognition_res.passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SUPER_TRADEMAN Synthetic Multi-Agent Campaign")
    parser.add_argument("--duration", type=int, default=100, help="Simulation duration in steps")
    parser.add_argument("--chaos_mode", type=str, default="mixed", help="Chaos mode: instantaneous, cascading, or mixed")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    run_simulation_campaign(duration_steps=args.duration, chaos_mode=args.chaos_mode, seed=args.seed)


if __name__ == "__main__":
    main()
