"""Tests for portfolio backtest purge and boundary handling."""

from datetime import datetime, timezone

from trading.backtest.engine import BacktestConfig
from trading.backtest.portfolio import FoldBoundaryAction, run_portfolio_backtest
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState, RiskConfig, Signal, Side


def test_portfolio_backtest_force_close():
    # 5 daily bars for BTC
    # ts, open, high, low, close, volume
    candles = [
        [1700000000000, 100.0, 105.0, 99.0, 104.0, 1000.0],
        [1700086400000, 104.0, 110.0, 103.0, 108.0, 1000.0],
        [1700172800000, 108.0, 112.0, 107.0, 111.0, 1000.0],
    ]
    candles_by_asset = {"BTC": candles}

    # Always generate long signal on first bar
    def strategy_fn(asset_candles, asset, dt):
        if len(asset_candles) == 1:
            return Signal(
                asset=asset,
                asset_class="crypto",
                side=Side.LONG,
                entry_price=104.0,
                confidence=0.8,
                timestamp=dt,
                rationale="test_signal",
                suggested_stop=95.0,
                suggested_target=130.0,
            )
        return None

    risk_engine = RiskEngine(RiskConfig(risk_pct=0.01))
    starting_account = AccountState(equity=10000.0, peak_equity=10000.0)

    result = run_portfolio_backtest(
        candles_by_asset=candles_by_asset,
        strategy_fn=strategy_fn,
        risk_engine=risk_engine,
        starting_account=starting_account,
        config=BacktestConfig(),
        boundary_action=FoldBoundaryAction.FORCE_CLOSE,
    )

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "cpcv_fold_force_close"
