#!/usr/bin/env bash
# Automated Deployment Script for SUPER_TRADEMAN
set -e

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

# Set PYTHONPATH
export PYTHONPATH=src:.

if [ "$DRY_RUN" = false ]; then
    if command -v git &> /dev/null && git rev-parse --is-inside-work-tree &> /dev/null; then
        BRANCH=$(git rev-parse --abbrev-ref HEAD)
        if [ "$BRANCH" != "main" ]; then
            echo "[DEPLOY_ERROR] Deployments must be run from the 'main' branch (Current: $BRANCH)."
            exit 1
        fi
    fi
fi

echo "[1/6] Running Full Pytest Test Suite..."
python3 -m pytest tests/ -q

echo "[2/6] Running Preflight Validation..."
python3 scripts/preflight_check.py --mode "$TARGET_MODE"

echo "[3/6] Running Operational Kill-Switch Drills..."
python3 scripts/kill_switch_drill.py --mode "$TARGET_MODE"

echo "[4/6] Running State Reconciliation Report..."
python3 scripts/reconcile_report.py --format table

echo "[5/6] Running Shadow Mode Gate 1 Evaluation..."
python3 scripts/shadow_report.py --days 20 --format table

echo "[6/6] Executing Mode Promotion Guard..."
if [ "$TARGET_MODE" = "LIVE_RESTRICTED" ] || [ "$TARGET_MODE" = "LIVE_FULL" ]; then
    if [ -z "$CONFIRM_TOKEN" ]; then
        echo "[DEPLOY_ERROR] Live mode promotion requires --confirm I_UNDERSTAND_THE_RISK token."
        exit 1
    fi
    python3 scripts/promote_mode.py --from-mode SHADOW --to-mode "$TARGET_MODE" --confirm "$CONFIRM_TOKEN"
else
    python3 scripts/promote_mode.py --from-mode PAPER --to-mode "$TARGET_MODE"
fi

echo "======================================================="
echo "      DEPLOYMENT COMPLETED SUCCESSFULLY!"
echo "======================================================="
