#!/usr/bin/env python3
"""Execution Mode Promotion Guard Script.

Prevents accidental or unauthorized mode promotion into dangerous execution modes
(e.g., SHADOW -> LIVE_RESTRICTED or LIVE_RESTRICTED -> LIVE_FULL).
"""

import argparse
import sys
from typing import Tuple

from trading.config import ExecutionMode
from trading.observability.logger import get_logger

logger = get_logger("scripts.promote_mode")

CONFIRMATION_TOKEN = "I_UNDERSTAND_THE_RISK"


def promote_execution_mode(
    current_mode: ExecutionMode,
    target_mode: ExecutionMode,
    confirm_token: str = "",
    preflight_passed: bool = True,
    gate_1_passed: bool = True,
    reconciliation_clean: bool = True,
    drills_passed: bool = True,
) -> Tuple[bool, str]:
    """Evaluates whether mode promotion from current_mode to target_mode is permitted."""
    if current_mode == target_mode:
        return False, f"Already in mode {target_mode.value.upper()}."

    # Validate mode hierarchy transition
    valid_transitions = {
        ExecutionMode.BACKTEST: [ExecutionMode.PAPER],
        ExecutionMode.PAPER: [ExecutionMode.SHADOW],
        ExecutionMode.SHADOW: [ExecutionMode.LIVE_RESTRICTED],
        ExecutionMode.LIVE_RESTRICTED: [ExecutionMode.LIVE_FULL],
    }

    allowed = valid_transitions.get(current_mode, [])
    if target_mode not in allowed:
        return False, f"Invalid mode promotion jump: {current_mode.value.upper()} -> {target_mode.value.upper()}. Must follow sequential hierarchy."

    # Rule checks per target mode
    if target_mode == ExecutionMode.PAPER:
        if not preflight_passed:
            return False, "Preflight check failed."

    elif target_mode == ExecutionMode.SHADOW:
        if not preflight_passed:
            return False, "Preflight check failed."
        if not reconciliation_clean:
            return False, "Reconciliation report is not CLEAN."

    elif target_mode in (ExecutionMode.LIVE_RESTRICTED, ExecutionMode.LIVE_FULL):
        if confirm_token != CONFIRMATION_TOKEN:
            return False, f"Missing or invalid human confirmation token. Require --confirm {CONFIRMATION_TOKEN}."
        if not preflight_passed:
            return False, "Preflight check failed."
        if not gate_1_passed:
            return False, "Shadow Mode Gate 1 status is not PASS."
        if not reconciliation_clean:
            return False, "Reconciliation report is not CLEAN."
        if not drills_passed:
            return False, "Kill-switch operational drills have not all passed."

    logger.info(f"[MODE_PROMOTION_SUCCESS] Promoted mode from {current_mode.value.upper()} to {target_mode.value.upper()}.")
    return True, f"Successfully promoted mode to {target_mode.value.upper()}."


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote SUPER_TRADEMAN Execution Mode")
    parser.add_argument("--from-mode", type=str, default="SHADOW", help="Current execution mode")
    parser.add_argument("--to-mode", type=str, required=True, help="Target execution mode")
    parser.add_argument("--confirm", type=str, default="", help="Human confirmation token")
    args = parser.parse_args()

    try:
        cur_m = ExecutionMode(args.from_mode.lower())
        tgt_m = ExecutionMode(args.to_mode.lower())
    except ValueError as e:
        print(f"PROMOTION_STATUS: FAIL\nReason: Invalid execution mode specified: {e}")
        sys.exit(1)

    success, msg = promote_execution_mode(current_mode=cur_m, target_mode=tgt_m, confirm_token=args.confirm)

    print("\n=======================================================")
    print(f"      SUPER_TRADEMAN MODE PROMOTION GUARD")
    print(f"      Transition: {cur_m.value.upper()} -> {tgt_m.value.upper()}")
    print("=======================================================\n")
    print(f"PROMOTION STATUS : {'SUCCESS' if success else 'BLOCKED'}")
    print(f"Details          : {msg}")
    print("-------------------------------------------------------\n")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
