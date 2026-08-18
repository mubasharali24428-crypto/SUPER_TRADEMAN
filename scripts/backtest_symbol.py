import asyncio
import json
import sys

import ccxt

from trading.backtest.engine import run_backtest
from trading.data.crypto import fetch_ohlcv_range
from trading.risk.engine import RiskEngine
from trading.risk.models import AccountState
from trading.strategy.crypto import generate_signal


async def main(symbol: str, days: int = 270):
    exchange = ccxt.binance({"enableRateLimit": True})
    until = exchange.milliseconds()
    since = until - days * 24 * 60 * 60 * 1000
    candles = await fetch_ohlcv_range(exchange, symbol, "1h", since, until)
    if len(candles) < 150:
        print(json.dumps({"symbol": symbol, "error": f"insufficient candles ({len(candles)})"}))
        return

    account = AccountState(equity=100_000.0, peak_equity=100_000.0)
    result = run_backtest(candles, symbol, generate_signal, RiskEngine(), account)
    r = result.report
    trades = [
        {"entry_time": t.entry_time.isoformat(), "net_pnl": t.net_pnl, "r_multiple": t.r_multiple}
        for t in result.trades
    ]
    print(
        json.dumps(
            {
                "symbol": symbol,
                "num_candles": len(candles),
                "start": candles[0][0],
                "end": candles[-1][0],
                "total_return_pct": r.total_return_pct,
                "max_drawdown_pct": r.max_drawdown_pct,
                "win_rate": r.win_rate,
                "win_rate_p_value": r.win_rate_p_value,
                "win_rate_ci_low": r.win_rate_ci_low,
                "win_rate_ci_high": r.win_rate_ci_high,
                "avg_r_multiple": r.avg_r_multiple,
                "num_trades": r.num_trades,
                "sample_size_verdict": r.sample_size_verdict,
                "trades": trades,
            }
        )
    )


if __name__ == "__main__":
    symbol_arg = sys.argv[1] if len(sys.argv) > 1 else "BTC/USDT"
    asyncio.run(main(symbol_arg))
