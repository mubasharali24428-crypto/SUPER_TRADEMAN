"""Tests for execution mode gating."""

import pytest

from trading.config import ExecutionMode, gate_execution_mode


def test_mode_gating_pass():
    # PAPER >= PAPER -> pass
    gate_execution_mode(ExecutionMode.PAPER, ExecutionMode.PAPER)
    # LIVE_RESTRICTED >= PAPER -> pass
    gate_execution_mode(ExecutionMode.PAPER, ExecutionMode.LIVE_RESTRICTED)


def test_mode_gating_violation_raises():
    # Trying to execute LIVE_RESTRICTED action while in BACKTEST mode -> raise RuntimeError
    with pytest.raises(RuntimeError, match="ExecutionMode violation"):
        gate_execution_mode(ExecutionMode.LIVE_RESTRICTED, ExecutionMode.BACKTEST)
