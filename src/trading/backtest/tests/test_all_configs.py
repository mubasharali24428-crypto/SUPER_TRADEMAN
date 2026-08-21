import sys
import math
import json
import sqlite3
import pytest
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import replace

# Ensure src is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from trading.backtest.engine import run_backtest, BacktestConfig
from trading.backtest.strategies import generate_synthetic_candles, make_momentum_strategy
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState, RiskConfig, Side, Signal
from trading.learning.graph import LearningGraph
from trading.learning.policy import ContextualBanditAllocator

@pytest.fixture
def sample_candles():
    return generate_synthetic_candles(300, start_price=100.0, seed=42)

@pytest.fixture
def tmp_lg_file(tmp_path):
    return Path(tmp_path) / "test_lg.jsonl"

@pytest.fixture
def tmp_sqlite_file(tmp_path):
    return Path(tmp_path) / "test_lg.db"

@pytest.mark.parametrize("target_mult,stop_pct", [
    (0.03, 0.01),
    (0.04, 0.01),
    (0.05, 0.01),
])
def test_momentum_strategy_signals(sample_candles, target_mult, stop_pct):
    strat = make_momentum_strategy(target_mult=target_mult, stop_pct=stop_pct)
    dt = datetime.now(timezone.utc)
    sig = strat(sample_candles[:5], "BTC/USDT", dt)
    assert sig is not None
    assert sig.asset == "BTC/USDT"
    assert sig.side in (Side.LONG, Side.SHORT)
    assert sig.suggested_stop is not None
    assert sig.suggested_target is not None

def test_advanced_risk_metrics_and_sqlite_export(sample_candles, tmp_lg_file, tmp_sqlite_file):
    lg = LearningGraph(storage_path=tmp_lg_file)
    risk_cfg = RiskConfig(
        risk_pct=0.005,
        max_drawdown=0.30,
        max_heat=0.10,
        max_heat_high_vol=0.08,
        correlation_threshold=0.5,
        max_positions_per_asset_class=10,
    )
    engine = RiskEngine(risk_cfg)
    acc = AccountState(equity=10000.0, peak_equity=10000.0)
    strat = make_momentum_strategy(target_mult=0.04, stop_pct=0.01)
    bt_cfg = BacktestConfig().with_trailing_atr(mult=2.0, period=14)

    res = run_backtest(
        candles=sample_candles,
        asset="BTC/USDT",
        strategy_fn=strat,
        risk_engine=engine,
        starting_account=acc,
        config=bt_cfg,
        learning_graph=lg,
    )

    rpt = res.report
    assert rpt.num_trades == len(res.trades)
    assert 0.0 <= rpt.win_rate <= 1.0
    assert 0.0 <= rpt.bayesian_win_rate <= 1.0
    assert isinstance(rpt.sortino_ratio, float)
    assert isinstance(rpt.tail_var_99, float)

    # Test SQLite Export
    lg.export_to_sqlite(tmp_sqlite_file)
    assert tmp_sqlite_file.exists()

    conn = sqlite3.connect(str(tmp_sqlite_file))
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM trades")
    count = cur.fetchone()[0]
    conn.close()
    assert count == len(res.trades)

def test_policy_gradient_allocator(sample_candles, tmp_lg_file):
    lg = LearningGraph(storage_path=tmp_lg_file)
    risk_cfg = RiskConfig(risk_pct=0.005, max_drawdown=0.30)
    engine = RiskEngine(risk_cfg)
    acc = AccountState(equity=10000.0, peak_equity=10000.0)
    strat = make_momentum_strategy(target_mult=0.03, stop_pct=0.01)

    res = run_backtest(sample_candles, "BTC/USDT", strat, engine, acc, learning_graph=lg)

    allocator = ContextualBanditAllocator(strategies=["trend_following", "mean_reversion"])
    allocator.fit_from_learning_graph(lg)
    probs = allocator.get_action_probabilities()
    assert len(probs) == 2
    assert math.isclose(sum(probs.values()), 1.0)
    summary = allocator.summary()
    assert "trend_following" in summary

def test_multi_symbol_basket_expansion():
    """Test a 3-symbol portfolio basket (BTC, ETH, SOL) with shared risk engine."""
    candles_btc = generate_synthetic_candles(150, start_price=100.0, seed=1)
    candles_eth = generate_synthetic_candles(150, start_price=1000.0, seed=2)
    candles_sol = generate_synthetic_candles(150, start_price=20.0, seed=3)

    risk_cfg = RiskConfig(risk_pct=0.005, max_drawdown=0.40)
    engine = RiskEngine(risk_cfg)
    acc = AccountState(equity=10000.0, peak_equity=10000.0)
    strat = make_momentum_strategy(target_mult=0.04, stop_pct=0.01)
    bt_cfg = BacktestConfig().with_trailing_atr(mult=2.0, period=14)

    # Sequential portfolio execution across basket
    res_btc = run_backtest(candles_btc, "BTC/USDT", strat, engine, acc, bt_cfg)
    acc_eth = replace(acc, equity=res_btc.report.final_equity, peak_equity=max(acc.peak_equity, res_btc.report.final_equity))
    res_eth = run_backtest(candles_eth, "ETH/USDT", strat, engine, acc_eth, bt_cfg)
    acc_sol = replace(acc_eth, equity=res_eth.report.final_equity, peak_equity=max(acc_eth.peak_equity, res_eth.report.final_equity))
    res_sol = run_backtest(candles_sol, "SOL/USDT", strat, engine, acc_sol, bt_cfg)

    all_trades = res_btc.trades + res_eth.trades + res_sol.trades
    assert len(all_trades) >= 0
    assert res_sol.report.final_equity > 0
