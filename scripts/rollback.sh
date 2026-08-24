#!/usr/bin/env bash
# Automated Emergency Rollback Script for SUPER_TRADEMAN
#
# Hardened (ALEX-FORCE SUB-08):
#   * set -Eeuo pipefail + ERR trap: no step failure is swallowed.
#   * Explicit per-step status; a failed step is recorded and the script exits
#     NON-ZERO with a failure summary — an operator must never see "SUCCESS"
#     after a partial rollback.
#
# NOTE: the demotion step itself is deliberately allowed to continue to later
# steps ONLY when it succeeds. If the circuit-breaker alert or the demotion
# fails, this script reports FAILURE — fix-forward manually from there.

set -Eeuo pipefail

DEMOTE_MODE="SHADOW"

echo "======================================================="
echo "      SUPER_TRADEMAN EMERGENCY ROLLBACK HARNESS"
echo "      Demoting Target Mode to: $DEMOTE_MODE"
echo "======================================================="

export PYTHONPATH=src:.

STEPS=()
declare -A STEP_STATUS  # PASSED / FAILED
CURRENT_STEP=""

record_step() { # name status
    STEPS+=("$1")
    STEP_STATUS["$1"]="$2"
}

print_summary() {
    echo ""
    echo "=================== ROLLBACK STEP SUMMARY ===================="
    local s
    for s in "${STEPS[@]}"; do
        printf '  %-45s %s\n' "$s" "${STEP_STATUS[$s]}"
    done
    echo "==============================================================="
}

cleanup_on_error() {
    local exit_code=$?
    local failed_step="${CURRENT_STEP:-unknown}"
    echo "" >&2
    echo "[ROLLBACK_ABORT] Step '$failed_step' FAILED (exit $exit_code)." >&2
    echo "[ROLLBACK_ABORT] The system may NOT be safely demoted." >&2
    echo "[ROLLBACK_ABORT] Manual follow-up required:" >&2
    echo "  1. Re-run: python3 scripts/promote_mode.py --from-mode <current> --to-mode SHADOW" >&2
    echo "  2. Verify mode file/state and run scripts/preflight_check.py --mode SHADOW" >&2
    echo "  3. Page the on-call operator if the circuit breaker could not fire." >&2
    if declare -F record_step > /dev/null; then
        record_step "$failed_step" "FAILED"
        for s in "${STEPS[@]}"; do
            [[ "${STEP_STATUS[$s]:-}" == "PASSED" ]] || STEP_STATUS["$s"]="FAILED"
        done
    fi
    print_summary
    exit "$exit_code"
}
trap cleanup_on_error ERR

CURRENT_STEP="[1/4] Emergency portfolio circuit breaker"
echo "[$CURRENT_STEP]..."
python3 -c "
from trading.ops.alert_manager import AlertManager, AlertSeverity
mgr = AlertManager()
mgr.evaluate_metric('drawdown_pct', 0.10, 'EMERGENCY ROLLBACK INITIATED BY OPERATOR')
"
record_step "$CURRENT_STEP" "PASSED"

CURRENT_STEP="[2/4] Emergency preflight check"
echo "[$CURRENT_STEP]..."
python3 scripts/preflight_check.py --mode "$DEMOTE_MODE"
record_step "$CURRENT_STEP" "PASSED"

CURRENT_STEP="[3/4] Operational safety drills"
echo "[$CURRENT_STEP]..."
python3 scripts/kill_switch_drill.py --mode "$DEMOTE_MODE"
record_step "$CURRENT_STEP" "PASSED"

CURRENT_STEP="[4/4] Demote system execution mode"
echo "[$CURRENT_STEP]..."
# No `|| true` here anymore: if demotion fails, rollback FAILS loudly (non-zero).
python3 scripts/promote_mode.py --from-mode PAPER --to-mode "$DEMOTE_MODE"
record_step "$CURRENT_STEP" "PASSED"

print_summary
echo "      EMERGENCY ROLLBACK EXECUTED SUCCESSFULLY!"
echo "      System safely demoted to $DEMOTE_MODE."
