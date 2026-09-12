"""R2 wave-2 regression tests for the quantized-approval path.

VB-002: every approval (quantized or not) is an exact-type RiskDecision --
``type(d) is RiskDecision`` per the repo's own idiom -- with the Decimal
candidate delivered out-of-band via ``RiskEngine.get_size_candidate(order)``
(the remediation the finding's fix hint offers for frozen-dataclass reality;
CPython's builtin type() cannot be overridden from Python).

VB-035: the dual-run now compares the QUANTIZED order quantity against an
independently recomputed exact-Decimal size (SIZE_DELTA_EXACT), making
formula-level float-vs-Decimal divergence observable.
"""

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

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
        suggested_target=112.0,
    )
    return replace(base, **overrides)


def make_account(**overrides) -> AccountState:
    base = AccountState(equity=100_000.0, peak_equity=100_000.0)
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# ---------------------------------------------------------------------------
# VB-002: exact-type identity on ALL paths
# ---------------------------------------------------------------------------


def test_quantized_approval_is_exact_type_riskdecision():
    """Repo convention is exact-type checks; approvals must satisfy them."""
    engine = RiskEngine(instruments={"BTC/USDT": "0.001"})
    decision = engine.evaluate(make_signal(), make_account(equity=123_456.789))

    assert type(decision) is RiskDecision
    assert decision.approved and decision.approved_order is not None
    # The candidate is still available -- out-of-band.
    assert engine.get_size_candidate(decision.approved_order) == Decimal("246.913")


def test_carrier_class_no_longer_constructed_by_evaluate():
    """_QuantizedRiskDecision stays importable for compat but is retired."""
    engine = RiskEngine(instruments={"BTC/USDT": "0.001"})
    decision = engine.evaluate(make_signal(), make_account(equity=123_456.789))

    assert isinstance(decision, _QuantizedRiskDecision) is False
    assert not hasattr(decision, "size_decimal_candidate")


def test_plain_decision_identity_unchanged():
    """Quantization-inactive approvals behave exactly as before."""
    engine = RiskEngine()
    decision = engine.evaluate(make_signal(), make_account())
    assert type(decision) is RiskDecision
    assert engine.get_size_candidate(decision.approved_order) is None


def test_unknown_order_has_no_candidate():
    engine = RiskEngine(instruments={"BTC/USDT": "0.001"})
    decision = engine.evaluate(make_signal(), make_account())
    other = engine.evaluate(
        make_signal(
            asset="ETH/USDT",
            entry_price=50.0,
            suggested_stop=45.0,
            suggested_target=60.0,
        ),
        make_account(),
    )
    assert other.approved_order is not None
    assert engine.get_size_candidate(other.approved_order) is None  # no step configured
    assert engine.get_size_candidate(decision.approved_order) == Decimal("200.000")


def test_candidate_lookup_dies_with_the_order():
    """WeakKeyDictionary: no unbounded growth across a daemon's lifetime."""
    import gc

    engine = RiskEngine(instruments={"BTC/USDT": "0.001"})
    order_ref = None
    for _ in range(3):
        decision = engine.evaluate(make_signal(), make_account())
        order_ref = decision.approved_order
    del decision
    gc.collect()
    assert len(engine._size_candidates) <= 1  # only the surviving order remains


# ---------------------------------------------------------------------------
# VB-035: SIZE_DELTA_EXACT — quantized qty vs Decimal-recomputed size
# ---------------------------------------------------------------------------


def test_size_delta_exact_fires_on_formula_level_divergence(caplog):
    """entry=91.4/stop=91.0: float rpu suffers cancellation -> float grid says
    2499.999 but the exact Decimal recompute says 2500.00. The alarm must fire
    with BOTH Decimals."""
    engine = RiskEngine(instruments={"BTC/USDT": "0.001"})
    signal = make_signal(entry_price=91.4, suggested_stop=91.0, suggested_target=110.0)

    with caplog.at_level("WARNING", logger="trading.risk"):
        decision = engine.evaluate(signal, make_account())

    assert decision.approved  # observation-only: approval unaffected
    assert decision.approved_order.position_size == pytest.approx(2499.9999999999645)
    assert engine.get_size_candidate(decision.approved_order) == Decimal("2499.999")

    events = [
        r for r in caplog.records if r.getMessage().startswith("SIZE_DELTA_EXACT:")
    ]
    assert (
        events
    ), "expected SIZE_DELTA_EXACT for formula-level float/Decimal divergence"
    msg = events[-1].getMessage()
    assert "quantized_qty=2499.999" in msg
    assert "exact_recomputed_size=2500.00" in msg


def test_size_delta_exact_silent_when_paths_agree(caplog):
    """Ordinary sizes: float grid == exact grid -> no SIZE_DELTA_EXACT noise."""
    engine = RiskEngine(instruments={"BTC/USDT": "0.001"})
    with caplog.at_level("WARNING", logger="trading.risk"):
        engine.evaluate(make_signal(), make_account(equity=123_456.789))

    assert [
        r for r in caplog.records if r.getMessage().startswith("SIZE_DELTA_EXACT:")
    ] == []


def test_size_delta_exact_both_values_are_decimals():
    """Contract: both compared quantities are Decimal instances."""
    from trading.core.money import decimal_from_float, quantize_to_step

    engine = RiskEngine(instruments={"BTC/USDT": "0.001"})
    signal = make_signal(entry_price=91.4, suggested_stop=91.0, suggested_target=110.0)
    decision = engine.evaluate(signal, make_account())
    candidate = engine.get_size_candidate(decision.approved_order)

    assert isinstance(candidate, Decimal)
    # Independently recompute what the warning's second operand must be.
    rpu = abs(decimal_from_float(91.4) - decimal_from_float(91.0))
    expected = quantize_to_step(
        decimal_from_float(100_000.0) * decimal_from_float(0.01) / rpu, "0.001"
    )
    assert expected == Decimal("2500.00") != candidate


def test_monkeypatched_quantizer_silent_when_decimals_agree(monkeypatch, caplog):
    """VA-026: SIZE_DELTA compares Decimal candidate vs Decimal exact recompute.
    When monkeypatched quantizer returns same value for both, no warning fires.
    The old wave-1 comparison (legacy float vs quantize) was trivially bounded
    to <1 step by construction and could NEVER fire — removed."""
    engine = RiskEngine(instruments={"BTC/USDT": "0.001"})
    monkeypatch.setattr(
        engine_mod, "quantize_to_step", lambda value, step: Decimal("150.000")
    )

    with caplog.at_level(__import__("logging").WARNING, logger="trading.risk"):
        decision = engine.evaluate(make_signal(), make_account(equity=123_456.789))

    # Both quantize calls (candidate + exact recompute) return 150.000 — no divergence.
    deltas = [
        r
        for r in caplog.records
        if r.getMessage().startswith("SIZE_DELTA:")
        or r.getMessage().startswith("SIZE_DELTA_EXACT:")
    ]
    assert not deltas, "no divergence expected when both Decimal paths agree"
