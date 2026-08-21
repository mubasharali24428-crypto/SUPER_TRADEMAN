import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from trading.backtest.engine import BacktestConfig
from trading.backtest.portfolio import run_portfolio_backtest
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState, RiskConfig, Side, Signal

DAY_MS = 86_400_000
NO_COST = BacktestConfig(slippage_pct=0.0, commission_pct=0.0, max_hold_bars=200)


def _candles(closes, start=datetime(2020, 1, 1, tzinfo=timezone.utc), pad=1.0):
    out = []
    for i, c in enumerate(closes):
        ts = int((start + timedelta(days=i)).timestamp() * 1000)
        out.append([ts, c, c + pad, c - pad, c, 1.0])
    return out


class RecordingRiskEngine:
    """Thin wrapper that records every decision the real RiskEngine makes, so
    tests can assert on the rejection reason without bypassing the engine."""

    def __init__(self, inner: RiskEngine):
        self.inner = inner
        self.decisions = []

    def evaluate(self, signal, account):
        decision = self.inner.evaluate(signal, account)
        self.decisions.append(decision)
        return decision

    def evaluate_exit_signal(self, signal, account):
        return self.inner.evaluate_exit_signal(signal, account)


def _fixed_signal_strategy(fire_map):
    """fire_map: dict[asset] -> dict[bar_index] -> (side, entry, stop, target).
    Fires once per (asset, bar_index) -- called with only that asset's own
    history, exactly like run_portfolio_backtest calls it."""
    fired = set()

    def fn(candles, asset, ts):
        idx = len(candles) - 1
        spec = fire_map.get(asset, {}).get(idx)
        if spec is None or (asset, idx) in fired:
            return None
        fired.add((asset, idx))
        side, entry, stop, target = spec
        return Signal(
            asset=asset,
            asset_class="crypto",
            side=side,
            entry_price=entry,
            confidence=1.0,
            timestamp=ts,
            rationale="test stub",
            suggested_stop=stop,
            suggested_target=target,
        )

    return fn


# --- (a) two concurrent positions both move the SHARED equity ---------------


def test_two_concurrent_positions_both_move_shared_equity():
    # 25 flat bars, then both assets signal at bar 20 and hit target at bar 21.
    closes = [100.0] * 22
    candles_by_asset = {
        "AAA/USDT": _candles(closes),
        "BBB/USDT": _candles(closes),
    }
    candles_by_asset["AAA/USDT"][21][2] = 116.0  # high, target=110
    candles_by_asset["BBB/USDT"][21][2] = 116.0

    fire_map = {
        "AAA/USDT": {20: (Side.LONG, 100.0, 98.0, 110.0)},
        "BBB/USDT": {20: (Side.LONG, 100.0, 98.0, 110.0)},
    }
    strategy_fn = _fixed_signal_strategy(fire_map)
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    risk_engine = RiskEngine(RiskConfig(risk_pct=0.01, min_reward_risk=2.0))

    result = run_portfolio_backtest(candles_by_asset, strategy_fn, risk_engine, account, config=NO_COST)

    assert len(result.trades) == 2
    assert {t.asset for t in result.trades} == {"AAA/USDT", "BBB/USDT"}
    # position_size = (100000 * 0.01) / 2 = 500; net_pnl = 500 * (110-100) = 5000 each
    for t in result.trades:
        assert t.net_pnl == pytest.approx(5000.0)
    # this is the actual proof: equity reflects BOTH trades, not just one
    assert result.report.final_equity == pytest.approx(100_000.0 + 5000.0 + 5000.0)
    assert result.equity_curve[-1] == pytest.approx(result.report.final_equity)


# --- (b) misaligned timestamps: trades land on each asset's own real dates --


