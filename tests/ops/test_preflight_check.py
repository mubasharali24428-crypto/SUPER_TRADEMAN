"""Tests for Deployment Preflight Checker."""

import os
import pytest

from scripts.preflight_check import run_preflight_checks
from trading.config import ExecutionMode


def test_preflight_checks_pass_default():
    passed, results = run_preflight_checks(ExecutionMode.SHADOW)
    assert passed
    assert len(results) >= 5
    assert all(r.passed for r in results if r.severity == "BLOCKING")


def test_preflight_checks_fail_invalid_risk():
    os.environ["RISK_PCT"] = "0.05"  # Exceeds sovereign cap 0.02
    passed, results = run_preflight_checks(ExecutionMode.SHADOW)
    os.environ["RISK_PCT"] = "0.01"  # Reset
    assert not passed
