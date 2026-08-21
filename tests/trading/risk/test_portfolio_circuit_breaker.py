"""Tests for Portfolio-Level Tiered Circuit Breaker."""

import pytest

from trading.risk.models import AccountState, ApprovedExit, Position, Side, _ISSUER
from trading.risk.portfolio_circuit_breaker import CircuitBreakerTier, PortfolioCircuitBreaker


def test_circuit_breaker_normal():
    cb = PortfolioCircuitBreaker()
    account = AccountState(equity=100000.0, peak_equity=100000.0)
    pos = Position(asset="BTC", asset_class="crypto", side=Side.LONG, entry_price=50000.0, stop_price=48000.0, risk_pct=0.01, position_size=1.0)
    account.open_positions = [pos]

    tier, exits = cb.evaluate_portfolio_drawdown(account, mark_prices={"BTC": 50000.0})
    assert tier == CircuitBreakerTier.NORMAL
    assert len(exits) == 0
    assert cb.is_entry_allowed()


def test_circuit_breaker_tier2_entry_block():
    cb = PortfolioCircuitBreaker(warning_threshold_pct=0.03, entry_block_threshold_pct=0.05, flatten_threshold_pct=0.08)
    account = AccountState(equity=100000.0, peak_equity=100000.0)
    pos = Position(asset="BTC", asset_class="crypto", side=Side.LONG, entry_price=50000.0, stop_price=48000.0, risk_pct=0.01, position_size=1.0)
    account.open_positions = [pos]

    # Drawdown = 6% ($6,000 loss) -> mark price $44,000
    tier, exits = cb.evaluate_portfolio_drawdown(account, mark_prices={"BTC": 44000.0})
    assert tier == CircuitBreakerTier.ENTRY_BLOCK
    assert not cb.is_entry_allowed()
    assert len(exits) == 0


def test_circuit_breaker_tier3_flatten():
    cb = PortfolioCircuitBreaker(warning_threshold_pct=0.03, entry_block_threshold_pct=0.05, flatten_threshold_pct=0.08)
    account = AccountState(equity=100000.0, peak_equity=100000.0)
    pos1 = Position(asset="BTC", asset_class="crypto", side=Side.LONG, entry_price=50000.0, stop_price=48000.0, risk_pct=0.01, position_size=1.0)
    pos2 = Position(asset="ETH", asset_class="crypto", side=Side.LONG, entry_price=3000.0, stop_price=2900.0, risk_pct=0.01, position_size=10.0)
    account.open_positions = [pos1, pos2]

    # Drawdown = 10% ($10,000 loss) -> BTC mark $41,000 (-$9000), ETH mark $2900 (-$1000)
    tier, exits = cb.evaluate_portfolio_drawdown(account, mark_prices={"BTC": 41000.0, "ETH": 2900.0})
    assert tier == CircuitBreakerTier.FLATTEN
    assert not cb.is_entry_allowed()
    assert len(exits) == 2
    for ex in exits:
        assert isinstance(ex, ApprovedExit)
        assert ex.issuer is _ISSUER
