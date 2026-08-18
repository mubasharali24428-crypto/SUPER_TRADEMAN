from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from trading.backtest.engine import (
    BacktestConfig,
    _apply_slippage,
    _binomial_sf,
    _wilson_ci,
    bootstrap_trade_returns,
    run_backtest,
    split_train_test,
)
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState, Side
from trading.strategy.crypto import generate_signal


def make_synthetic_candles(n=260, base=100.0, spike=25.0, spike_every=20):
    """Flat baseline (tiny alternating noise, so std != 0) with periodic single-bar
    spikes that immediately revert. A smooth sine wave doesn't work here: its
    long-window "trend" reading aliases against the regime filter's windows and
    blocks every entry. A flat baseline with isolated spikes keeps the long-term
    trend near zero (spikes are 1 bar wide) while still giving large, clean
    z-score deviations with plenty of room to clear the 2:1 reward:risk floor."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles = []
    for i in range(n):
        noise = 0.1 if i % 2 == 0 else -0.1
        close = base + noise
        if i % spike_every == 0 and i > 0:
            close = base + spike if (i // spike_every) % 2 == 0 else base - spike
        ts = int((start + timedelta(hours=i)).timestamp() * 1000)
        candles.append([ts, close, close + 0.3, close - 0.3, close, 1.0])
    return candles


def test_slippage_direction_is_always_unfavorable():
    cfg = BacktestConfig(slippage_pct=0.01, commission_pct=0.0)
    assert _apply_slippage(100.0, Side.LONG, entering=True, config=cfg) == pytest.approx(101.0)
    assert _apply_slippage(100.0, Side.LONG, entering=False, config=cfg) == pytest.approx(99.0)
    assert _apply_slippage(100.0, Side.SHORT, entering=True, config=cfg) == pytest.approx(99.0)
    assert _apply_slippage(100.0, Side.SHORT, entering=False, config=cfg) == pytest.approx(101.0)


def test_backtest_runs_and_produces_trades_on_synthetic_mean_reverting_data():
    candles = make_synthetic_candles()
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    result = run_backtest(candles, "BTC/USDT", generate_signal, RiskEngine(), account)

    assert len(result.equity_curve) == len(candles) + 1  # seeded with starting equity before bar 0
    assert result.report.num_trades > 0
    assert result.report.num_trades == len(result.trades)
    assert 0.0 <= result.report.win_rate <= 1.0
    assert result.report.max_drawdown_pct >= 0.0
    assert result.report.final_equity == pytest.approx(result.equity_curve[-1])


def test_fees_and_slippage_reduce_returns():
    candles = make_synthetic_candles()
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)

    zero_cost = run_backtest(
        candles,
        "BTC/USDT",
        generate_signal,
        RiskEngine(),
        account,
        config=BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
    )
    with_costs = run_backtest(
        candles,
        "BTC/USDT",
        generate_signal,
        RiskEngine(),
        account,
        config=BacktestConfig(slippage_pct=0.0015, commission_pct=0.001),
    )
    assert with_costs.report.total_return_pct <= zero_cost.report.total_return_pct


def test_split_train_test_is_chronological_and_covers_all_candles():
    candles = make_synthetic_candles(n=100)
    train, test = split_train_test(candles, train_frac=0.7)
    assert len(train) == 70
    assert len(test) == 30
    assert train[-1][0] < test[0][0]


def test_binomial_sf_and_wilson_ci_match_reference_values():
    # Cross-checked against externally-computed reference figures for these exact
    # win/loss counts (one-sided exact binomial test vs. a 1/3 breakeven rate,
    # Wilson 95% CI): 9/15 -> p=0.031, CI=[35.7%, 80.2%]; 4/7 -> p=0.173,
    # CI=[25.0%, 84.2%]; 13/22 -> p=0.012, CI=[38.7%, 76.7%].
    p9_15 = _binomial_sf(9, 15, 1 / 3)
    assert p9_15 == pytest.approx(0.031, abs=0.001)
    ci9_15 = _wilson_ci(9, 15)
    assert ci9_15[0] == pytest.approx(0.357, abs=0.001)
    assert ci9_15[1] == pytest.approx(0.802, abs=0.001)

    p4_7 = _binomial_sf(4, 7, 1 / 3)
    assert p4_7 == pytest.approx(0.173, abs=0.001)
    ci4_7 = _wilson_ci(4, 7)
    assert ci4_7[0] == pytest.approx(0.250, abs=0.001)
    assert ci4_7[1] == pytest.approx(0.842, abs=0.001)

    p13_22 = _binomial_sf(13, 22, 1 / 3)
    assert p13_22 == pytest.approx(0.012, abs=0.001)
    ci13_22 = _wilson_ci(13, 22)
    assert ci13_22[0] == pytest.approx(0.387, abs=0.001)
    assert ci13_22[1] == pytest.approx(0.767, abs=0.001)


def test_sample_size_verdict_thresholds():
    from trading.backtest.engine import _sample_size_verdict

    assert "insufficient" in _sample_size_verdict(22)
    assert "provisional" in _sample_size_verdict(50)
    assert "reasonable" in _sample_size_verdict(150)


def test_backtest_report_includes_statistical_fields():
    candles = make_synthetic_candles()
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    result = run_backtest(candles, "BTC/USDT", generate_signal, RiskEngine(), account)
    r = result.report
    assert 0.0 <= r.win_rate_p_value <= 1.0
    assert 0.0 <= r.win_rate_ci_low <= r.win_rate_ci_high <= 1.0
    assert r.sample_size_verdict  # non-empty


def test_bootstrap_trade_returns_is_deterministic_with_a_seed():
    from types import SimpleNamespace

    trades = [SimpleNamespace(net_pnl=pnl) for pnl in [500, -200, 300, -400, 600, -100, 250]]
    a = bootstrap_trade_returns(trades, start_equity=100_000.0, n_iterations=500, seed=42)
    b = bootstrap_trade_returns(trades, start_equity=100_000.0, n_iterations=500, seed=42)
    assert a == b
    assert a["p5"] <= a["p50"] <= a["p95"]


def test_bootstrap_trade_returns_handles_no_trades():
    result = bootstrap_trade_returns([], start_equity=100_000.0)
    assert result == {"p5": 0.0, "p50": 0.0, "p95": 0.0}


def test_train_and_test_windows_both_report_full_metrics():
    candles = make_synthetic_candles(n=300)
    train, test = split_train_test(candles, train_frac=0.7)
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)

    train_result = run_backtest(train, "BTC/USDT", generate_signal, RiskEngine(), account)
    test_result = run_backtest(test, "BTC/USDT", generate_signal, RiskEngine(), account)

    for result in (train_result, test_result):
        assert result.report.num_trades >= 0
        assert isinstance(result.report.sharpe_ratio, float)
        assert isinstance(result.report.avg_r_multiple, float)
        assert isinstance(result.report.max_drawdown_pct, float)


# --- trailing stop + corrected breakeven null -------------------------------


def _fires_once_at(bar_index, entry, stop, target):
    """Minimal strategy stub: one LONG signal at a known bar, nothing after."""
    from trading.risk.models import Signal

    fired = []

    def fn(candles, asset, ts):
        if len(candles) - 1 == bar_index and not fired:
            fired.append(True)
            return Signal(
                asset=asset,
                asset_class="crypto",
                side=Side.LONG,
                entry_price=entry,
                confidence=1.0,
                timestamp=ts,
                rationale="test stub",
                suggested_stop=stop,
                suggested_target=target,
            )
        return None

    return fn


def _ramp_then_drop():
    """20 flat bars at 100, signal on bar 20, climb to 140, then fall back."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    closes = [100.0] * 21 + [100.0 + 2 * i for i in range(1, 21)] + [138.0, 120.0, 110.0]
    return [
        [int((start + timedelta(hours=i)).timestamp() * 1000), c, c + 1, c - 1, c, 1.0]
        for i, c in enumerate(closes)
    ]


