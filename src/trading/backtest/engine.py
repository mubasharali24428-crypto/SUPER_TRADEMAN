import math
import random
import statistics
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from trading.indicators import atr
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState, Side


@dataclass(frozen=True)
class BacktestConfig:
    slippage_pct: float = 0.001  # 0.1%, within realistic 0.05-0.15% range
    commission_pct: float = 0.001  # 0.1% per fill, typical taker fee
    max_hold_bars: int = 24  # time stop: exit if the trade hasn't resolved within N bars
    trail_atr_mult: float | None = None  # None = fixed stop. Set to trail the stop by N x ATR.
    trail_atr_period: int = 14


@dataclass
class Trade:
    asset: str
    side: Side
    entry_time: datetime
    exit_time: datetime
    entry_fill: float
    exit_fill: float
    stop_price: float
    target_price: float
    position_size: float
    net_pnl: float
    r_multiple: float
    exit_reason: str


@dataclass
class BacktestReport:
    start_equity: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float  # per-bar, not annualized
    win_rate: float
    avg_r_multiple: float
    num_trades: int
    breakeven_win_rate: float  # win rate this system needs, given its realized payoffs
    win_rate_p_value: float  # one-sided exact binomial test vs. breakeven_win_rate
    win_rate_ci_low: float  # Wilson score 95% CI (valid at small n, unlike Wald)
    win_rate_ci_high: float
    sample_size_verdict: str


@dataclass
class BacktestResult:
    report: BacktestReport
    trades: list
    equity_curve: list


def _apply_slippage(price: float, side: Side, entering: bool, config: BacktestConfig) -> float:
    is_buy = (side is Side.LONG) == entering
    return price * (1 + config.slippage_pct) if is_buy else price * (1 - config.slippage_pct)


def _commission(fill_price: float, position_size: float, config: BacktestConfig) -> float:
    return fill_price * position_size * config.commission_pct


def _check_exit(side: Side, stop_price: float, target_price: float, high: float, low: float):
    if side is Side.LONG:
        stop_hit, target_hit = low <= stop_price, high >= target_price
    else:
        stop_hit, target_hit = high >= stop_price, low <= target_price
    if stop_hit:  # conservative: assume stop fills first when both hit in the same bar
        return stop_price, "stop"
    if target_hit:
        return target_price, "target"
    return None, None


def _close_trade(open_trade, exit_price, exit_time, reason, asset, config) -> tuple:
    exit_fill = _apply_slippage(exit_price, open_trade["side"], entering=False, config=config)
    sign = 1 if open_trade["side"] is Side.LONG else -1
    raw_pnl = sign * (exit_fill - open_trade["entry_fill"]) * open_trade["position_size"]
    fees = _commission(open_trade["entry_fill"], open_trade["position_size"], config) + _commission(
        exit_fill, open_trade["position_size"], config
    )
    net_pnl = raw_pnl - fees
    # R is always measured against the risk taken at ENTRY. Using the current
    # stop_price would silently rescale R once the trailing stop moves, making a
    # trailed winner look like a bigger multiple of a risk that was never taken.
    risked = open_trade["position_size"] * abs(
        open_trade["entry_fill"] - open_trade["initial_stop_price"]
    )
    r_multiple = net_pnl / risked if risked else 0.0
    return Trade(
        asset=asset,
        side=open_trade["side"],
        entry_time=open_trade["entry_time"],
        exit_time=exit_time,
        entry_fill=open_trade["entry_fill"],
        exit_fill=exit_fill,
        stop_price=open_trade["stop_price"],
        target_price=open_trade["target_price"],
        position_size=open_trade["position_size"],
        net_pnl=net_pnl,
        r_multiple=r_multiple,
        exit_reason=reason,
    ), net_pnl


def run_backtest(
    candles: list[list[float]],
    asset: str,
    strategy_fn,
    risk_engine: RiskEngine,
    starting_account: AccountState,
    config: BacktestConfig = BacktestConfig(),
    breakeven_p: float | None = None,  # None = derive from realized payoffs; see _breakeven_win_rate
) -> BacktestResult:
    """Replays candles through strategy_fn -> risk_engine -> a simulated fill,
    one position at a time. candles are [ts_ms, open, high, low, close, volume]."""
    equity = starting_account.equity
    peak_equity = starting_account.peak_equity
    equity_curve = [equity]
    trades: list[Trade] = []
    open_trade = None

    for i, candle in enumerate(candles):
        ts, _o, high, low, close, _v = candle
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)

        if open_trade is not None:
            open_trade["bars_held"] += 1
            exit_price, reason = _check_exit(
                open_trade["side"], open_trade["stop_price"], open_trade["target_price"], high, low
            )
            if exit_price is None and open_trade["bars_held"] >= config.max_hold_bars:
                exit_price, reason = close, "time_stop"
            if exit_price is not None:
                trade, net_pnl = _close_trade(open_trade, exit_price, dt, reason, asset, config)
                equity += net_pnl
                peak_equity = max(peak_equity, equity)
                trades.append(trade)
                open_trade = None
            elif config.trail_atr_mult is not None:
                trail = atr(candles[: i + 1], config.trail_atr_period)
                if trail is not None:
                    distance = config.trail_atr_mult * trail
                    if open_trade["side"] is Side.LONG:
                        open_trade["stop_price"] = max(open_trade["stop_price"], high - distance)
                    else:
                        open_trade["stop_price"] = min(open_trade["stop_price"], low + distance)
            equity_curve.append(equity)
            continue

        signal = strategy_fn(candles[: i + 1], asset, dt)
        if signal is not None:
            account_snapshot = replace(starting_account, equity=equity, peak_equity=peak_equity)
            decision = risk_engine.evaluate(signal, account_snapshot)
            if decision.approved:
                order = decision.approved_order
                entry_fill = _apply_slippage(order.entry_price, order.side, entering=True, config=config)
                open_trade = {
                    "entry_time": dt,
                    "side": order.side,
                    "entry_fill": entry_fill,
                    "stop_price": order.stop_price,
                    "initial_stop_price": order.stop_price,
                    "target_price": order.target_price,
                    "position_size": order.position_size,
                    "bars_held": 0,
                }
        equity_curve.append(equity)

    if open_trade is not None:
        last_ts, _o, _h, _l, last_close, _v = candles[-1]
        exit_time = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc)
        trade, net_pnl = _close_trade(open_trade, last_close, exit_time, "end_of_data", asset, config)
        equity += net_pnl
        trades.append(trade)
        equity_curve[-1] = equity

    report = _build_report(starting_account.equity, equity, equity_curve, trades, breakeven_p)
    return BacktestResult(report=report, trades=trades, equity_curve=equity_curve)


