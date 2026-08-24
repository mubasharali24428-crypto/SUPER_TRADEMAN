#!/usr/bin/env bash
# Automated Deployment Script for SUPER_TRADEMAN
#
# Hardened (ALEX-FORCE SUB-08):
#   * set -Eeuo pipefail + ERR trap -> any failed step aborts and is reported
#     in a final step summary (no silent partial deployments).
#   * Post-promote health gate: retrying readiness probe before declaring
#     success.
#   * Live-mode confirmation token: the EXPECTED token is read from the
#     PROMOTE_CONFIRMATION_TOKEN environment variable by promote_mode.py;
#     pass the matching value out-of-band via --confirm. Never inline the
#     secret in shell history or CI logs.

set -Eeuo pipefail

DRY_RUN=false
TARGET_MODE="SHADOW"
CONFIRM_TOKEN=""

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --dry-run) DRY_RUN=true ;;
        --mode) TARGET_MODE="$2"; shift ;;
        --confirm) CONFIRM_TOKEN="$2"; shift ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

echo "======================================================="
echo "      SUPER_TRADEMAN AUTOMATED DEPLOYMENT HARNESS"
echo "      Target Mode: $TARGET_MODE (Dry Run: $DRY_RUN)"
echo "======================================================="

export PYTHONPATH=src:.

# --- Step tracking & cleanup -------------------------------------------------
STEPS=()
declare -A STEP_STATUS  # PASSED / FAILED / SKIPPED

record_step() { # name status
    STEPS+=("$1")
    STEP_STATUS["$1"]="$2"
}

cleanup_on_error() {
    local exit_code=$?
    local failed_step="${CURRENT_STEP:-unknown}"
    echo ""
    echo "[DEPLOY_ABORT] Step '$failed_step' failed (exit $exit_code). Running cleanup..." >&2
    # Best-effort cleanup: nothing destructive, just state visibility.
    if declare -F record_step > /dev/null; then
        for s in "${STEPS[@]}"; do
            [[ "${STEP_STATUS[$s]:-}" == "PASSED" ]] || STEP_STATUS["$s"]="FAILED"
        done
        record_step "$failed_step" "FAILED"
    fi
    print_summary
    exit "$exit_code"
}
trap cleanup_on_error ERR

CURRENT_STEP=""
print_summary() {
    echo ""
    echo "=================== DEPLOYMENT STEP SUMMARY ==================="
    local s
    for s in "${STEPS[@]}"; do
        printf '  %-45s %s\n' "$s" "${STEP_STATUS[$s]}"
    done
    echo "================================================================"
}

# Health gate: poll until healthy or retries exhausted.
HEALTH_URL_DEFAULT="http://127.0.0.1:8080/"
health_gate() {
    local url="${HEALTH_URL:-$HEALTH_URL_DEFAULT}"
    local retries="${HEALTH_RETRIES:-12}"
    local sleep_s="${HEALTH_SLEEP_SECONDS:-5}"
    local i
    for ((i = 1; i <= retries; i++)); do
        if curl -fsS --max-time 5 "$url" > /dev/null 2>&1; then
            echo "[HEALTH_GATE] OK after $i attempt(s): $url"
            return 0
        fi
        echo "[HEALTH_GATE] attempt $i/$retries failed; retrying in ${sleep_s}s..."
        sleep "$sleep_s"
    done
    echo "[HEALTH_GATE_ERROR] Service did not become healthy after $retries attempts: $url"
    return 1
}

if [ "$DRY_RUN" = false ]; then
    if command -v git &> /dev/null && git rev-parse --is-inside-work-tree &> /dev/null; then
        BRANCH=$(git rev-parse --abbrev-ref HEAD)
        if [ "$BRANCH" != "main" ]; then
            echo "[DEPLOY_ERROR] Deployments must be run from the 'main' branch (Current: $BRANCH)."
            exit 1
        fi
    fi
fi

CURRENT_STEP="[1/6] Full pytest test suite"
echo "[$CURRENT_STEP]..."
python3 -m pytest tests/ -q
record_step "$CURRENT_STEP" "PASSED"

CURRENT_STEP="[2/6] Preflight validation"
echo "[$CURRENT_STEP]..."
python3 scripts/preflight_check.py --mode "$TARGET_MODE"
record_step "$CURRENT_STEP" "PASSED"

CURRENT_STEP="[3/6] Operational kill-switch drills"
echo "[$CURRENT_STEP]..."
python3 scripts/kill_switch_drill.py --mode "$TARGET_MODE"
record_step "$CURRENT_STEP" "PASSED"

CURRENT_STEP="[4/6] State reconciliation report"
echo "[$CURRENT_STEP]..."
python3 scripts/reconcile_report.py --format table
record_step "$CURRENT_STEP" "PASSED"

CURRENT_STEP="[5/6] Shadow Mode Gate 1 evaluation"
echo "[$CURRENT_STEP]..."
python3 scripts/shadow_report.py --days 20 --format table
record_step "$CURRENT_STEP" "PASSED"

CURRENT_STEP="[6/6] Mode promotion guard"
echo "[$CURRENT_STEP]..."
if [ "$TARGET_MODE" = "LIVE_RESTRICTED" ] || [ "$TARGET_MODE" = "LIVE_FULL" ]; then
    # promote_mode.py reads the expected token from $PROMOTE_CONFIRMATION_TOKEN
    # and compares it (constant-time) to the value passed here via --confirm.
    # Supply BOTH out-of-band (secret store / CI masked variable); never commit
    # or log them. Fail fast with a non-leaking hint if either side is missing.
    if [ -z "$CONFIRM_TOKEN" ]; then
        echo "[DEPLOY_ERROR] Live promotion requires the confirmation VALUE via --confirm."
        echo "               The expected token must be set in \$PROMOTE_CONFIRMATION_TOKEN."
        exit 1
    fi
    python3 scripts/promote_mode.py --from-mode SHADOW --to-mode "$TARGET_MODE" --confirm "$CONFIRM_TOKEN"
else
    python3 scripts/promote_mode.py --from-mode PAPER --to-mode "$TARGET_MODE"
fi
record_step "$CURRENT_STEP" "PASSED"

if [ "$DRY_RUN" = true ]; then
    echo "[DRY_RUN] Skipping post-promote health gate."
    record_step "Post-promote health gate" "SKIPPED (dry run)"
else
    CURRENT_STEP="Post-promote health gate"
    echo "[$CURRENT_STEP]..."
    health_gate
    record_step "$CURRENT_STEP" "PASSED"
fi

print_summary
echo "      DEPLOYMENT COMPLETED SUCCESSFULLY!"
