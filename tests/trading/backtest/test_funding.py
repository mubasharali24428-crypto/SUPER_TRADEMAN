"""Tests for funding rate accounting and liquidation checks."""

from datetime import datetime, timezone

from trading.backtest.funding import FundingEvent, apply_funding
from trading.risk.models import AccountState, Position, Side, check_liquidation


def test_apply_funding_long_pays():
    account = AccountState(equity=10000.0, peak_equity=10000.0)
    open_trades = {
        "BTC": {
            "side": Side.LONG,
            "position_size": 1.0,  # 1 BTC
        }
    }
    # Funding rate 0.01% (0.0001), mark price $50,000
    # Notional = $50,000. Fee = 1.0 * 50000 * 0.0001 = $5.0 paid.
    event = FundingEvent(
        timestamp=datetime.now(timezone.utc),
        symbol="BTC",
        funding_rate=0.0001,
        mark_price=50000.0,
    )

    updated_account, fee = apply_funding(account, open_trades, event)
    assert fee == 5.0
    assert updated_account.equity == 9995.0


def test_apply_funding_short_receives():
    account = AccountState(equity=10000.0, peak_equity=10000.0)
    open_trades = {
        "BTC": {
            "side": Side.SHORT,
            "position_size": 1.0,
        }
    }
    # Funding rate 0.01%, mark price $50,000
    # Short receives $5.0. Fee returned by apply_funding is -5.0.
    event = FundingEvent(
        timestamp=datetime.now(timezone.utc),
        symbol="BTC",
        funding_rate=0.0001,
        mark_price=50000.0,
    )

    updated_account, fee = apply_funding(account, open_trades, event)
    assert fee == -5.0
    assert updated_account.equity == 10005.0


def test_check_liquidation_healthy():
    position = {
        "side": Side.LONG,
        "entry_fill": 50000.0,
        "position_size": 0.1,  # $5,000 notional
    }
    equity = 1000.0  # healthy equity
    mark_price = 49500.0  # slight drop

    # Unrealized PnL = 0.1 * (49500 - 50000) = -$50
    # Effective equity = $950
    # Maint margin req = 0.1 * 49500 * 0.05 = $247.5
    # Effective equity ($950) > req ($247.5) -> False
    assert not check_liquidation(position, equity, mark_price, maintenance_margin_pct=0.05)


def test_check_liquidation_triggered():
    position = {
        "side": Side.LONG,
        "entry_fill": 50000.0,
        "position_size": 1.0,  # $50,000 notional
    }
    equity = 1000.0  # small equity relative to 1 BTC position
    mark_price = 47500.0  # price drop of $2,500

    # Unrealized PnL = 1.0 * (47500 - 50000) = -$2,500
    # Effective equity = 1000 - 2500 = -$1,500 < Maint req -> True
    assert check_liquidation(position, equity, mark_price, maintenance_margin_pct=0.05)
