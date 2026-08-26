"""Tests for CcxtLiveAdapter — contract compliance + safety rails (no network).

Fake ccxt client: plain object with async methods recording calls. Exercises:
mode gating, dry-run no-submit, ApprovedOrder type assertion, quantization
call-through, retry-only-on-NetworkError, kill-switch pre-submit block, and
ccxt-exception -> OrderState mapping through the legal transition table.
"""

import asyncio
import logging

import ccxt
import pytest

from trading.config import ExecutionMode
from trading.execution.live_adapter import (
    CcxtLiveAdapter,
    KillSwitchActiveError,
    LiveModeError,
    OrderRejectedError,
    classify_ccxt_error,
    next_state_on_venue_error,
)
from trading.risk.models import AccountState, ApprovedOrder, Side, _ISSUER


# --------------------------------------------------------------------- #
# fakes                                                                 #
# --------------------------------------------------------------------- #

class FakeExchange:
    """Offline stand-in for a ccxt exchange instance."""

    def __init__(self):
        self.calls = []
        self.create_order_result = {"id": "ex-1", "clientOrderId": "cid-1", "status": "open"}
        self.markets = {
            "BTC/USDT": {
                "limits": {
                    "amount": {"min": 0.001},
                    "cost": {"min": 10.0},
                },
                "precision": {"amount": 0.0001, "price": 0.01},
            }
        }
        self.load_markets_called = 0

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.calls.append(("create_order", symbol, type_, side, amount, price, dict(params or {})))
        return dict(self.create_order_result)

    async def cancel_order(self, client_order_id, symbol):
        self.calls.append(("cancel_order", client_order_id, symbol))
        return {"clientOrderId": client_order_id, "status": "canceled"}

    async def fetch_order(self, cid, symbol, params=None):
        self.calls.append(("fetch_order", cid, symbol))
        return {"id": "ex-1", "clientOrderId": cid, "status": "closed", "filled": 1.0}

    async def fetch_open_orders(self, symbol=None):
        self.calls.append(("fetch_open_orders", symbol))
        return [{"id": "ex-1", "clientOrderId": "cid-1", "symbol": symbol, "status": "open"}]

    async def fetch_balance(self):
        self.calls.append(("fetch_balance",))
        return {
            "free": {"BTC": 0.5},
            "used": {"BTC": 0.25},
            "total": {"BTC": 0.75},
        }

    async def load_markets(self, reload=False):
        self.load_markets_called += 1
        return self.markets

    def market(self, symbol):
        return self.markets.get(symbol)


class FlakyNetworkExchange(FakeExchange):
    """create_order raises NetworkError twice then succeeds."""

    def __init__(self, fail_times=2, exc=None):
        super().__init__()
        self.fail_times = fail_times
        self.exc = exc or ccxt.NetworkError("connection reset")
        self.attempts = 0

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise self.exc
        return await super().create_order(symbol, type_, side, amount, price, params)


# --------------------------------------------------------------------- #
# helpers                                                               #
# --------------------------------------------------------------------- #

def make_order(side=Side.LONG, size=0.123456, price=50_000.1234):
    from trading.risk.models import ApprovedOrder

    return ApprovedOrder(
        asset="BTC/USDT",
        asset_class="crypto",
        side=side,
        entry_price=price,
        stop_price=48_000.0,
        target_price=55_000.0,
        position_size=size,
        risk_pct=0.01,
        issuer=_ISSUER,
    )


def make_exit():
    from trading.risk.models import ApprovedExit

    return ApprovedExit(asset="BTC/USDT", asset_class="crypto", reason="test exit", issuer=_ISSUER)


def live_adapter(exchange=None, **kw):
    kw.setdefault("execution_mode", ExecutionMode.LIVE_RESTRICTED)
    kw.setdefault("dry_run", False)
    kw.setdefault("api_key", "k")
    kw.setdefault("secret", "s")
    kw.setdefault("call_timeout_s", 1.0)
    adapter = CcxtLiveAdapter(**kw)
    if exchange is not None:
        adapter._exchange = exchange
    return adapter


def dry_adapter(**kw):
    kw.setdefault("execution_mode", ExecutionMode.PAPER)
    kw.setdefault("dry_run", True)
    return CcxtLiveAdapter(**kw)


def account(kill=False):
    return AccountState(equity=100_000.0, peak_equity=110_000.0, kill_switch=kill)


# --------------------------------------------------------------------- #
# mode gating                                                           #
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_constructor_refuses_non_live_mode_without_dry_run():
    for mode in (ExecutionMode.BACKTEST, ExecutionMode.PAPER, ExecutionMode.SHADOW):
        with pytest.raises(LiveModeError):
            CcxtLiveAdapter(execution_mode=mode, dry_run=False, api_key="k", secret="s")


@pytest.mark.asyncio
async def test_constructor_allows_non_live_mode_with_dry_run_true():
    adapter = dry_adapter()
    assert adapter.dry_run is True


@pytest.mark.asyncio
async def test_live_mode_requires_credentials():
    with pytest.raises(LiveModeError):
        CcxtLiveAdapter(
            execution_mode=ExecutionMode.LIVE_FULL, dry_run=False,
            api_key=None, secret=None,
        )


