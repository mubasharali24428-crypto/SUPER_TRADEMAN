"""Shared-risk portfolio backtest: one AccountState, one equity curve, many
concurrent positions across assets -- so the risk engine's portfolio heat cap,
correlation guard, and per-asset-class position limit actually gate something
(ANTIGRAVITY.md section 7/9). `run_backtest` in engine.py deliberately stays
single-position; this is a materially different execution loop with different
state (a dict of open trades keyed by asset, not one `open_trade`), kept in
its own file so the single-asset path and its tests are untouched.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

from enum import Enum

from trading.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    _apply_slippage,
    _build_report,
    _check_exit,
    _close_trade,
)
from trading.indicators import atr, log_return_correlation
from trading.risk.engine import RiskEngine
from trading.risk.equity import marked_equity
from trading.risk.models import AccountState, ApprovedExit, Position, Side, _ISSUER


class FoldBoundaryAction(Enum):
    FORCE_CLOSE = "force_close"
    PURGE_OVERLAP = "purge_overlap"


def run_portfolio_backtest(
    candles_by_asset: dict[str, list[list[float]]],
    strategy_fn,
    risk_engine: RiskEngine,
    starting_account: AccountState,
    config: BacktestConfig = BacktestConfig(),
    breakeven_p: float | None = None,
    purge_days: int = 0,
    embargo_days: int = 0,
    boundary_action: FoldBoundaryAction | None = None,
) -> BacktestResult:
    """Replays candles from every asset through one shared account. candles_by_asset
    values are raw candle lists exactly like run_backtest takes:
    [ts_ms, open, high, low, close, volume].

    Assets' candle lists may differ in length/start date (e.g. a later exchange
    listing) -- events are merged by TIMESTAMP, never by list index, so each
    asset's own bar always maps to its own real calendar date.
    """
    # Chronological timeline of every (timestamp, asset, index-in-that-asset's-own-
    # list) across all assets. Ties at the same timestamp break alphabetically by
    # asset symbol -- deterministic, and documented here rather than left to
    # Python's stable-sort-of-insertion-order (which would depend on dict order).
    timeline = sorted(
        (candle[0], asset, i)
        for asset, candles in candles_by_asset.items()
        for i, candle in enumerate(candles)
    )

    settled_equity = starting_account.equity
    peak_equity = starting_account.peak_equity
    # F-0301: deep-copy the account used as the risk-engine snapshot base.
    # AccountState is NOT frozen and open_positions is a mutable list; without
    # the copy, Position objects minted here (or mutated elsewhere) would be
    # shared across every config/fold run through this same caller-owned
    # starting_account, letting state leak between runs.
    snapshot_base = deepcopy(starting_account)

    # Latest observed close per asset, kept as each asset's own bar streams
    # through -- open positions are marked at THEIR asset's price, never the
    # currently-processing asset's bar (calendars can be misaligned).
    last_close: dict[str, float] = {}
    open_trades: dict[str, dict] = {}

    def _mark_prices() -> dict[str, float]:
        return {asset: last_close[asset] for asset in open_trades}

    def _curve_point() -> float:
        # ONE series for drawdown/breakers AND reporting: settled cash equity
        # plus unrealized P&L marked at the current bars' closes.
        mark_account = replace(
            snapshot_base,
            equity=settled_equity,
            peak_equity=peak_equity,
            open_positions=[
                Position(
                    asset=asset,
                    asset_class=trade["asset_class"],
                    side=trade["side"],
                    entry_price=trade["entry_fill"],
                    stop_price=trade["stop_price"],
                    risk_pct=trade["risk_pct"],
                    position_size=trade["position_size"],
                )
                for asset, trade in open_trades.items()
            ],
        )
        return marked_equity(mark_account, _mark_prices())

    equity_curve = [_curve_point()]
    trades = []

    for ts, asset, i in timeline:
        asset_candles = candles_by_asset[asset]
        _ts, _o, high, low, close, _v = asset_candles[i]
        last_close[asset] = close
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)

        open_trade = open_trades.get(asset)
        if open_trade is not None:
            open_trade["bars_held"] += 1
            exit_price, reason = _check_exit(
                open_trade["side"], open_trade["stop_price"], open_trade["target_price"], high, low
            )
            if exit_price is None and open_trade["bars_held"] >= config.max_hold_bars:
                exit_price, reason = close, "time_stop"
            if exit_price is not None:
                trade, net_pnl = _close_trade(open_trade, exit_price, dt, reason, asset, config)
                settled_equity += net_pnl
                peak_equity = max(peak_equity, settled_equity)
                trades.append(trade)
                del open_trades[asset]
            elif config.trail_atr_mult is not None:
                trail = atr(asset_candles[: i + 1], config.trail_atr_period)
                if trail is not None:
                    distance = config.trail_atr_mult * trail
                    if open_trade["side"] is Side.LONG:
                        open_trade["stop_price"] = max(open_trade["stop_price"], high - distance)
                    else:
                        open_trade["stop_price"] = min(open_trade["stop_price"], low + distance)
            equity_curve.append(_curve_point())
            continue

        signal = strategy_fn(asset_candles[: i + 1], asset, dt)
        if signal is not None:
            other_positions = [
                Position(
                    asset=other_asset,
                    asset_class=other_trade["asset_class"],
                    side=other_trade["side"],
                    entry_price=other_trade["entry_fill"],
                    stop_price=other_trade["stop_price"],
                    risk_pct=other_trade["risk_pct"],
                )
                for other_asset, other_trade in open_trades.items()
            ]
            correlations = {}
            for other_asset in open_trades:
                corr = log_return_correlation(candles_by_asset[other_asset], asset_candles, ts)
                if corr is not None:
                    correlations[frozenset({other_asset, asset})] = corr

            snapshot = replace(
                snapshot_base,
                equity=settled_equity,
                peak_equity=peak_equity,
                open_positions=other_positions,
                correlations=correlations,
            )
            decision = risk_engine.evaluate(signal, snapshot)
            if decision.approved:
                order = decision.approved_order
                entry_fill = _apply_slippage(order.entry_price, order.side, entering=True, config=config)
                open_trades[asset] = {
                    "entry_time": dt,
                    "side": order.side,
                    "entry_fill": entry_fill,
                    "stop_price": order.stop_price,
                    "initial_stop_price": order.stop_price,
                    "target_price": order.target_price,
                    "position_size": order.position_size,
                    "bars_held": 0,
                    "risk_pct": order.risk_pct,
                    "asset_class": order.asset_class,
                }
        equity_curve.append(_curve_point())

    # End of data / Fold boundary: close every asset still open using synthetic ApprovedExit.
    # list(): we delete entries as we settle them.
    for asset, open_trade in list(open_trades.items()):
        last_ts, _o, _h, _l, exit_close, _v = candles_by_asset[asset][-1]
        exit_time = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc)
        
        reason_str = "end_of_data"
        if boundary_action == FoldBoundaryAction.FORCE_CLOSE:
            reason_str = "cpcv_fold_force_close"
            # Explicitly mint synthetic ApprovedExit to confirm invariant compliance
            approved_exit = ApprovedExit(
                asset=asset,
                asset_class=open_trade.get("asset_class", "crypto"),
                reason=reason_str,
                issuer=_ISSUER,
            )
            assert approved_exit.issuer is _ISSUER

        trade, net_pnl = _close_trade(open_trade, exit_close, exit_time, reason_str, asset, config)
        settled_equity += net_pnl
        trades.append(trade)
        del open_trades[asset]
    # Final point is fully settled (all positions flat) so the reported series
    # ends exactly at report.final_equity -- one series, no reconciliation gap.
    equity_curve[-1] = settled_equity

    report = _build_report(starting_account.equity, settled_equity, equity_curve, trades, breakeven_p)
    return BacktestResult(report=report, trades=trades, equity_curve=equity_curve)
