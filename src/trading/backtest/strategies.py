import random
from datetime import datetime, timezone
from trading.risk.models import Signal, Side


def generate_synthetic_candles(
    num: int,
    start_price: float = 100.0,
    volatility: float = 0.02,
    seed: int | None = 42,
) -> list[list[float]]:
    """Generates synthetic random-walk candles [ts_ms, open, high, low, close, volume]."""
    rng = random.Random(seed)
    candles = []
    ts0 = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    price = start_price
    for i in range(num):
        change = rng.uniform(-volatility, volatility)
        open_p = price
        high = price * (1 + max(change, 0) + 0.005)
        low = price * (1 + min(change, 0) - 0.005)
        close = price * (1 + change)
        candles.append([ts0 + i * 60_000, open_p, high, low, close, 0.0])
        price = close
    return candles


def make_momentum_strategy(target_mult: float = 0.03, stop_pct: float = 0.01, confidence: float = 0.9):
    """Creates a momentum-based signal generator with configurable target multiplier and stop."""
    def strategy(candles: list[list[float]], asset: str, dt: datetime) -> Signal | None:
        if len(candles) < 2:
            return None
        prev_close = candles[-2][4]
        cur_open = candles[-1][1]
        direction = Side.LONG if cur_open > prev_close else Side.SHORT
        entry = cur_open
        stop = entry * (1 - stop_pct) if direction is Side.LONG else entry * (1 + stop_pct)
        target = entry * (1 + target_mult) if direction is Side.LONG else entry * (1 - target_mult)
        return Signal(
            asset=asset,
            asset_class="crypto",
            side=direction,
            entry_price=entry,
            confidence=confidence,
            timestamp=dt,
            rationale="momentum",
            suggested_stop=stop,
            suggested_target=target,
        )
    return strategy