def _build_report(start_equity, final_equity, equity_curve, trades, breakeven_p=None) -> BacktestReport:
    total_return_pct = (final_equity - start_equity) / start_equity

    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    returns = [(b - a) / a for a, b in zip(equity_curve, equity_curve[1:]) if a != 0]
    sharpe = (
        statistics.mean(returns) / statistics.pstdev(returns)
        if len(returns) > 1 and statistics.pstdev(returns) > 0
        else 0.0
    )

    wins = [t for t in trades if t.net_pnl > 0]
    n = len(trades)
    win_rate = len(wins) / n if trades else 0.0
    avg_r = statistics.mean([t.r_multiple for t in trades]) if trades else 0.0
    if breakeven_p is None:
        breakeven_p = _breakeven_win_rate(trades)
    p_value = _binomial_sf(len(wins), n, breakeven_p) if n else 1.0
    ci_low, ci_high = _wilson_ci(len(wins), n)

    return BacktestReport(
        start_equity=start_equity,
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_dd,
        sharpe_ratio=sharpe,
        win_rate=win_rate,
        avg_r_multiple=avg_r,
        num_trades=n,
        breakeven_win_rate=breakeven_p,
        win_rate_p_value=p_value,
        win_rate_ci_low=ci_low,
        win_rate_ci_high=ci_high,
        sample_size_verdict=_sample_size_verdict(n),
    )


def split_train_test(candles: list[list[float]], train_frac: float = 0.7):
    split_idx = int(len(candles) * train_frac)
    return candles[:split_idx], candles[split_idx:]


def _breakeven_win_rate(trades: list, fallback: float = 1 / 3) -> float:
    """Win rate required to break even, given the payoffs this system actually
    realized: avg_loss / (avg_win + avg_loss), in R.

    The previous hardcoded 1/3 assumed a clean 2:1 payoff and zero costs. Neither
    holds: a stop-out realizes worse than -1R once exit slippage and both
    commissions are counted (measured at -1.33R on BTC/USDT 1h), so the true
    breakeven sat near 40%, not 33.3%, and every p-value against 1/3 was
    optimistic.

    ponytail: the null is estimated from the same sample it is tested against, so
    the exact-binomial p-value is approximate rather than exact -- it is a
    sanity check, not a proof. `bootstrap_trade_returns` is the more trustworthy
    statistic here; for a formal test, resample R-multiples directly.
    """
    wins = [t.r_multiple for t in trades if t.net_pnl > 0]
    losses = [-t.r_multiple for t in trades if t.net_pnl <= 0]
    if not wins or not losses:
        return fallback
    avg_win, avg_loss = statistics.mean(wins), statistics.mean(losses)
    if avg_win + avg_loss <= 0:
        return fallback
    return avg_loss / (avg_win + avg_loss)


def _binomial_sf(k_min: int, n: int, p: float) -> float:
    """Exact P(X >= k_min) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(k_min, n + 1))


def _wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple:
    """Wilson score interval -- reliable at small n, unlike the normal/Wald interval."""
    if n == 0:
        return (0.0, 0.0)
    phat = wins / n
    denom = 1 + z**2 / n
    center = phat + z**2 / (2 * n)
    margin = z * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2))
    return (max(0.0, (center - margin) / denom), min(1.0, (center + margin) / denom))


def _sample_size_verdict(n: int) -> str:
    if n < 30:
        return "insufficient (<30 trades): hypothesis-generating, not hypothesis-confirming"
    if n < 100:
        return "provisional (30-99 trades): treat as suggestive, not confirmed"
    return "reasonable (100+ trades)"


def bootstrap_trade_returns(
    trades: list, start_equity: float, n_iterations: int = 2000, seed: int | None = None
) -> dict:
    """Resample realized trade P&Ls with replacement to see how sensitive terminal
    return is to which trades happened to occur -- the robustness check for a trade
    count too low for fold-based cross-validation to be meaningful."""
    if not trades:
        return {"p5": 0.0, "p50": 0.0, "p95": 0.0}
    pnls = [t.net_pnl for t in trades]
    rng = random.Random(seed)
    terminal_returns = sorted(
        sum(rng.choice(pnls) for _ in pnls) / start_equity for _ in range(n_iterations)
    )

    def pct(p):
        return terminal_returns[min(len(terminal_returns) - 1, int(p * len(terminal_returns)))]

    return {"p5": pct(0.05), "p50": pct(0.50), "p95": pct(0.95)}
