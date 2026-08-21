"""End-to-end integration test verifying the full pipeline (Phase 0 through 7)."""

import asyncio
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from trading.backtest.engine import BacktestConfig
from trading.backtest.funding import FundingEvent, apply_funding
from trading.backtest.impact import apply_market_impact
from trading.backtest.portfolio import FoldBoundaryAction, run_portfolio_backtest
from trading.data.quality import validate_ohlcv
from trading.execution.oms import OrderManagementSystem
from trading.execution.outbox import generate_client_order_id
from trading.execution.state_machine import OrderState
from trading.execution.venue_adapter import MockVenueAdapter
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState, RiskConfig, Side, Signal, _ISSUER
from trading.stats.cross_validation import CPCVConfig, generate_cpcv_splits
from trading.stats.effective_trials import effective_trials
from trading.stats.pbo import compute_pbo


@pytest.mark.asyncio
async def test_full_pipeline_end_to_end():
    # 1. Generate synthetic daily OHLCV data for BTC
    dates = pd.date_range("2025-01-01", periods=100, freq="D")
    df = pd.DataFrame(
        {
            "open": np.linspace(50000, 52000, 100),
            "high": np.linspace(50500, 52500, 100),
            "low": np.linspace(49500, 51500, 100),
            "close": np.linspace(50200, 52200, 100),
            "volume": [1000.0] * 100,
        },
        index=dates,
    )

    # 2. Data quality validation (Phase 3)
    validate_ohlcv(df)

    # 3. CPCV split generation (Phase 1)
    cfg = CPCVConfig(n_folds=5, purge_days=1, max_holding_days=1, min_train_size=10, min_test_size=5)
    splits = generate_cpcv_splits(df, cfg)
    assert len(splits) > 0

    # 4. Shared Portfolio Backtest with synthetic exit token (Phase 0 & 1)
    candles = [
        [int(dates[i].timestamp() * 1000), df["open"].iloc[i], df["high"].iloc[i], df["low"].iloc[i], df["close"].iloc[i], df["volume"].iloc[i]]
        for i in range(len(df))
    ]
    candles_by_asset = {"BTC": candles}

    def dummy_strategy(asset_candles, asset, dt):
        if len(asset_candles) == 10:
            return Signal(
                asset=asset,
                asset_class="crypto",
                side=Side.LONG,
                entry_price=asset_candles[-1][4],
                confidence=0.8,
                timestamp=dt,
                rationale="integration_signal",
                suggested_stop=asset_candles[-1][4] * 0.95,
                suggested_target=asset_candles[-1][4] * 1.10,
            )
        return None

    risk_engine = RiskEngine(RiskConfig(risk_pct=0.01))
    starting_account = AccountState(equity=100000.0, peak_equity=100000.0)

    result = run_portfolio_backtest(
        candles_by_asset=candles_by_asset,
        strategy_fn=dummy_strategy,
        risk_engine=risk_engine,
        starting_account=starting_account,
        config=BacktestConfig(),
        boundary_action=FoldBoundaryAction.FORCE_CLOSE,
    )
    assert len(result.trades) >= 1

    # 5. Economic Realism - Funding Fee Deduction (Phase 2)
    open_trades = {"BTC": {"side": Side.LONG, "position_size": 0.1}}
    event = FundingEvent(timestamp=datetime.now(timezone.utc), symbol="BTC", funding_rate=0.0001, mark_price=50000.0)
    updated_account, fee = apply_funding(starting_account, open_trades, event)
    assert fee == 0.5

    # 6. Market Impact Model (Phase 5)
    impact_pct, exec_notional, was_capped = apply_market_impact(
        order_notional=5000.0,
        adv_notional=100000.0,
        sigma=0.02,
        max_participation_pct=0.03,
    )
    assert exec_notional == 3000.0  # capped at 3%
    assert was_capped

    # 7. Execution OMS & Venue Adapter (Phase 4)
    venue = MockVenueAdapter()
    oms = OrderManagementSystem(venue_adapter=venue)
    client_order_id = generate_client_order_id("strat_integration", "sig_1")
    approved_order = result.trades[0]
    # Check invariant constructor token protection
    with pytest.raises(PermissionError):
        from trading.risk.models import ApprovedOrder
        ApprovedOrder(
            asset="BTC",
            asset_class="crypto",
            side=Side.LONG,
            entry_price=50000.0,
            stop_price=48000.0,
            target_price=55000.0,
            position_size=1.0,
            risk_pct=0.01,
            issuer=object(),
        )

    # 8. PBO and Effective Trials statistics (Phase 1)
    pbo = compute_pbo([0.8, -0.2, 0.5])
    assert 0.0 <= pbo <= 1.0

    n_eff = effective_trials(5, avg_corr=0.3)
    assert n_eff > 1.0
