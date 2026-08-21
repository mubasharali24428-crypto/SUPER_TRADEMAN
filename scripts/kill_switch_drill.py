#!/usr/bin/env python3
"""Kill-Switch & Operational Safety Drill CLI Harness Script.

Executes deterministic operational safety drills (Stale Data, Drawdown Tier 2/3,
Duplicate Daemon, Reconciliation Quarantine, Partial Fill, Wash Trading).
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from trading.config import ExecutionMode
from trading.ops.drills import OperationalDrillHarness


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="Run SUPER_TRADEMAN Operational Safety Drills")
    parser.add_argument("--drill", type=str, default="all", help="Drill name (1..7, name, or 'all')")
    parser.add_argument("--mode", type=str, default="SHADOW", help="Execution mode (SHADOW, PAPER, DRILL)")
    parser.add_argument("--format", type=str, default="table", choices=["table", "json"], help="Output format")
    args = parser.parse_args()

    mode = ExecutionMode(args.mode.lower())
    harness = OperationalDrillHarness(mode=mode)

    results = await harness.run_all_drills()
    all_pass = all(r.status == "PASS" for r in results)

    if args.format == "json":
        out = [{"drill_name": r.drill_name, "status": r.status, "duration_ms": r.duration_ms, "events": r.events_observed} for r in results]
        print(json.dumps(out, indent=2))
    else:
        print("\n=======================================================")
        print(f"      SUPER_TRADEMAN OPERATIONAL SAFETY DRILLS")
        print(f"      Mode: {mode.value.upper()}")
        print("=======================================================\n")
        for r in results:
            print(f"[{r.status}] {r.drill_name:<35} ({r.duration_ms} ms)")
            for ev in r.events_observed:
                print(f"       -> Event Observed: {ev}")
        print("\n-------------------------------------------------------")
        print(f"DRILL HARNESS RESULT: {'PASS' if all_pass else 'FAIL'}")
        print("-------------------------------------------------------\n")

    sys.exit(0 if all_pass else 1)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
