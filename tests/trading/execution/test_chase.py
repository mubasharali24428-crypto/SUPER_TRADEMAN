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


# --- EX5: rate limiter gates EVERY order-touching action ---------------------


def _stale_order(cid, t0_ms, filled_qty=0.0):
    return WorkingOrderInfo(
        client_order_id=cid,
        symbol="BTC",
        side=Side.LONG,
        submitted_price=50000.0,
        stop_price=48000.0,
        requested_qty=1.0,
        filled_qty=filled_qty,
        last_fill_time_ms=t0_ms,
    )


@pytest.mark.asyncio
async def test_rate_limit_denial_skips_and_logs_instead_of_silent_action():
    """With an exhausted bucket, a stale order must be SKIPPED-and-logged --
    not acted on silently -- and stay tracked for a future scan."""
    venue = MockVenueAdapter()
    limiter = TokenBucketRateLimiter(capacity=1, refill_rate=0.0)  # one token, ever
    chaser = OrderChaser(venue_adapter=venue, chase_timeout_ms=5000.0, rate_limiter=limiter)

    t0_ms = 1000000.0
    t_now_ms = t0_ms + 6000.0
    info = _stale_order("cid_denied", t0_ms)
    chaser.register_order(info)

    # Burn the single token.
    assert await limiter.acquire() is True

    actions = await chaser.check_and_chase(current_time_ms=t_now_ms)

    assert actions == ["RATE_LIMIT_DENIED:cid_denied"]
    assert chaser.denied_actions == 1
    assert limiter.denied_count == 1
    # Nothing touched at the venue, and the order REMAINS tracked for retry.
    assert "cid_denied" not in venue.orders
    assert info.status == OrderState.SUBMITTED
    assert "cid_denied" in chaser.working_orders


@pytest.mark.asyncio
async def test_abandon_remainder_path_is_also_rate_limited():
    """The min-notional abandonment path used to bypass the limiter entirely;
    it must be gated like every other venue-touching action."""
    venue = MockVenueAdapter(
        instrument_info_map={"BTC": InstrumentInfo(symbol="BTC", min_notional=100.0, min_qty=0.01)}
    )
    limiter = TokenBucketRateLimiter(capacity=1, refill_rate=0.0)
    chaser = OrderChaser(venue_adapter=venue, chase_timeout_ms=5000.0, rate_limiter=limiter)

    t0_ms = 1000000.0
    info = _stale_order("cid_tiny", t0_ms, filled_qty=0.999)  # remainder below min_notional
    chaser.register_order(info)

    assert await limiter.acquire() is True  # exhaust the bucket

    actions = await chaser.check_and_chase(current_time_ms=t0_ms + 6000.0)

    assert actions == ["RATE_LIMIT_DENIED:cid_tiny"]
    assert chaser.denied_actions == 1
    assert info.status == OrderState.SUBMITTED, "denied abandonment must not finalize"
    assert "cid_tiny" not in venue.orders, "no venue call may happen on a denied acquire"
    assert "cid_tiny" in chaser.working_orders


@pytest.mark.asyncio
async def test_denied_order_is_retried_once_budget_returns():
    """Skip-and-log semantics: the SAME order gets its chase action on a later
    scan once the rate limiter has budget again."""
    venue = MockVenueAdapter()
    limiter = TokenBucketRateLimiter(capacity=1, refill_rate=0.0)
    chaser = OrderChaser(venue_adapter=venue, chase_timeout_ms=5000.0, rate_limiter=limiter)

    t0_ms = 1000000.0
    info = _stale_order("cid_retry", t0_ms)
    chaser.register_order(info)

    assert await limiter.acquire() is True  # exhaust
    first_actions = await chaser.check_and_chase(current_time_ms=t0_ms + 6000.0)
    assert first_actions == ["RATE_LIMIT_DENIED:cid_retry"]

    limiter.tokens = float(limiter.capacity)  # budget restored (e.g. next window)
    actions = await chaser.check_and_chase(current_time_ms=t0_ms + 12000.0)

    assert actions == ["REPRICED:cid_retry"]
    assert venue.orders["cid_retry"]["status"] == "canceled"


@pytest.mark.asyncio
async def test_exhausted_bucket_only_denies_second_order():
    """One token, two stale orders: exactly one action goes through; the other
    is explicitly denied (observable, not swallowed)."""
    venue = MockVenueAdapter()
    limiter = TokenBucketRateLimiter(capacity=1, refill_rate=0.0)
    chaser = OrderChaser(venue_adapter=venue, chase_timeout_ms=5000.0, rate_limiter=limiter)

    t0_ms = 1000000.0
    chaser.register_order(_stale_order("cid_a", t0_ms))
    chaser.register_order(_stale_order("cid_b", t0_ms))

    actions = await chaser.check_and_chase(current_time_ms=t0_ms + 6000.0)

    assert len(actions) == 2
    acted = [a for a in actions if a.startswith("REPRICED:")]
    denied = [a for a in actions if a.startswith("RATE_LIMIT_DENIED:")]
    assert len(acted) == 1 and len(denied) == 1
    assert chaser.denied_actions == 1
