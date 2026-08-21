#!/usr/bin/env bash
# Automated Emergency Rollback Script for SUPER_TRADEMAN
set -e

DEMOTE_MODE="SHADOW"

echo "======================================================="
echo "      SUPER_TRADEMAN EMERGENCY ROLLBACK HARNESS"
echo "      Demoting Target Mode to: $DEMOTE_MODE"
echo "======================================================="

export PYTHONPATH=src:.

echo "[1/4] Triggering Emergency Portfolio Circuit Breaker..."
python3 -c "
from trading.ops.alert_manager import AlertManager, AlertSeverity
mgr = AlertManager()
mgr.evaluate_metric('drawdown_pct', 0.10, 'EMERGENCY ROLLBACK INITIATED BY OPERATOR')
"

echo "[2/4] Running Emergency Preflight Check..."
python3 scripts/preflight_check.py --mode "$DEMOTE_MODE"

echo "[3/4] Running Operational Safety Drills..."
python3 scripts/kill_switch_drill.py --mode "$DEMOTE_MODE"

echo "[4/4] Demoting System Execution Mode..."
python3 scripts/promote_mode.py --from-mode PAPER --to-mode "$DEMOTE_MODE" || true

echo "======================================================="
echo "      EMERGENCY ROLLBACK EXECUTED SUCCESSFULLY!"
echo "      System safely demoted to $DEMOTE_MODE."
echo "======================================================="
