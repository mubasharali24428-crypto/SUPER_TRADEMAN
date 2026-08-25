"""SQUAD DM-1 wave-1: Decimal dual-run tests for the risk path (F-0342).

Covers:
* quantization-active mode: size_decimal_candidate present + floor convention
  at an injected step of 0.001;
* SIZE_DELTA warning when the candidate diverges from legacy by >= 1 step;
* quantization-inactive default: plain RiskDecision, no candidate field,
  identical behavior to pre-wave approvals.
"""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import logging

import pytest

import trading.risk.engine as engine_mod
from trading.risk.engine import RiskEngine, _QuantizedRiskDecision
from trading.risk.models import AccountState, RiskDecision, Side, Signal

NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_signal(**overrides) -> Signal:
    base = Signal(
        asset="BTC/USDT",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=100.0,
        confidence=0.7,
        timestamp=NOW,
        rationale="test signal",
        suggested_stop=95.0,
        suggested_target=112.0,  # reward 12 / risk 5 = 2.4 R:R
    )
    return replace(base, **overrides)


def make_account(**overrides) -> AccountState:
    base = AccountState(equity=100_000.0, peak_equity=100_000.0)
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# ---------------------------------------------------------------------------
# Quantization-active dual-run
# ---------------------------------------------------------------------------

def test_quantization_active_produces_floor_candidate():
    """step=0.001 injected -> approval stays an exact-type RiskDecision (R2
    VB-002) with the floored Decimal candidate delivered out-of-band; legacy
    float position_size unchanged."""
    engine = RiskEngine(instruments={"BTC/USDT": "0.001"})
    account = make_account(equity=123_456.789)

    decision = engine.evaluate(make_signal(), account)

    assert decision.approved
    assert type(decision) is RiskDecision

    order = decision.approved_order
    assert order is not None
    legacy = order.position_size
    cand = engine.get_size_candidate(order)

    # Legacy float path untouched: (equity * risk_pct) / risk_per_unit
    assert legacy == pytest.approx((123_456.789 * 0.01) / 5.0)
    # Floor convention: candidate never exceeds legacy and sits on the grid.
    assert isinstance(cand, Decimal)
    assert float(cand) <= legacy
    assert cand == Decimal("246.913")  # 246.913578 floors to 246.913 @ 0.001
    assert (cand / Decimal("0.001")) % 1 == 0
    # Invariant holds: within one step of the legacy size.
    assert abs(float(cand) - legacy) <= 0.001


def test_quantization_inactive_by_default_is_byte_compatible():
    """instruments=None (default): plain RiskDecision, no candidate attribute,
    identical approval values to the pre-wave engine."""
    decision = RiskEngine().evaluate(make_signal(), make_account())

    assert type(decision) is RiskDecision
    assert not hasattr(decision, "size_decimal_candidate")
    assert decision.approved
    order = decision.approved_order
    assert order is not None
    assert order.position_size == pytest.approx(200.0)
    assert order.risk_pct == pytest.approx(0.01)


def test_instruments_without_asset_match_stays_plain():
    """Step map present but no entry for this asset -> inactive path."""
    engine = RiskEngine(instruments={"ETH/USDT": "0.01"})
    decision = engine.evaluate(make_signal(), make_account())
    assert type(decision) is RiskDecision
    assert not hasattr(decision, "size_decimal_candidate")


def test_empty_step_string_treated_as_inactive():
    engine = RiskEngine(instruments={"BTC/USDT": ""})
    decision = engine.evaluate(make_signal(), make_account())
    assert type(decision) is RiskDecision


# ---------------------------------------------------------------------------
# Divergence reporting (SIZE_DELTA)
# ---------------------------------------------------------------------------

def test_healthy_quantization_never_warns(caplog):
    """On-grid/off-grid sizes within one step must NOT emit SIZE_DELTA."""
    engine = RiskEngine(instruments={"BTC/USDT": "0.001"})
    with caplog.at_level(logging.WARNING, logger="trading.risk"):
        decision = engine.evaluate(make_signal(), make_account(equity=123_456.789))
    assert type(decision) is RiskDecision
    assert [r for r in caplog.records if r.getMessage().startswith("SIZE_DELTA")] == []


def test_size_delta_warning_emitted_when_divergence_ge_one_step(monkeypatch, caplog):
    """Force the quantized candidate > 1 step away from the legacy float and
    require the engine's own invariant branch to emit a SIZE_DELTA warning
    carrying both values plus the tolerance."""
    engine = RiskEngine(instruments={"BTC/USDT": "0.001"})

    # Sabotage only the quantizer result: 150.000 vs legacy ~246.913578 with a
    # 0.001 tolerance -- ~96.9 steps of divergence.
    monkeypatch.setattr(engine_mod, "quantize_to_step", lambda value, step: Decimal("150.000"))

    with caplog.at_level(logging.WARNING, logger="trading.risk"):
        decision = engine.evaluate(make_signal(), make_account(equity=123_456.789))

    assert type(decision) is RiskDecision
    assert decision.approved  # observation only: approval unaffected
    assert decision.approved_order is not None
    assert decision.approved_order.position_size == pytest.approx(246.913578)
    assert engine.get_size_candidate(decision.approved_order) == Decimal("150.000")

    deltas = [r for r in caplog.records if r.getMessage().startswith("SIZE_DELTA")]
    assert deltas, "expected SIZE_DELTA warning for >= 1-step divergence"
    msg = deltas[-1].getMessage()
    assert "legacy_float" in msg and "decimal_candidate" in msg and "tolerance" in msg


# ---------------------------------------------------------------------------
# Degraded mode
# ---------------------------------------------------------------------------

def test_invalid_step_degrades_to_legacy_decision(caplog):
    """Non-positive step must not crash the gate: log SIZE_QUANTIZE_ERROR and
    return the plain legacy-style approval (no candidate)."""
    engine = RiskEngine(instruments={"BTC/USDT": "-0.001"})
    with caplog.at_level(logging.WARNING, logger="trading.risk"):
        decision = engine.evaluate(make_signal(), make_account())

    assert decision.approved
    assert type(decision) is RiskDecision  # R2 VB-002: no carrier subclass anymore
    assert engine.get_size_candidate(decision.approved_order) is None  # degraded: no candidate
    assert decision.approved_order is not None
    assert decision.approved_order.position_size == pytest.approx(200.0)
    errs = [
        r
        for r in caplog.records
        if r.getMessage().startswith("SIZE_QUANTIZE_ERROR")
    ]
    assert errs, "expected SIZE_QUANTIZE_ERROR for invalid step"


# ---------------------------------------------------------------------------
# money.py helper semantics relied on by the dual-run
# ---------------------------------------------------------------------------

def test_money_helpers_boundary_semantics():
    from trading.core.money import check_min_notional, decimal_from_float, quantize_to_step

    assert quantize_to_step("246.913578", "0.001") == Decimal("246.913")
    assert quantize_to_step(0.1 + 0.2, "0.1") == Decimal("0.3")  # str() round-trip
    assert decimal_from_float(0.1) == Decimal("0.1")
    assert check_min_notional("10", "100", "1000") is True   # exactly on boundary passes
    assert check_min_notional("9.99", "100", "1000") is False