def test_misaligned_asset_lengths_land_trades_on_correct_real_dates():
    # X: 60 daily bars from 2020-01-01. Y: 40 daily bars from 2020-01-21 (a
    # later "listing"), mirroring the real BTC-vs-SOL bar-count mismatch.
    x_start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    y_start = datetime(2020, 1, 21, tzinfo=timezone.utc)
    candles_by_asset = {
        "XXX/USDT": _candles([100.0] * 60, start=x_start),
        "YYY/USDT": _candles([100.0] * 40, start=y_start),
    }
    # Y fires on ITS OWN bar index 5 -> real calendar date 2020-01-26, target
    # hit on its own bar index 6 -> 2020-01-27. X never fires.
    candles_by_asset["YYY/USDT"][6][2] = 116.0  # high on the bar after entry
    fire_map = {"YYY/USDT": {5: (Side.LONG, 100.0, 98.0, 110.0)}}
    strategy_fn = _fixed_signal_strategy(fire_map)
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    risk_engine = RiskEngine(RiskConfig(risk_pct=0.01, min_reward_risk=2.0))

    result = run_portfolio_backtest(candles_by_asset, strategy_fn, risk_engine, account, config=NO_COST)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.asset == "YYY/USDT"
    assert trade.entry_time == y_start + timedelta(days=5)
    assert trade.exit_time == y_start + timedelta(days=6)
    # Y's bar index 5/6 are NOT X's bar index 5/6 (2020-01-06/07) -- if the
    # loop misaligned by shared index instead of timestamp, these would fail.
    assert trade.entry_time != x_start + timedelta(days=5)


# --- (c) the portfolio heat cap actually rejects a signal -------------------


def test_portfolio_heat_cap_rejects_a_signal_and_the_trade_never_opens():
    # A, B, C each open with risk_pct=0.02 at bar 20 (same timestamp) ->
    # combined heat 0.06, exactly at the 0.06 default cap. D signals 5 bars
    # later, while A/B/C are still open (wide stop/target, never hit) -> its
    # 0.02 would push heat to 0.08 > cap.
    closes = [100.0] * 30
    candles_by_asset = {
        sym: _candles(closes) for sym in ["AAA/USDT", "BBB/USDT", "CCC/USDT", "DDD/USDT"]
    }
    wide = (Side.LONG, 100.0, 98.0, 110.0)  # R:R = 5, stop/target never touched by flat data
    fire_map = {
        "AAA/USDT": {20: wide},
        "BBB/USDT": {20: wide},
        "CCC/USDT": {20: wide},
        "DDD/USDT": {25: wide},
    }
    strategy_fn = _fixed_signal_strategy(fire_map)
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    cfg = RiskConfig(risk_pct=0.02, min_reward_risk=2.0, max_heat=0.06, max_positions_per_asset_class=10)
    risk_engine = RecordingRiskEngine(RiskEngine(cfg))

    result = run_portfolio_backtest(candles_by_asset, strategy_fn, risk_engine, account, config=NO_COST)

    rejections = [d for d in risk_engine.decisions if not d.approved and d.signal.asset == "DDD/USDT"]
    assert len(rejections) == 1
    assert "heat" in rejections[0].reason
    # the trade never opened: DDD/USDT never appears among the trades, closed or not
    assert all(t.asset != "DDD/USDT" for t in result.trades)
    assert "DDD/USDT" not in {t.asset for t in result.trades}


# --- (d) the correlation guard actually rejects a signal --------------------


def _sine_closes(n, base, amplitude, phase=0.0, freq=0.3):
    return [base + amplitude * math.sin(i * freq + phase) for i in range(n)]


