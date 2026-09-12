"""Regression test for VA-042: GARCH vol scale must NOT fold the survival multiplier.

The heartbeat used to build garch_vol_scale = garch_scale * survival multiplier, and
engine.evaluate() multiplies by garch_vol_scale again => double application (risk grew
when models said shrink). After the fix the heartbeat passes the raw GARCH factor.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

HB = Path(__file__).resolve().parents[1] / "src" / "trading" / "daemon" / "heartbeat.py"


def test_heartbeat_does_not_fold_survival_multiplier_into_garch_scale():
    src = HB.read_text()
    assert "effective_risk_multiplier" not in re.findall(r"garch_scale\s*=.*", src), (
        "garch_scale must be the raw GARCH volatility_scale_factor; folding the survival "
        "multiplier here double-applies it because engine.evaluate() also multiplies by "
        "garch_vol_scale (VA-042)."
    )


def test_engine_still_applies_garch_vol_scale_once():
    eng_src = (
        Path(__file__).resolve().parents[1] / "src" / "trading" / "risk" / "engine.py"
    ).read_text()
    # exactly one application site remains
    assert eng_src.count("effective_risk_pct *= signal.garch_vol_scale") == 1


import re  # noqa: E402  (kept at bottom so the asserts above read cleanly)
