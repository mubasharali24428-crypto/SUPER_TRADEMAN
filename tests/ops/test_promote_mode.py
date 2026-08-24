"""Tests for Mode Promotion Guard."""

import os

import pytest

from scripts.promote_mode import TOKEN_ENV_VAR, promote_execution_mode
from trading.config import ExecutionMode


def test_mode_promotion_rules():
    # Valid promotion: BACKTEST -> PAPER (fail-closed contract: preflight must be EXPLICITLY proven)
    ok, msg = promote_execution_mode(ExecutionMode.BACKTEST, ExecutionMode.PAPER, preflight_passed=True)
    assert ok

    # Invalid jump: BACKTEST -> LIVE_RESTRICTED (blocked)
    ok, msg = promote_execution_mode(ExecutionMode.BACKTEST, ExecutionMode.LIVE_RESTRICTED)
    assert not ok
    assert "Invalid mode promotion jump" in msg

    # Promotion to LIVE_RESTRICTED requires confirmation token
    ok, msg = promote_execution_mode(ExecutionMode.SHADOW, ExecutionMode.LIVE_RESTRICTED, confirm_token="")
    assert not ok
    assert "confirmation token" in msg


def test_live_promotion_with_env_confirmation_token(monkeypatch):
    """Confirmation token comes ONLY from PROMOTE_CONFIRMATION_TOKEN env (fail-closed contract)."""
    monkeypatch.setenv(TOKEN_ENV_VAR, "wave4-test-token")
    ok, msg = promote_execution_mode(
        ExecutionMode.SHADOW,
        ExecutionMode.LIVE_RESTRICTED,
        confirm_token="wave4-test-token",
        preflight_passed=True,
        gate_1_passed=True,
        reconciliation_clean=True,
        drills_passed=True,
    )
    assert ok