def test_correlation_guard_rejects_a_signal_on_a_near_identical_asset():
    n = 60
    # E and F share the same relative fluctuation (amplitude/base = 0.05 for
    # both) -> their log-return correlation is ~1.0, well above the 0.7 guard.
    e_closes = _sine_closes(n, base=100.0, amplitude=5.0)
    f_closes = _sine_closes(n, base=50.0, amplitude=2.5)
    candles_by_asset = {"EEE/USDT": _candles(e_closes), "FFF/USDT": _candles(f_closes)}

    e_entry = e_closes[40]
    f_entry = f_closes[45]
    fire_map = {
        "EEE/USDT": {40: (Side.LONG, e_entry, e_entry - 20.0, e_entry + 60.0)},
        "FFF/USDT": {45: (Side.LONG, f_entry, f_entry - 10.0, f_entry + 30.0)},
    }
    strategy_fn = _fixed_signal_strategy(fire_map)
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    risk_engine = RecordingRiskEngine(RiskEngine(RiskConfig(min_reward_risk=2.0)))

    result = run_portfolio_backtest(candles_by_asset, strategy_fn, risk_engine, account, config=NO_COST)

    f_decisions = [d for d in risk_engine.decisions if d.signal.asset == "FFF/USDT"]
    assert len(f_decisions) == 1
    assert not f_decisions[0].approved
    assert "correlation" in f_decisions[0].reason
    assert all(t.asset != "FFF/USDT" for t in result.trades)


def test_correlation_guard_lets_an_uncorrelated_signal_through():
    n = 60
    e_closes = _sine_closes(n, base=100.0, amplitude=5.0)
    rng = random.Random(7)
    g_closes = [80.0]
    for _ in range(n - 1):
        g_closes.append(g_closes[-1] * math.exp(rng.uniform(-0.02, 0.02)))
    candles_by_asset = {"EEE/USDT": _candles(e_closes), "GGG/USDT": _candles(g_closes)}

    e_entry = e_closes[40]
    g_entry = g_closes[45]
    fire_map = {
        "EEE/USDT": {40: (Side.LONG, e_entry, e_entry - 20.0, e_entry + 60.0)},
        "GGG/USDT": {45: (Side.LONG, g_entry, g_entry * 0.9, g_entry * 1.3)},
    }
    strategy_fn = _fixed_signal_strategy(fire_map)
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    risk_engine = RecordingRiskEngine(RiskEngine(RiskConfig(min_reward_risk=2.0)))

    result = run_portfolio_backtest(candles_by_asset, strategy_fn, risk_engine, account, config=NO_COST)

    g_decisions = [d for d in risk_engine.decisions if d.signal.asset == "GGG/USDT"]
    assert len(g_decisions) == 1
    assert g_decisions[0].approved
    assert any(t.asset == "GGG/USDT" for t in result.trades)


# --- (e) end-of-data closes each asset at its OWN last bar ------------------


def test_end_of_data_closes_each_still_open_asset_at_its_own_last_bar():
    h_start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    i_start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    h_closes = [100.0] * 30
    i_closes = [50.0] * 45  # longer series, never touches H's stop/target range
    candles_by_asset = {
        "HHH/USDT": _candles(h_closes, start=h_start),
        "III/USDT": _candles(i_closes, start=i_start),
    }
    # huge stop/target on both -> never hit by the flat data, must be closed by end_of_data
    fire_map = {
        "HHH/USDT": {10: (Side.LONG, 100.0, 50.0, 500.0)},
        "III/USDT": {10: (Side.LONG, 50.0, 10.0, 500.0)},
    }
    strategy_fn = _fixed_signal_strategy(fire_map)
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    risk_engine = RiskEngine(RiskConfig(risk_pct=0.01, min_reward_risk=2.0))

    result = run_portfolio_backtest(candles_by_asset, strategy_fn, risk_engine, account, config=NO_COST)

    trades_by_asset = {t.asset: t for t in result.trades}
    assert set(trades_by_asset) == {"HHH/USDT", "III/USDT"}
    for t in trades_by_asset.values():
        assert t.exit_reason == "end_of_data"

    h_trade, i_trade = trades_by_asset["HHH/USDT"], trades_by_asset["III/USDT"]
    assert h_trade.exit_time == h_start + timedelta(days=29)  # H's own last bar
    assert h_trade.exit_fill == pytest.approx(100.0)  # H's own last close, zero slippage
    assert i_trade.exit_time == i_start + timedelta(days=44)  # I's own last bar, NOT H's
    assert i_trade.exit_fill == pytest.approx(50.0)  # I's own last close
