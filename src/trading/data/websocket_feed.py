"""WebSocket OHLCV feed: ccxt.pro-style watch loop + heartbeat buffer.

Design (mission §P3-ALPHA):
* :func:`watch_ohlcv_loop` uses ``exchange.watch_ohlcv`` when the exchange
  object exposes it (ccxt.pro / async-support instances), else falls back to
  polling ``fetch_ohlcv`` at a fixed cadence.
* Reconnects use exponential backoff starting at ``backoff_start_s`` (1 s)
  capped at ``backoff_max_s`` (60 s), reset after a successful receive.
* Candles land in a bounded :class:`collections.deque` consumed by
  :func:`heartbeat_consume` — the heartbeat pattern: the consumer drains
  whatever is buffered on each beat; an empty buffer means "no data this
  beat", never an error.

All network waits carry explicit timeouts; cancellation is always honored.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any, Optional

__all__ = [
    "HeartbeatBuffer",
    "watch_ohlcv_loop",
    "poll_ohlcv_loop",
    "compute_backoff",
    "DEFAULT_BACKOFF_START_S",
    "DEFAULT_BACKOFF_MAX_S",
]

logger = logging.getLogger(__name__)

DEFAULT_BACKOFF_START_S = 1.0
DEFAULT_BACKOFF_MAX_S = 60.0


def compute_backoff(attempt: int, start_s: float, max_s: float) -> float:
    """Exponential backoff for ``attempt`` (0-based): min(start * 2**attempt, cap)."""
    if attempt < 0:
        attempt = 0
    return min(start_s * (2 ** attempt), max_s)


class HeartbeatBuffer:
    """Bounded deque of candles drained by the heartbeat consumer."""

    def __init__(self, maxlen: int = 1000) -> None:
        self._buf: deque = deque(maxlen=maxlen)
        self.dropped = 0  # candles shed by the bounded buffer

    def push(self, candle: Any) -> None:
        if len(self._buf) == self._buf.maxlen:
            self.dropped += 1
            logger.warning("heartbeat buffer full; dropping oldest candle (%d dropped)", self.dropped)
        self._buf.append(candle)

    def extend(self, candles) -> None:
        for c in candles or []:
            self.push(c)

    def drain(self) -> list:
        """Heartbeat consume: take everything currently buffered."""
        out = []
        while self._buf:
            out.append(self._buf.popleft())
        return out

    def __len__(self) -> int:
        return len(self._buf)


async def watch_ohlcv_loop(
    exchange,
    symbol: str,
    timeframe: str,
    buffer: HeartbeatBuffer,
    *,
    stop_event: Optional[asyncio.Event] = None,
    poll_interval_s: float = 5.0,
    recv_timeout_s: float = 30.0,
    backoff_start_s: float = DEFAULT_BACKOFF_START_S,
    backoff_max_s: float = DEFAULT_BACKOFF_MAX_S,
) -> None:
    """Watch OHLCV for ``symbol`` forever, feeding ``buffer``.

    Prefers ``exchange.watch_ohlcv`` (ccxt.pro); falls back to
    :func:`poll_ohlcv_loop` when absent. On any transport error, backs off
    exponentially (1s → 60s cap) and reconnects. Returns only when
    ``stop_event`` is set or the task is cancelled.
    """
    stop_event = stop_event or asyncio.Event()
    if not hasattr(exchange, "watch_ohlcv"):
        logger.info("%s lacks watch_ohlcv; using polling fallback", type(exchange).__name__)
        await poll_ohlcv_loop(
            exchange, symbol, timeframe, buffer,
            stop_event=stop_event,
            poll_interval_s=poll_interval_s,
            recv_timeout_s=recv_timeout_s,
            backoff_start_s=backoff_start_s,
            backoff_max_s=backoff_max_s,
        )
        return

    attempt = 0
    while not stop_event.is_set():
        try:
            candles = await asyncio.wait_for(
                exchange.watch_ohlcv(symbol, timeframe=timeframe),
                timeout=recv_timeout_s,
            )
            buffer.extend(candles)
            attempt = 0  # successful receive resets backoff
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport boundary: reconnect
            delay = compute_backoff(attempt, backoff_start_s, backoff_max_s)
            attempt += 1
            logger.warning(
                "watch_ohlcv %s %s failed (%s: %s); reconnect in %.1fs (attempt %d)",
                symbol, timeframe, type(exc).__name__, exc, delay, attempt,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                break  # stop requested during backoff sleep
            except asyncio.TimeoutError:
                pass


async def poll_ohlcv_loop(
    exchange,
    symbol: str,
    timeframe: str,
    buffer: HeartbeatBuffer,
    *,
    stop_event: Optional[asyncio.Event] = None,
    poll_interval_s: float = 5.0,
    recv_timeout_s: float = 30.0,
    backoff_start_s: float = DEFAULT_BACKOFF_START_S,
    backoff_max_s: float = DEFAULT_BACKOFF_MAX_S,
) -> None:
    """REST-polling fallback loop with the same reconnect/backoff contract."""
    stop_event = stop_event or asyncio.Event()
    since: Optional[int] = None
    attempt = 0
    while not stop_event.is_set():
        try:
            candles = await asyncio.wait_for(
                exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since),
                timeout=recv_timeout_s,
            )
            candles = candles or []
            if candles:
                # Advance the cursor past what we've seen; last candle may
                # still be forming, so re-fetch it next round.
                since = candles[-1][0]
                closed = candles[:-1] if len(candles) > 1 else []
                buffer.extend(closed if closed else candles)
            attempt = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport boundary: retry w/ backoff
            delay = compute_backoff(attempt, backoff_start_s, backoff_max_s)
            attempt += 1
            logger.warning(
                "fetch_ohlcv %s %s failed (%s: %s); retry in %.1fs (attempt %d)",
                symbol, timeframe, type(exc).__name__, exc, delay, attempt,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
            break
        except asyncio.TimeoutError:
            pass
