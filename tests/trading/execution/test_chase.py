"""Tests for OrderChaser daemon and TokenBucketRateLimiter."""

import asyncio
import time
import pytest

from trading.execution.chase import OrderChaser, TokenBucketRateLimiter, WorkingOrderInfo
from trading.execution.state_machine import OrderState
from trading.execution.venue_adapter import InstrumentInfo, MockVenueAdapter
from trading.risk.models import Side


@pytest.mark.asyncio
async def test_token_bucket_rate_limiter():
    limiter = TokenBucketRateLimiter(capacity=2, refill_rate=1.0)
    assert await limiter.acquire()
    assert await limiter.acquire()
    # 3rd acquire in same second should fail
    assert not await limiter.acquire()


@pytest.mark.asyncio
async def test_chase_order_reprice_stale_order():
    venue = MockVenueAdapter()
    chaser = OrderChaser(venue_adapter=venue, chase_timeout_ms=5000.0)

    # Working order submitted 6000ms ago (stale)
    t0_ms = 1000000.0
    t_now_ms = t0_ms + 6000.0

    order_info = WorkingOrderInfo(
        client_order_id="cid_chase_1",
        symbol="BTC",
        side=Side.LONG,
        submitted_price=50000.0,
        stop_price=48000.0,
        requested_qty=1.0,  # 1.0 BTC ($50,000 > min_notional $10)
        filled_qty=0.0,
        last_fill_time_ms=t0_ms,
    )
    chaser.register_order(order_info)

    actions = await chaser.check_and_chase(current_time_ms=t_now_ms)
    assert len(actions) == 1
    assert actions[0] == "REPRICED:cid_chase_1"
    assert venue.orders["cid_chase_1"]["status"] == "canceled"


@pytest.mark.asyncio
async def test_chase_abandon_remainder_below_min_notional():
    venue = MockVenueAdapter(
        instrument_info_map={"BTC": InstrumentInfo(symbol="BTC", min_notional=100.0, min_qty=0.01)}
    )
    chaser = OrderChaser(venue_adapter=venue, chase_timeout_ms=5000.0)

    t0_ms = 1000000.0
    t_now_ms = t0_ms + 6000.0

    # Remaining quantity 0.001 BTC @ $50,000 = $50 (< $100 min_notional)
    order_info = WorkingOrderInfo(
        client_order_id="cid_small_remainder",
        symbol="BTC",
        side=Side.LONG,
        submitted_price=50000.0,
        stop_price=48000.0,
        requested_qty=1.0,
        filled_qty=0.999,  # remaining 0.001 BTC
        last_fill_time_ms=t0_ms,
    )
    chaser.register_order(order_info)

    actions = await chaser.check_and_chase(current_time_ms=t_now_ms)
    assert len(actions) == 1
    assert actions[0] == "ABANDONED:cid_small_remainder"
    assert order_info.status == OrderState.PARTIAL_FILL_FINALIZED