NO_COST = BacktestConfig(slippage_pct=0.0, commission_pct=0.0, max_hold_bars=200)


def test_trailing_stop_exits_above_entry_after_a_favorable_run():
    candles = _ramp_then_drop()
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    result = run_backtest(
        candles,
        "BTC/USDT",
        _fires_once_at(20, entry=100.0, stop=98.0, target=200.0),
        RiskEngine(),
        account,
        config=replace(NO_COST, trail_atr_mult=1.0),
    )
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stop"
    assert trade.stop_price > 98.0, "stop should have ratcheted up during the advance"
    assert trade.exit_fill > trade.entry_fill, "trailed stop should lock in profit"
    assert trade.net_pnl > 0


def test_trailing_stop_never_loosens_against_the_position():
    """An immediately adverse move must still exit at the original stop -- the
    ratchet is one-way, so a falling price can never widen the stop."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    closes = [100.0] * 21 + [99.0, 97.0, 95.0]
    candles = [
        [int((start + timedelta(hours=i)).timestamp() * 1000), c, c + 1, c - 1, c, 1.0]
        for i, c in enumerate(closes)
    ]
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    result = run_backtest(
        candles,
        "BTC/USDT",
        _fires_once_at(20, entry=100.0, stop=98.0, target=200.0),
        RiskEngine(),
        account,
        config=replace(NO_COST, trail_atr_mult=1.0),
    )
    assert result.trades[0].stop_price == 98.0
    assert result.trades[0].exit_fill == 98.0


def test_r_multiple_is_measured_against_entry_risk_not_the_trailed_stop():
    """Regression guard: R must stay anchored to the risk actually taken at entry.
    Dividing by the trailed stop would inflate every trailed winner."""
    candles = _ramp_then_drop()
    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    result = run_backtest(
        candles,
        "BTC/USDT",
        _fires_once_at(20, entry=100.0, stop=98.0, target=200.0),
        RiskEngine(),
        account,
        config=replace(NO_COST, trail_atr_mult=1.0),
    )
    trade = result.trades[0]
    entry_risk = trade.position_size * abs(trade.entry_fill - 98.0)  # 98.0 = the ORIGINAL stop
    assert trade.r_multiple == pytest.approx(trade.net_pnl / entry_risk)
    # and it must NOT match the figure you'd get from the trailed stop
    trailed_risk = trade.position_size * abs(trade.entry_fill - trade.stop_price)
    assert trade.r_multiple != pytest.approx(trade.net_pnl / trailed_risk)


def test_breakeven_win_rate_reflects_realized_payoffs():
    """The old hardcoded 1/3 assumed a clean 2:1 payoff and zero costs. With
    +2R wins and -1R losses breakeven is 1/3; once losses realize -1.33R
    (slippage + both commissions) it rises above 40%."""
    from types import SimpleNamespace

    from trading.backtest.engine import _breakeven_win_rate

    clean = [SimpleNamespace(net_pnl=1, r_multiple=2.0)] * 3 + [
        SimpleNamespace(net_pnl=-1, r_multiple=-1.0)
    ] * 3
    assert _breakeven_win_rate(clean) == pytest.approx(1 / 3)

    realistic = [SimpleNamespace(net_pnl=1, r_multiple=1.98)] * 5 + [
        SimpleNamespace(net_pnl=-1, r_multiple=-1.33)
    ] * 5
    assert _breakeven_win_rate(realistic) == pytest.approx(0.402, abs=0.001)


def test_breakeven_win_rate_falls_back_when_a_side_is_missing():
    from types import SimpleNamespace

    from trading.backtest.engine import _breakeven_win_rate

    assert _breakeven_win_rate([]) == pytest.approx(1 / 3)
    assert _breakeven_win_rate([SimpleNamespace(net_pnl=1, r_multiple=2.0)]) == pytest.approx(1 / 3)
