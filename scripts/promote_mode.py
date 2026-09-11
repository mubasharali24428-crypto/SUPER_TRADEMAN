#!/usr/bin/env python3
"""Execution Mode Promotion Guard Script.

Prevents accidental or unauthorized mode promotion into dangerous execution modes
(e.g., SHADOW -> LIVE_RESTRICTED or LIVE_RESTRICTED -> LIVE_FULL).

Security posture (audit findings F-0059 cluster):
- All four evidence gates default to False — promotion is blocked unless every
  gate is explicitly proven True by its producing tool.
- The human confirmation token is read ONLY from the PROMOTE_CONFIRMATION_TOKEN
  environment variable and is compared via hmac.compare_digest. It is never
  printed, logged, or embedded in help/error text.
"""

import argparse
import hmac
import os
import sys
from typing import Tuple

from trading.config import ExecutionMode
from trading.observability.logger import get_logger

logger = get_logger("scripts.promote_mode")

TOKEN_ENV_VAR = "PROMOTE_CONFIRMATION_TOKEN"


def _confirmation_token() -> str:
    """Read the confirmation token exclusively from the environment."""
    return os.getenv(TOKEN_ENV_VAR, "")


def _token_matches(provided: str) -> bool:
    """Constant-time comparison of provided token against env-configured token."""
    expected = _confirmation_token()
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def promote_execution_mode(
    current_mode: ExecutionMode,
    target_mode: ExecutionMode,
    confirm_token: str = "",
    preflight_passed: bool = False,
    gate_1_passed: bool = False,
    reconciliation_clean: bool = False,
    drills_passed: bool = False,
    pbo_deflated_sharpe: float | None = None,  # VA-032
    deflated_sharpe: float | None = None,       # VA-032
) -> Tuple[bool, str]:
    """Evaluates whether mode promotion from current_mode to target_mode is permitted.

    All evidence flags default to False (fail-closed). A caller must pass each
    gate as explicitly proven by its tool (preflight_check.py, shadow_report.py,
    reconcile_report.py, kill_switch_drill.py).
    """
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

    # Rule checks per target mode — evidence flags are fail-closed defaults.
    if target_mode == ExecutionMode.PAPER:
        if not preflight_passed:
            return False, "Preflight check failed."

    elif target_mode == ExecutionMode.SHADOW:
        if not preflight_passed:
            return False, "Preflight check failed."
        if not reconciliation_clean:
            return False, "Reconciliation report is not CLEAN."

    elif target_mode in (ExecutionMode.LIVE_RESTRICTED, ExecutionMode.LIVE_FULL):
        if not _token_matches(confirm_token):
            logger.warning(
                f"[MODE_PROMOTION_BLOCKED] Missing/invalid confirmation token for "
                f"{current_mode.value.upper()} -> {target_mode.value.upper()}."
            )
            return False, (
                "Missing or invalid human confirmation token. Set the "
                f"{TOKEN_ENV_VAR} environment variable and pass the matching value via --confirm."
            )
        if not preflight_passed:
            return False, "Preflight check failed."
        if not gate_1_passed:
            return False, "Shadow Mode Gate 1 status is not PASS."
        if not reconciliation_clean:
            return False, "Reconciliation report is not CLEAN."
        if not drills_passed:
            return False, "Kill-switch operational drills have not all passed."

        # VA-032: PBO/DSR probabilistic safeguards for LIVE promotion
        if pbo_deflated_sharpe is not None and pbo_deflated_sharpe >= 0.5:
            return False, (
                f"PBO deflated Sharpe ratio {pbo_deflated_sharpe:.3f} >= 0.5 — "
                f"high probability of overfitting; cannot promote to LIVE."
            )
        if deflated_sharpe is not None and deflated_sharpe <= 0:
            return False, (
                f"Deflated Sharpe ratio {deflated_sharpe:.3f} <= 0 — "
                f"strategy performance is indistinguishable from random; cannot promote to LIVE."
            )
        if pbo_deflated_sharpe is not None and deflated_sharpe is not None:
            logger.info(
                "[VA-032] PBO-DSR gate: PBO-deflated Sharpe=%.3f, Deflated Sharpe=%.3f",
                pbo_deflated_sharpe, deflated_sharpe,
            )

    logger.info(f"[MODE_PROMOTION_SUCCESS] Promoted mode from {current_mode.value.upper()} to {target_mode.value.upper()}.")
    return True, f"Successfully promoted mode to {target_mode.value.upper()}."


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote SUPER_TRADEMAN Execution Mode")
    parser.add_argument("--from-mode", type=str, default="SHADOW", help="Current execution mode")
    parser.add_argument("--to-mode", type=str, required=True, help="Target execution mode")
    parser.add_argument("--confirm", type=str, default="", help="Human confirmation token (value supplied out-of-band)")
    parser.add_argument("--preflight-passed", action="store_true", default=False, help="VA-070: Preflight check passed (from preflight_check.py exit code 0)")
    parser.add_argument("--gate1-passed", action="store_true", default=False, help="VA-070: Gate 1 report PASS (from generate_gate1_report.py exit code 0)")
    parser.add_argument("--reconciliation-clean", action="store_true", default=False, help="VA-070: Reconciliation report CLEAN (from reconcile_report.py exit code 0)")
    parser.add_argument("--drills-passed", action="store_true", default=False, help="VA-070: Kill-switch drills passed (from kill_switch_drill.py exit code 0)")
    parser.add_argument("--pbo-deflated-sharpe", type=float, default=None, help="VA-032: PBO-deflated Sharpe ratio (pass-through from compute_pbo_cscv)")
    parser.add_argument("--deflated-sharpe", type=float, default=None, help="VA-032: Deflated Sharpe ratio (pass-through from deflated_sharpe_ratio)")
    args = parser.parse_args()

    try:
        cur_m = ExecutionMode(args.from_mode.lower())
        tgt_m = ExecutionMode(args.to_mode.lower())
    except ValueError as e:
        print(f"PROMOTION_STATUS: FAIL\nReason: Invalid execution mode specified: {e}")
        sys.exit(1)

    success, msg = promote_execution_mode(
        current_mode=cur_m, target_mode=tgt_m, confirm_token=args.confirm,
        preflight_passed=args.preflight_passed,
        gate_1_passed=args.gate1_passed,
        reconciliation_clean=args.reconciliation_clean,
        drills_passed=args.drills_passed,
        pbo_deflated_sharpe=args.pbo_deflated_sharpe, deflated_sharpe=args.deflated_sharpe,
    )

    print("\n=======================================================")
    print("      SUPER_TRADEMAN MODE PROMOTION GUARD")
    print(f"      Transition: {cur_m.value.upper()} -> {tgt_m.value.upper()}")
    print("=======================================================\n")
    print(f"PROMOTION STATUS : {'SUCCESS' if success else 'BLOCKED'}")
    print(f"Details          : {msg}")
    print("-------------------------------------------------------\n")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
