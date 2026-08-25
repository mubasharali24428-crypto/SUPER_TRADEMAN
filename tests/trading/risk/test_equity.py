"""Tests for canonical equity accounting (src/trading/risk/equity.py)."""

import pytest

from trading.risk.equity import marked_equity, unrealized_pnl
from trading.risk.models import AccountState, Position, Side


def _pos(asset: str, side: Side, entry: float, size: float) -> Position:
    return Position(
        asset=asset,
        asset_class="crypto",
        side=side,
        entry_price=entry,
        stop_price=entry * 0.95,
        risk_pct=0.01,
        position_size=size,
    )


def test_long_and_short_pnl_sign_convention():
    # Hand-check from the audit evidence: long 2x@(100->110)=+20,
    # short 3x@(50->40)=+30.
    long_pos = _pos("BTC", Side.LONG, 100.0, 2.0)
    short_pos = _pos("ETH", Side.SHORT, 50.0, 3.0)
    total = unrealized_pnl(
        [long_pos, short_pos], {"BTC": 110.0, "ETH": 40.0}
    )
    assert total == pytest.approx(20.0 + 30.0)


def test_missing_mark_marks_at_entry_zero_unrealized():
    pos = _pos("BTC", Side.LONG, 100.0, 2.0)
    assert unrealized_pnl([pos], {}) == pytest.approx(0.0)


# --------------------------------------------------------------------- #
# Wave-6 RECT-ALPHA (VB-016): no silent unit-size fallback               #
# --------------------------------------------------------------------- #

def test_vb016_position_like_without_position_size_fails_loudly():
    """A corrupted / duck-typed position object lacking ``position_size`` must
    raise AttributeError instead of being silently valued at 1 unit."""
    class _BareDTO:
        def __init__(self):
            self.asset = "BTC"
            self.side = Side.LONG
            self.entry_price = 100.0

    with pytest.raises(AttributeError):
        unrealized_pnl([_BareDTO()], {"BTC": 120.0})


def test_vb016_real_position_default_size_still_one():
    """Real Positions keep working; explicit size flows through."""
    unit = _pos("BTC", Side.LONG, 100.0, 1.0)   # dataclass default path
    sized = _pos("ETH", Side.LONG, 50.0, 4.0)
    total = unrealized_pnl([unit, sized], {"BTC": 105.0, "ETH": 52.0})
    assert total == pytest.approx(5.0 + 8.0)


def test_marked_equity_combines_settled_and_unrealized():
    account = AccountState(equity=10_000.0, peak_equity=10_000.0)
    account.open_positions.append(_pos("BTC", Side.LONG, 100.0, 2.0))
    eq = marked_equity(account, {"BTC": 110.0})
    assert eq == pytest.approx(10_020.0)
