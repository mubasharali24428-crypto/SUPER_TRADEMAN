"""Tests for Mode Promotion Guard."""

from scripts.promote_mode import CONFIRMATION_TOKEN, promote_execution_mode
from trading.config import ExecutionMode


def test_mode_promotion_rules():
    # Valid promotion: BACKTEST -> PAPER
    ok, msg = promote_execution_mode(ExecutionMode.BACKTEST, ExecutionMode.PAPER)
    assert ok

    # Invalid jump: BACKTEST -> LIVE_RESTRICTED (blocked)
    ok, msg = promote_execution_mode(ExecutionMode.BACKTEST, ExecutionMode.LIVE_RESTRICTED)
    assert not ok
    assert "Invalid mode promotion jump" in msg

    # Promotion to LIVE_RESTRICTED requires confirmation token
    ok, msg = promote_execution_mode(ExecutionMode.SHADOW, ExecutionMode.LIVE_RESTRICTED, confirm_token="")
    assert not ok
    assert "confirmation token" in msg

    # Promotion to LIVE_RESTRICTED succeeds with confirmation token and clean gates
    ok, msg = promote_execution_mode(
        ExecutionMode.SHADOW,
        ExecutionMode.LIVE_RESTRICTED,
        confirm_token=CONFIRMATION_TOKEN,
        preflight_passed=True,
        gate_1_passed=True,
        reconciliation_clean=True,
        drills_passed=True,
    )
    assert ok
