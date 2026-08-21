import sys
from pathlib import Path
import json
import pytest
from datetime import datetime, timezone

# Ensure the project src directory is on the import path
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'src'))

from trading.backtest.engine import run_backtest, BacktestConfig
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState, Signal, Side
from trading.learning.graph import LearningGraph

def generate_candles(num: int, start_price: float = 100.0) -> list[list[float]]:
    """Generate a simple upward‑trending candle series.
    Each candle is [timestamp_ms, open, high, low, close, volume].
    """
    candles = []
    ts = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    price = start_price
    for i in range(num):
        open_price = price
        high = price * 1.01
        low = price * 0.99
        close = price * 1.005
        volume = 0
        candles.append([ts + i * 60_000, open_price, high, low, close, volume])
        price = close
    return candles

def simple_strategy(candles, asset, ts):
    """Open a LONG on the first candle, then do nothing.
    Mirrors the minimal strategy used in manual testing.
    """
    if len(candles) == 1:
        return Signal(
            asset=asset,
            asset_class='crypto',
            side=Side.LONG,
            entry_price=candles[-1][1],
            confidence=1.0,
            timestamp=ts,
            rationale='test',
            suggested_stop=candles[-1][3] - 0.5,
            suggested_target=candles[-1][2] + 5.0,
        )
    return None

@pytest.fixture
def fresh_engine():
    return RiskEngine()

@pytest.fixture
def fresh_account():
    return AccountState(equity=10_000.0, peak_equity=10_000.0)

@pytest.fixture
def learning_graph(tmp_path):
    # Use a temporary file for the JSON‑Lines storage
    return LearningGraph(storage_path=Path(tmp_path) / 'lg.jsonl')

def test_single_trade_records(fresh_engine, fresh_account, learning_graph):
    candles = generate_candles(3)
    result = run_backtest(
        candles=candles,
        asset='BTC/USDT',
        strategy_fn=simple_strategy,
        risk_engine=fresh_engine,
        starting_account=fresh_account,
        config=BacktestConfig(),
        learning_graph=learning_graph,
    )
    # Exactly one trade should be produced
    assert len(result.trades) == 1
    # LearningGraph should have one recorded decision/result pair
    summary = learning_graph.dump_summary()
    assert '1 trades recorded' in summary
    # Verify JSONL file contains a single line with both decision and result
    lines = learning_graph.storage_path.read_text().strip().split('\n')
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert 'decision' in payload and 'result' in payload
    # Ensure timestamps are ISO‑8601 strings
    assert isinstance(payload['decision']['timestamp'], str)
    assert isinstance(payload['result']['timestamp'], str)

def test_multiple_candles_performance(fresh_engine, fresh_account):
    """Benchmark run_backtest on a larger synthetic dataset."""
    import time
    candles = generate_candles(1_000)
    # No‑op strategy that never opens a trade to focus on loop overhead
    def noop_strategy(candles, asset, ts):
        return None

    t0 = time.perf_counter()
    res = run_backtest(
        candles=candles,
        asset='BTC/USDT',
        strategy_fn=noop_strategy,
        risk_engine=fresh_engine,
        starting_account=fresh_account,
        config=BacktestConfig(),
    )
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0  # 1,000 candles should easily complete within 1.0s
    assert res.report.num_trades == 0
