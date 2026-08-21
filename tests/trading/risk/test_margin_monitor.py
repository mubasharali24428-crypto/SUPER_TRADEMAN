"""Tests for Concurrent Cross-Margin Liquidation Monitor."""

from trading.risk.models import Position, Side, check_portfolio_liquidation


def test_check_portfolio_liquidation_healthy():
    pos1 = Position(asset="BTC", asset_class="crypto", side=Side.LONG, entry_price=50000.0, stop_price=48000.0, risk_pct=0.01, position_size=1.0)
    pos2 = Position(asset="ETH", asset_class="crypto", side=Side.LONG, entry_price=3000.0, stop_price=2900.0, risk_pct=0.01, position_size=10.0)

    positions = [pos1, pos2]
    equity = 20000.0  # Healthy equity ($20k) vs $4,000 maintenance margin requirement
    mark_prices = {"BTC": 50000.0, "ETH": 3000.0}

    is_warning, effective_eq, mm_total = check_portfolio_liquidation(positions, equity, mark_prices)
    assert not is_warning
    assert effective_eq == 20000.0
    assert mm_total == (50000.0 * 0.05 + 30000.0 * 0.05)  # $2500 + $1500 = $4000


def test_check_portfolio_liquidation_warning():
    pos1 = Position(asset="BTC", asset_class="crypto", side=Side.LONG, entry_price=50000.0, stop_price=48000.0, risk_pct=0.01, position_size=1.0)

    positions = [pos1]
    equity = 2700.0  # Equity $2,700 < Buffer ($2500 * 1.20 = $3,000)
    mark_prices = {"BTC": 50000.0}

    is_warning, effective_eq, mm_total = check_portfolio_liquidation(positions, equity, mark_prices, liquidation_buffer_pct=0.20)
    assert is_warning