@pytest.mark.asyncio
async def test_live_modes_accepted():
    for mode in (ExecutionMode.LIVE_RESTRICTED, ExecutionMode.LIVE_FULL):
        a = live_adapter(execution_mode=mode)
        assert a.mode is mode


# --------------------------------------------------------------------- #
# dry-run: log instead of submit                                        #
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_dry_run_logs_and_never_submits(caplog):
    fake = FakeExchange()
    a = dry_adapter(exchange=fake)
    caplog.set_level(logging.WARNING, logger="trading.execution.live_adapter")

    resp = await a.create_order(make_order(), "cid-dry-1")

    assert resp["dry_run"] is True
    assert fake.calls == []                      # NOTHING reached the exchange
    assert any("[DRY-RUN SUBMISSION]" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_dry_run_create_exit_and_cancel_never_touch_exchange():
    fake = FakeExchange()
    a = dry_adapter(exchange=fake)
    e = await a.create_exit(make_exit(), "cid-dry-e")
    c = await a.cancel_order("cid-dry-c", "BTC/USDT")
    assert e["dry_run"] and c["dry_run"]
    assert fake.calls == []


# --------------------------------------------------------------------- #
# approved-order gate                                                   #
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_create_order_rejects_non_approved_types():
    a = live_adapter(exchange=FakeExchange())

    class ImpostorOrder(ApprovedOrder):
        pass

    # Even a subclass minted with the real issuer token is refused: the
    # adapter accepts EXACTLY ApprovedOrder, nothing else.
    subclass_instance = ImpostorOrder(
        asset="BTC/USDT", asset_class="crypto", side=Side.LONG,
        entry_price=50_000.0, stop_price=48_000.0, target_price=55_000.0,
        position_size=0.1, risk_pct=0.01, issuer=_ISSUER,
    )
    with pytest.raises(OrderRejectedError):
        await a.create_order(subclass_instance, "cid-sub")
    with pytest.raises(OrderRejectedError):
        await a.create_order({"asset": "BTC/USDT"}, "cid-y")   # raw dict
    with pytest.raises(OrderRejectedError):
        await a.create_order(None, "cid-z")                    # None
    # ...and a genuine ApprovedOrder still goes through.
    ok = await a.create_order(make_order(), "cid-ok")
    assert ok["id"]


@pytest.mark.asyncio
async def test_create_exit_rejects_plain_objects():
    a = live_adapter(exchange=FakeExchange())
    with pytest.raises(OrderRejectedError):
        await a.create_exit(object(), "cid-q")


# --------------------------------------------------------------------- #
# kill switch honored pre-submit                                        #
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_kill_switch_blocks_submission_before_any_exchange_call():
    fake = FakeExchange()
    a = live_adapter(exchange=fake)
    a.account_state.kill_switch = True
    with pytest.raises(KillSwitchActiveError):
        await a.create_order(make_order(), "cid-k")
    assert fake.calls == []          # blocked BEFORE any venue traffic
    a.account_state.kill_switch = False
    await a.create_order(make_order(), "cid-k2")   # now goes through
    assert len(fake.calls) == 1


# --------------------------------------------------------------------- #
# quantization call-through                                             #
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_quantity_and_price_quantized_via_instrument_info_steps():
    fake = FakeExchange()
    a = live_adapter(exchange=fake)
    order = make_order(size=0.123456789, price=50_000.1289)

    await a.create_order(order, "cid-q")

    kind, symbol, type_, side, qty, px, params = fake.calls[0]
    assert qty == 0.1234                       # step 0.0001, rounded DOWN
    assert px == 50_000.12                     # precision 0.01, rounded DOWN
    assert params.get("newClientOrderId") == "cid-q"


@pytest.mark.asyncio
async def test_min_notional_violation_rejected_after_quantization():
    fake = FakeExchange()
    fake.markets["BTC/USDT"]["limits"]["cost"]["min"] = 10_000_000.0   # absurd floor
    a = live_adapter(exchange=fake)
    with pytest.raises(OrderRejectedError):
        await a.create_order(make_order(size=0.5), "cid-mn")


# --------------------------------------------------------------------- #
# retry policy: NetworkError yes / RateLimit NO                         #
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_network_error_is_retried_then_succeeds(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
    fake = FlakyNetworkExchange(fail_times=2)
    a = live_adapter(exchange=fake)
    resp = await a.create_order(make_order(), "cid-r")
    assert resp["id"] == "ex-1"
    assert fake.attempts == 3                  # 2 failures + 1 success


@pytest.mark.asyncio
async def test_rate_limit_never_retried_raised_to_caller(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
    fake = FlakyNetworkExchange(exc=ccxt.RateLimitExceeded("breathe"))
    a = live_adapter(exchange=fake)
    with pytest.raises(ccxt.RateLimitExceeded):
        await a.create_order(make_order(), "cid-rl")
    assert fake.attempts == 1                  # exactly one attempt — no retry


@pytest.mark.asyncio
async def test_network_error_exhausts_retries_and_raises(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
    fake = FlakyNetworkExchange(fail_times=99)
    a = live_adapter(exchange=fake)
    with pytest.raises(ccxt.NetworkError):
        await a.create_order(make_order(), "cid-ex")
    assert fake.attempts == 4                  # 1 + _MAX_RETRIES(3)


async def _noop_sleep(_delay, *a, **kw):       # fake clock: instant backoff
    return None


# --------------------------------------------------------------------- #
# timeout on every call                                                 #
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_timeout_wraps_every_venue_call():
    class Slow(FakeExchange):
        async def create_order(self, *a, **kw):
            self.calls.append(("slow",))
            await asyncio.sleep(5)          # real sleep: exceeds call_timeout_s
            return {}

    a = live_adapter(exchange=Slow(), call_timeout_s=0.05)
    with pytest.raises(asyncio.TimeoutError):
        await a.create_order(make_order(), "cid-slow")
    # every attempt was individually bounded by call_timeout_s
    from trading.execution.live_adapter import _MAX_RETRIES

    assert len(a._exchange.calls) == 1 + _MAX_RETRIES


@pytest.mark.asyncio
async def test_asyncio_timeout_mapped_to_unknown_not_retry_terminal(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    class HangThenTimeout(FakeExchange):
        async def create_order(self, *a, **kw):
            raise asyncio.TimeoutError()

    a = live_adapter(exchange=HangThenTimeout())
    with pytest.raises(asyncio.TimeoutError):
        await a.create_order(make_order(), "cid-t")
    from trading.execution.state_machine import OrderState

    assert classify_ccxt_error(asyncio.TimeoutError()) == OrderState.UNKNOWN


# --------------------------------------------------------------------- #
# exception -> OrderState via legal transition table                     #
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_classify_maps_ccxt_exceptions_to_states():
    from trading.execution.state_machine import OrderState

    # RateLimit IS-A NetworkError in ccxt>=4 but must classify as UNKNOWN,
    # never as a retryable-transient terminal state.
    assert classify_ccxt_error(ccxt.RateLimitExceeded("rl")) == OrderState.UNKNOWN
    assert classify_ccxt_error(ccxt.NetworkError("net")) == OrderState.UNKNOWN
    assert classify_ccxt_error(ccxt.InsufficientFunds("funds")) == OrderState.REJECTED
    assert classify_ccxt_error(ccxt.InvalidOrder("bad")) == OrderState.REJECTED
    assert classify_ccxt_error(ccxt.OrderNotFound("gone")) == OrderState.EXPIRED
    assert classify_ccxt_error(ccxt.ExchangeNotAvailable("down")) == OrderState.UNKNOWN


@pytest.mark.asyncio
async def test_next_state_routes_through_legal_table():
    from trading.execution.state_machine import OrderState

    # InsufficientFunds on a SUBMITTED order -> REJECTED (legal transition).
    s = next_state_on_venue_error(OrderState.SUBMITTED, ccxt.InsufficientFunds("x"))
    assert s is OrderState.REJECTED
    # Network error on CREATED -> QUARANTINED per LEGAL_TRANSITIONS.
    s = next_state_on_venue_error(OrderState.CREATED, ccxt.NetworkError("x"))
    assert s is OrderState.QUARANTINED
    # Terminal states stay put regardless of classification.
    s = next_state_on_venue_error(OrderState.FILLED, ccxt.InsufficientFunds("x"))
    assert s is OrderState.FILLED


# --------------------------------------------------------------------- #
# contract compliance vs VenueAdapter ABC                               #
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_contract_compliance_every_abstract_method_implemented():
    a = live_adapter(exchange=FakeExchange())
    # Instantiation proves all abstractmethods are implemented; exercise each.
    o = await a.create_order(make_order(), "c1")
    e = await a.create_exit(make_exit(), "c2")
    c = await a.cancel_order("c1", "BTC/USDT")
    f = await a.fetch_order("c1", "BTC/USDT")
    oo = await a.fetch_open_orders("BTC/USDT")
    p = await a.fetch_positions()
    i = await a.get_instrument_info("BTC/USDT")
    assert o["id"] and e["id"] and c["status"] == "canceled"
    assert f["status"] == "closed" and isinstance(oo, list) and isinstance(p, list)
    assert i.symbol == "BTC/USDT" and i.step_size == 0.0001 and i.price_precision == 2


@pytest.mark.asyncio
async def test_get_instrument_info_from_fake_markets_dry_run_offline_defaults():
    # Dry-run, no exchange injected: instrument info comes from offline
    # defaults and NO exchange is ever built (proven below).
    a = dry_adapter()
    info = await a.get_instrument_info("ETH/USDT")
    assert info == info.__class__(symbol="ETH/USDT")
    assert a._exchange is None
    # Live-mode adapter backed by the injected fake reads real market structure
    # (tick-size precision convention: 0.0001 -> step, 0.01 price tick).
    info2 = await live_adapter(exchange=FakeExchange()).get_instrument_info("BTC/USDT")
    assert info2.min_qty == 0.001 and info2.min_notional == 10.0
    assert info2.step_size == 0.0001 and info2.price_precision == 2
