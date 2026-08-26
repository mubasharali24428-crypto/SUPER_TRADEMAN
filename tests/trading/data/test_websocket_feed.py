"""Tests for websocket_feed — heartbeat buffer, watch/poll loops, backoff.

No network: fake exchanges raise/return canned data. Backoff timing uses
tiny start/max constants (fake clock): the same exponential schedule is
verified exactly by the pure ``compute_backoff`` unit tests.
"""

import asyncio
import time

import pytest

from trading.data.websocket_feed import (
    DEFAULT_BACKOFF_MAX_S,
    DEFAULT_BACKOFF_START_S,
    HeartbeatBuffer,
    compute_backoff,
    poll_ohlcv_loop,
    watch_ohlcv_loop,
)


# ----------------------------- backoff math ----------------------------- #

def test_compute_backoff_exponential_from_1s_capped_at_60s():
    assert compute_backoff(0, 1.0, 60.0) == 1.0
    assert compute_backoff(1, 1.0, 60.0) == 2.0
    assert compute_backoff(2, 1.0, 60.0) == 4.0
    assert compute_backoff(5, 1.0, 60.0) == 32.0
    assert compute_backoff(6, 1.0, 60.0) == 60.0      # 64 -> capped
    assert compute_backoff(20, 1.0, 60.0) == 60.0     # stays at cap
    # custom schedule used by the loop tests below
    assert compute_backoff(0, 0.01, 0.04) == 0.01
    assert compute_backoff(2, 0.01, 0.04) == 0.04


def test_default_schedule_is_mission_contract():
    assert DEFAULT_BACKOFF_START_S == 1.0
    assert DEFAULT_BACKOFF_MAX_S == 60.0


# --------------------------- heartbeat buffer --------------------------- #

def test_buffer_push_drain_roundtrip():
    b = HeartbeatBuffer()
    b.push("c1")
    b.extend(["c2", "c3"])
    assert len(b) == 3
    assert b.drain() == ["c1", "c2", "c3"]
    assert len(b) == 0


def test_buffer_bounded_drops_oldest_and_counts():
    b = HeartbeatBuffer(maxlen=2)
    b.push(1)
    b.push(2)
    b.push(3)                       # evicts 1
    assert b.drain() == [2, 3]
    assert b.dropped == 1


# ------------------------------ watch path ------------------------------ #

@pytest.mark.asyncio
async def test_watch_loop_feeds_buffer_and_stops_cleanly():
    class WatchExchange:
        def __init__(self):
            self.calls = 0

        async def watch_ohlcv(self, symbol, timeframe=None):
            self.calls += 1
            if self.calls == 1:
                return [[1000, 1, 2, 0.5, 1.5, 10]]
            raise RuntimeError("transport blew up")   # triggers reconnect path

    ex = WatchExchange()
    buf = HeartbeatBuffer()
    stop = asyncio.Event()

    async def stop_soon():
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.gather(
        watch_ohlcv_loop(
            ex, "BTC/USDT", "1m", buf,
            stop_event=stop,
            backoff_start_s=0.005, backoff_max_s=0.01, recv_timeout_s=0.2,
        ),
        stop_soon(),
    )
    assert buf.drain() == [[1000, 1, 2, 0.5, 1.5, 10]]
    assert ex.calls >= 2                    # received once, retried after failure


# --------------------------- polling fallback --------------------------- #

@pytest.mark.asyncio
async def test_exchange_without_watch_ohlcv_falls_back_to_polling():
    class PollOnlyExchange:
        def __init__(self):
            self.fetches = 0

        async def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
            self.fetches += 1
            stop.set()                          # shut down after first poll
            return [[1000 + i * 60_000, 1, 2, 0.5, 1.5, 10] for i in range(3)]

    ex = PollOnlyExchange()
    buf = HeartbeatBuffer()
    stop = asyncio.Event()

    await watch_ohlcv_loop(
        ex, "BTC/USDT", "1m", buf,
        stop_event=stop,
        poll_interval_s=0.001,
    )
    assert ex.fetches == 1
    # The last candle may still be forming; the loop buffers CLOSED candles.
    assert len(buf) == 2
    assert buf.drain() == [
        [1000, 1, 2, 0.5, 1.5, 10],
        [61000, 1, 2, 0.5, 1.5, 10],
    ]


@pytest.mark.asyncio
async def test_poll_loop_reconnects_with_exponential_backoff_fake_clock():
    """Failures back off 0.01s -> 0.02s -> ...; stop during backoff exits fast."""
    stop = asyncio.Event()
    attempt_times = []

    class FailingExchange:
        async def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
            attempt_times.append(time.monotonic())
            if len(attempt_times) >= 3:
                stop.set()                  # ask for shutdown mid-backoff
            raise ConnectionError("socket gone")

    t0 = time.monotonic()
    await poll_ohlcv_loop(
        FailingExchange(), "BTC/USDT", "1m", HeartbeatBuffer(),
        stop_event=stop,
        backoff_start_s=0.01, backoff_max_s=0.06, recv_timeout_s=0.5,
    )
    elapsed = time.monotonic() - t0

    assert len(attempt_times) == 3          # stop honored during 3rd backoff wait
    gap_1 = attempt_times[1] - attempt_times[0]
    gap_2 = attempt_times[2] - attempt_times[1]
    assert 0.008 <= gap_1 <= 0.08           # ~0.01s first backoff
    assert gap_2 >= 0.018                   # grew (~0.02s exponential step)
    assert elapsed <= 1.0                   # fake clock: nothing near the 60s cap


@pytest.mark.asyncio
async def test_poll_loop_recovers_after_transient_failures():
    stop = asyncio.Event()
    calls = {"n": 0}

    class RecoveringExchange:
        async def fetch_ohlcv(self, symbol, timeframe=None, since=None, limit=None):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise ConnectionError("flap")
            stop.set()                      # shut down after first success
            return [[1000, 1, 2, 0.5, 1.5, 10]]

    buf = HeartbeatBuffer()
    await poll_ohlcv_loop(
        RecoveringExchange(), "BTC/USDT", "1m", buf,
        stop_event=stop,
        backoff_start_s=0.005, backoff_max_s=0.01, recv_timeout_s=0.2,
    )
    assert calls["n"] == 3                  # two failures then success
    assert buf.drain() == [[1000, 1, 2, 0.5, 1.5, 10]]


@pytest.mark.asyncio
async def test_watch_recv_timeout_is_bounded_not_infinite():
    """A silent socket must not hang the loop: recv timeout fires, retry runs."""

    class SilentWatchExchange:
        def __init__(self):
            self.calls = 0

        async def watch_ohlcv(self, symbol, timeframe=None):
            self.calls += 1
            if self.calls >= 2:
                asyncio.get_running_loop().call_soon(lambda: None)
                await asyncio.sleep(5)      # hangs past recv_timeout_s
            return []

    ex = SilentWatchExchange()
    stop = asyncio.Event()

    async def stop_soon():
        await asyncio.sleep(0.15)
        stop.set()

    await asyncio.wait_for(
        asyncio.gather(
            watch_ohlcv_loop(
                ex, "BTC/USDT", "1m", HeartbeatBuffer(),
                stop_event=stop,
                backoff_start_s=0.005, backoff_max_s=0.01, recv_timeout_s=0.03,
            ),
            stop_soon(),
        ),
        timeout=3.0,
    )
    assert ex.calls >= 2                    # timeout fired, loop re-entered
