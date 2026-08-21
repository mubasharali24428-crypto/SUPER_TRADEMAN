"""Tests for Capital Allocation Engine."""

import pytest

from trading.risk.capital_allocator import CapitalAllocator
from trading.risk.models import AccountState, ApprovedOrder, Position, Side, _ISSUER


def test_capital_allocator_priority_and_constraints():
    allocator = CapitalAllocator(
        max_concurrent_positions=2,
        max_portfolio_exposure_pct=0.50,
        max_single_asset_pct=0.20,
    )

    account = AccountState(equity=100000.0, peak_equity=100000.0)

    # 3 pending orders: BTC (strongest signal), ETH, SOL
    order_btc = ApprovedOrder(
        asset="BTC",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=50000.0,
        stop_price=48000.0,
        target_price=60000.0,  # 5:1 reward ratio -> high priority
        position_size=0.1,    # Notional $5,000 (5% equity)
        risk_pct=0.01,
        issuer=_ISSUER,
    )
    order_eth = ApprovedOrder(
        asset="ETH",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=3000.0,
        stop_price=2900.0,
        target_price=3300.0,  # 3:1 reward ratio
        position_size=2.0,    # Notional $6,000 (6% equity)
        risk_pct=0.01,
        issuer=_ISSUER,
    )
    order_sol = ApprovedOrder(
        asset="SOL",
        asset_class="crypto",
        side=Side.LONG,
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,   # 2:1 reward ratio
        position_size=50.0,   # Notional $5,000 (5% equity)
        risk_pct=0.01,
        issuer=_ISSUER,
    )

    pending = [order_sol, order_btc, order_eth]

    accepted, rejected = allocator.allocate_capital(pending, account)

    # Max concurrent positions is 2 -> BTC and ETH should be accepted, SOL rejected
    assert len(accepted) == 2
    assert {o.asset for o in accepted} == {"BTC", "ETH"}
    assert len(rejected) == 1
    assert rejected[0][0].asset == "SOL"
    assert "CAPITAL_ALLOCATION_REJECTED" in rejected[0][1]

    # SOVEREIGN INVARIANT: Position size of accepted orders is 100% unaltered
    assert accepted[0].position_size in (0.1, 2.0)
