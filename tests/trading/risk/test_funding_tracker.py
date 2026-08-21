"""Tests for Portfolio Funding Burn Tracker."""

from datetime import datetime, timezone

from trading.backtest.funding import PortfolioFundingTracker


def test_portfolio_funding_tracker():
    tracker = PortfolioFundingTracker(max_daily_funding_burn_pct=0.005)
    now = datetime.now(timezone.utc)

    # 10 funding intervals paying $50 each = $500 total
    for i in range(10):
        tracker.record_funding(now, "BTC", 50.0)

    equity = 100000.0  # $500 / $100,000 = 0.005 (0.5%)
    burn_rate = tracker.calculate_daily_burn_rate(equity)
    assert burn_rate == 0.005
    assert not tracker.is_funding_burn_excessive(equity)

    # Add another $100 fee -> exceeds threshold
    tracker.record_funding(now, "ETH", 100.0)
    assert tracker.is_funding_burn_excessive(equity)
