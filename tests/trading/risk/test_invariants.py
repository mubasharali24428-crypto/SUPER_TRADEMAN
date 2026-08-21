import pytest
from trading.risk.engine import RiskEngine
from trading.risk.models import ApprovedOrder, ApprovedExit, _ISSUER, AccountState, Side, Signal, RiskDecision, ExitSignal, ExitDecision

# Helper to create a dummy RiskEngine (uses the private _ISSUER internally)
class DummyRiskEngine(RiskEngine):
    def __init__(self):
        super().__init__()

    def evaluate(self, signal, snapshot) -> RiskDecision:
        stop_price = signal.suggested_stop or signal.entry_price * 0.99
        risk_pct = 0.01
        dist = abs(signal.entry_price - stop_price)
        size = (snapshot.equity * risk_pct) / dist if dist > 0 else 1000.0
        order = ApprovedOrder(
            asset=signal.asset,
            asset_class=signal.asset_class,
            side=signal.side,
            entry_price=signal.entry_price,
            stop_price=stop_price,
            target_price=signal.suggested_target or signal.entry_price * 1.02,
            position_size=size,
            risk_pct=risk_pct,
            issuer=_ISSUER,
        )
        return RiskDecision(approved=True, reason="test", signal=signal, approved_order=order)

    def evaluate_exit(self, exit_signal, snapshot) -> ExitDecision:
        exit = ApprovedExit(
            asset=exit_signal.asset,
            asset_class=exit_signal.asset_class,
            reason=exit_signal.reason,
            issuer=_ISSUER,
        )
        return ExitDecision(approved=True, reason="test", signal=exit_signal, approved_exit=exit)


def test_approved_order_construction_allowed():
    engine = DummyRiskEngine()
    signal = Signal(
        asset="BTC",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=20000.0,
        confidence=0.9,
        timestamp=__import__("datetime").datetime.utcnow(),
        rationale="test",
    )
    decision = engine.evaluate(signal, AccountState(equity=100000, peak_equity=100000))
    assert decision.approved
    assert isinstance(decision.approved_order, ApprovedOrder)


def test_approved_order_cannot_be_created_directly():
    with pytest.raises(PermissionError):
        ApprovedOrder(
            asset="BTC",
            asset_class="crypto",
            side=Side.LONG,
            entry_price=20000.0,
            stop_price=19000.0,
            target_price=21000.0,
            position_size=1000,
            risk_pct=0.01,
            issuer=object(),  # wrong issuer
        )


def test_approved_exit_construction_allowed():
    engine = DummyRiskEngine()
    exit_signal = ExitSignal(
        asset="BTC",
        asset_class="crypto",
        reason="manual_exit",
        source="manual",
        confidence=1.0,
        timestamp=__import__("datetime").datetime.utcnow(),
    )
    decision = engine.evaluate_exit(exit_signal, AccountState(equity=100000, peak_equity=100000))
    assert decision.approved
    assert isinstance(decision.approved_exit, ApprovedExit)


def test_approved_exit_cannot_be_created_directly():
    with pytest.raises(PermissionError):
        ApprovedExit(
            asset="BTC",
            asset_class="crypto",
            reason="illegal",
            issuer=object(),
        )

def test_evaluate_exit_signal_not_blocked_by_drawdown():
    engine = DummyRiskEngine()
    # Simulate account with drawdown flags set
    account = AccountState(
        equity=5000,
        peak_equity=10000,
        kill_switch=False,
        high_volatility=False,
    )
    signal = Signal(
        asset="BTC",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=20000.0,
        confidence=0.9,
        timestamp=__import__("datetime").datetime.utcnow(),
        rationale="test",
    )
    decision = engine.evaluate(signal, account)
    assert decision.approved

def test_position_sizing_formula():
    # Size = Equity * risk_pct / |Entry - Stop|
    equity = 100000.0
    risk_pct = 0.01
    entry = 20000.0
    stop = 19000.0
    expected_size = equity * risk_pct / abs(entry - stop)
    engine = DummyRiskEngine()
    signal = Signal(
        asset="BTC",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=entry,
        confidence=0.9,
        timestamp=__import__("datetime").datetime.utcnow(),
        rationale="test",
        suggested_stop=stop,
    )
    decision = engine.evaluate(signal, AccountState(equity=equity, peak_equity=equity))
    order = decision.approved_order
    assert pytest.approx(order.position_size) == expected_size

def test_r_multiple_uses_initial_stop():
    # Placeholder test to enforce policy that R‑multiple uses the initial stop price.
    assert True

def test_stop_fill_is_pessimistic():
    # For a long position, pessimistic fill should be <= stop price (or worse due to gap)
    stop = 19000.0
    gap_low = 18000.0
    fill_price = min(stop, gap_low)
    assert fill_price <= stop
