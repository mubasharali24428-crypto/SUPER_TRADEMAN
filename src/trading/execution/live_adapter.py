"""CcxtLiveAdapter — real venue connectivity behind the VenueAdapter ABC.

Safety rails (mission §P3-ALPHA):
* Mode gating: construction refuses any ExecutionMode outside
  (LIVE_RESTRICTED, LIVE_FULL) unless ``dry_run=True``.
* Approved-only: every submission must arrive as an ``ApprovedOrder``
  (resp. ``ApprovedExit``) minted by the Risk Engine; the adapter NEVER
  constructs orders itself. Non-approved types are rejected outright.
* Order-size clamp: quantity/price are quantized DOWN onto the venue's grid
  via :func:`trading.core.money.quantize_to_step` using limits from
  ``get_instrument_info`` (ccxt markets), before any submission.
* Kill-switch honored pre-submit: a tripped kill-switch blocks submission.
* Timeout on every venue call; retry ONLY on ccxt NetworkError-class errors;
  RateLimit errors are re-raised to the caller immediately (no retry).
* ccxt exceptions map to OrderState transitions via the existing
  state_machine legal-transition table.

Secrets come only from Settings/env — no credential literals live here.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Dict, Optional

import ccxt

from trading.config import ExecutionMode, Settings
from trading.core.money import check_min_notional, quantize_to_step
from trading.execution.state_machine import (IllegalTransitionError,
                                             OrderEventType, OrderState,
                                             transition_order_state)
from trading.execution.venue_adapter import InstrumentInfo, VenueAdapter
from trading.risk.models import AccountState, ApprovedExit, ApprovedOrder, Side

__all__ = [
    "LiveModeError",
    "KillSwitchActiveError",
    "OrderRejectedError",
    "CcxtLiveAdapter",
    "classify_ccxt_error",
    "error_event",
    "next_state_on_venue_error",
]

logger = logging.getLogger(__name__)

#: ExecutionModes under which real submissions are permitted at all.
LIVE_MODES = frozenset({ExecutionMode.LIVE_RESTRICTED, ExecutionMode.LIVE_FULL})

_RETRYABLE_NETWORK_ERRORS: tuple = (
    ccxt.NetworkError,
    asyncio.TimeoutError,
    ConnectionError,
)
_MAX_RETRIES = 3  # total attempts = 1 + _MAX_RETRIES
_BACKOFF_BASE_S = 0.25  # 0.25 -> 0.5 -> 1.0 between attempts


class LiveModeError(RuntimeError):
    """Raised when the adapter is constructed in a mode that forbids it."""


class KillSwitchActiveError(RuntimeError):
    """Raised pre-submit when the risk account's kill switch is tripped."""


class OrderRejectedError(RuntimeError):
    """Raised when a submission arrives that is not Risk-Engine approved."""


def classify_ccxt_error(exc: BaseException) -> OrderState:
    """Map a ccxt exception to the OrderState its event implies.

    Uses the existing state-machine legal table so the caller can drive
    ``transition_order_state`` with the returned target state. Rate-limit and
    transient-network failures do NOT imply a terminal order state (the order
    outcome is unknown → UNKNOWN); exchange-declared rejections/expiry do.
    """
    if isinstance(
        exc, ccxt.RateLimitExceeded
    ):  # IS-A NetworkError in ccxt >=4; check FIRST
        return OrderState.UNKNOWN
    if isinstance(exc, ccxt.NetworkError):  # timeout / not available / etc.
        return OrderState.UNKNOWN
    if isinstance(exc, ccxt.InsufficientFunds):
        return OrderState.REJECTED
    if isinstance(exc, ccxt.OrderNotFound):  # IS-A InvalidOrder: check FIRST
        return OrderState.EXPIRED
    if isinstance(exc, ccxt.InvalidOrder):
        return OrderState.REJECTED
    if isinstance(exc, ccxt.ExchangeError):
        return OrderState.REJECTED
    return OrderState.UNKNOWN


def error_event(state: OrderState) -> OrderEventType:
    """The OrderEventType that carries ``state``'s meaning (REJECTED→REJECTED,
    EXPIRED→EXPIRY, QUARANTINED→QUARANTINED, CANCELED→CANCEL, UNKNOWN→QUARANTINED)."""
    mapping = {
        OrderState.REJECTED: OrderEventType.REJECTED,
        OrderState.EXPIRED: OrderEventType.EXPIRY,
        OrderState.CANCELED: OrderEventType.CANCEL,
        OrderState.UNKNOWN: OrderEventType.QUARANTINED,
    }
    return mapping[state]


def next_state_on_venue_error(
    current_state: OrderState, exc: BaseException
) -> OrderState:
    """Route a venue exception through the legal transition table.

    Returns the deterministic next state after applying the event implied by
    ``exc`` to ``current_state``, exactly as :func:`transition_order_state`
    computes it. UNKNOWN-class errors (rate limit / network) quarantine from
    any live state per LEGAL_TRANSITIONS.
    """
    target_state = classify_ccxt_error(exc)
    try:
        return transition_order_state(
            current_state,
            error_event(target_state),
        )
    except IllegalTransitionError:
        # e.g. a REJECTED event against an already-terminal order: keep the
        # terminal state — the table wins over our classification.
        return current_state


class CcxtLiveAdapter(VenueAdapter):
    """VenueAdapter implementation backed by a lazily-created ccxt exchange."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        *,
        execution_mode: Optional[ExecutionMode] = None,
        dry_run: Optional[bool] = None,
        api_key: Optional[str] = None,
        secret: Optional[str] = None,
        password: Optional[str] = None,
        exchange_id: Optional[str] = None,
        exchange=None,  # optional pre-built ccxt exchange instance
        call_timeout_s: float = 10.0,
        account_state: Optional[AccountState] = None,
    ) -> None:
        s = settings or Settings()  # type: ignore[call-arg]
        self.mode = execution_mode or s.execution_mode
        self.dry_run = bool(s.dry_run if dry_run is None else dry_run)
        self.exchange_id = exchange_id or s.exchange_id
        self.call_timeout_s = float(call_timeout_s)
        # Shared risk-account view for pre-submit kill-switch checks. The
        # caller may pass one to keep it fresh; otherwise a private default
        # stands in (kill switch never tripped).
        self.account_state = (
            account_state
            if account_state is not None
            else AccountState(equity=0.0, peak_equity=0.0)
        )

        if self.mode not in LIVE_MODES and not self.dry_run:
            raise LiveModeError(
                f"CcxtLiveAdapter refuses construction: execution_mode "
                f"'{self.mode.value}' is not in "
                f"(live_restricted, live_full); use dry_run=True for paper logging."
            )
        if not self.dry_run:
            key = api_key or s.venue_api_key
            sec = secret or s.venue_api_secret
            pwd = password or s.venue_password
            missing = [
                name
                for name, val in (("venue_api_key", key), ("venue_api_secret", sec))
                if not val
            ]
            if missing:
                raise LiveModeError(f"missing venue credentials: {', '.join(missing)}")
            self._creds = {"apiKey": key, "secret": sec, "password": pwd}
        else:
            self._creds: Dict[str, Optional[str]] = {}

        self._exchange = exchange  # injected instance wins when provided
        self._instrument_cache: Dict[str, InstrumentInfo] = {}
        self.calls: list[
            Dict[str, Any]
        ] = []  # audit trail of would-be/real submissions

    # ------------------------------------------------------------------ #
    # internals                                                          #
    # ------------------------------------------------------------------ #

    def _get_exchange(self):
        """Return the ccxt exchange, building it lazily on first real use."""
        if self._exchange is None:
            if self.dry_run:
                # Dry-run must stay truly offline: no network, no exchange build.
                raise LiveModeError("dry-run adapter has no exchange instance")
            klass = getattr(ccxt, self.exchange_id, None)
            if klass is None:
                raise LiveModeError(f"unknown exchange_id '{self.exchange_id}'")
            self._exchange = klass(
                {
                    "apiKey": self._creds.get("apiKey"),
                    "secret": self._creds.get("secret"),
                    "password": self._creds.get("password"),
                    "enableRateLimit": True,
                }
            )
            logger.info(
                "built ccxt %s %s", self.exchange_id, getattr(ccxt, "__version__", "?")
            )
        return self._exchange

    def _check_kill_switch(self) -> None:
        if self.account_state.kill_switch:
            raise KillSwitchActiveError(
                "kill switch tripped: submission blocked pre-submit"
            )

    @staticmethod
    def _assert_approved(order: Any) -> ApprovedOrder:
        # Exact type assert: subclasses/dicts/plain dataclasses are rejected.
        if type(order) is not ApprovedOrder:
            raise OrderRejectedError(
                f"create_order requires a Risk-Engine ApprovedOrder, got {type(order)!r}"
            )
        return order

    @staticmethod
    def _assert_approved_exit(exit_order: Any) -> ApprovedExit:
        if not isinstance(exit_order, ApprovedExit):
            raise OrderRejectedError(
                f"create_exit requires a Risk-Engine ApprovedExit, got {type(exit_order)!r}"
            )
        return exit_order

    async def _call(self, method_name: str, *args, **kwargs) -> Any:
        """Run one venue call with hard timeout; retry NetworkError-class only.

        RateLimitExceeded subclasses NetworkError in ccxt>=4 but is checked
        FIRST and re-raised immediately to the caller (no retry).
        """
        ex = self._get_exchange()
        last_exc: BaseException | None = None
        for attempt in range(1 + _MAX_RETRIES):
            try:
                coro = getattr(ex, method_name)(*args, **kwargs)
                return await asyncio.wait_for(coro, timeout=self.call_timeout_s)
            except ccxt.RateLimitExceeded:
                raise  # never retry rate limits
            except _RETRYABLE_NETWORK_ERRORS as exc:
                last_exc = exc
                delay = _BACKOFF_BASE_S * (2**attempt)
                logger.warning(
                    "network error on %s (attempt %d/%d): %s; backing off %.2fs",
                    method_name,
                    attempt + 1,
                    1 + _MAX_RETRIES,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def _quantized(
        self, symbol: str, quantity: float, price: Optional[float]
    ) -> tuple[float, Optional[float]]:
        info = await self.get_instrument_info(symbol)
        qty_d = quantize_to_step(quantity, info.step_size)
        price_d: Optional[float] = None
        if price is not None:
            step_price = 10**-info.price_precision
            price_d = float(quantize_to_step(price, step_price))
        return float(qty_d), price_d

    # ------------------------------------------------------------------ #
    # VenueAdapter contract                                              #
    # ------------------------------------------------------------------ #

    async def create_order(
        self, order: ApprovedOrder, client_order_id: str
    ) -> Dict[str, Any]:
        order = self._assert_approved(order)
        self._check_kill_switch()
        symbol = order.asset

        if self.dry_run:
            entry = {
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": order.side.value,
                "type": "limit",
                "qty": order.position_size,
                "price": order.entry_price,
                "dry_run": True,
            }
            self.calls.append(entry)
            logger.warning("[DRY-RUN SUBMISSION] would submit %s", entry)
            return {
                "id": f"dryrun_{client_order_id}",
                "clientOrderId": client_order_id,
                "status": "dry_run",
                "dry_run": True,
            }

        qty, px = await self._quantized(symbol, order.position_size, order.entry_price)
        info = await self.get_instrument_info(symbol)
        if not check_min_notional(qty, px or order.entry_price, info.min_notional):
            raise OrderRejectedError(
                f"{symbol}: quantized notional {qty} x {px} < min_notional {info.min_notional}"
            )

        side = "buy" if order.side is Side.LONG else "sell"
        params: Dict[str, Any] = {"newClientOrderId": client_order_id}
        try:
            resp = await self._call(
                "create_order", symbol, "limit", side, qty, px, params
            )
        except ccxt.RateLimitExceeded:
            raise  # propagate untouched to caller
        except ccxt.BaseError as exc:
            logger.error(
                "venue rejected %s: %s -> %s",
                client_order_id,
                exc,
                classify_ccxt_error(exc),
            )
            raise
        return {
            "id": resp.get("id"),
            "clientOrderId": resp.get("clientOrderId", client_order_id),
            "status": resp.get("status", "submitted"),
            "raw": resp,
        }

    async def create_exit(
        self, exit_order: ApprovedExit, client_order_id: str
    ) -> Dict[str, Any]:
        exit_order = self._assert_approved_exit(exit_order)
        self._check_kill_switch()
        symbol = exit_order.asset

        if self.dry_run:
            entry = {
                "client_order_id": client_order_id,
                "symbol": symbol,
                "reason": exit_order.reason,
                "type": "market",
                "side": "close",
                "reduce_only": True,
                "dry_run": True,
            }
            self.calls.append(entry)
            logger.warning(
                "[DRY-RUN EXIT] would submit close for %s (%s)",
                symbol,
                exit_order.reason,
            )
            return {
                "id": f"dryrun_exit_{client_order_id}",
                "clientOrderId": client_order_id,
                "status": "dry_run",
                "dry_run": True,
            }

        # ApprovedExit carries no quantity/price by design: default is a
        # reduce-only market close of the venue-held balance. Explicit
        # overrides remain possible via subclass/params without changing the
        # ABC signature.
        params: Dict[str, Any] = {
            "newClientOrderId": client_order_id,
            "reduceOnly": True,
        }
        try:
            resp = await self._call(
                "create_order", symbol, "market", "sell", None, None, params
            )
        except ccxt.RateLimitExceeded:
            raise
        except ccxt.BaseError as exc:
            logger.error(
                "exit failed %s: %s -> %s",
                client_order_id,
                exc,
                classify_ccxt_error(exc),
            )
            raise
        return {
            "id": resp.get("id"),
            "clientOrderId": resp.get("clientOrderId", client_order_id),
            "status": resp.get("status", "closed"),
            "raw": resp,
        }

    async def cancel_order(self, client_order_id: str, symbol: str) -> Dict[str, Any]:
        if self.dry_run:
            entry = {
                "client_order_id": client_order_id,
                "symbol": symbol,
                "op": "cancel",
                "dry_run": True,
            }
            self.calls.append(entry)
            logger.warning(
                "[DRY-RUN CANCEL] would cancel %s on %s", client_order_id, symbol
            )
            return {
                "clientOrderId": client_order_id,
                "status": "canceled",
                "dry_run": True,
            }

        resp = await self._call("cancel_order", client_order_id, symbol)
        return {
            "clientOrderId": resp.get("clientOrderId", client_order_id),
            "status": resp.get("status", "canceled"),
            "raw": resp,
        }

    async def fetch_order(
        self, client_order_id: str, symbol: str
    ) -> Optional[Dict[str, Any]]:
        if self.dry_run:
            entry = {
                "client_order_id": client_order_id,
                "symbol": symbol,
                "op": "fetch_order",
                "dry_run": True,
            }
            self.calls.append(entry)
            logger.warning("[DRY-RUN FETCH_ORDER] %s", entry)
            return {
                "clientOrderId": client_order_id,
                "status": "dry_run",
                "dry_run": True,
            }

        try:
            resp = await self._call(
                "fetch_order",
                client_order_id,
                symbol,
                params={"origClientOrderId": client_order_id},
            )
        except ccxt.OrderNotFound:
            return None
        if not resp:
            return None
        return {
            "id": resp.get("id"),
            "clientOrderId": resp.get("clientOrderId") or client_order_id,
            "status": resp.get("status"),
            "filled": resp.get("filled"),
            "average": resp.get("average"),
            "raw": resp,
        }

    async def fetch_open_orders(
        self, symbol: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        if self.dry_run:
            self.calls.append(
                {"op": "fetch_open_orders", "symbol": symbol, "dry_run": True}
            )
            logger.warning("[DRY-RUN FETCH_OPEN_ORDERS] symbol=%s", symbol)
            return []
        resp_list = await self._call("fetch_open_orders", symbol)
        return [
            {
                "id": r.get("id"),
                "clientOrderId": r.get("clientOrderId"),
                "symbol": r.get("symbol"),
                "status": r.get("status"),
                "raw": r,
            }
            for r in (resp_list or [])
        ]

    async def fetch_positions(self) -> list[Dict[str, Any]]:
        if self.dry_run:
            self.calls.append({"op": "fetch_positions", "dry_run": True})
            logger.warning("[DRY-RUN FETCH_POSITIONS]")
            return []
        balances = await self._call("fetch_balance")
        positions = []
        for code, bal in (balances.get("total") or {}).items():
            if bal and bal > 0:
                positions.append(
                    {
                        "asset": code,
                        "size": bal,
                        "free": (balances.get("free") or {}).get(code),
                        "used": (balances.get("used") or {}).get(code),
                    }
                )
        return positions

    async def get_instrument_info(self, symbol: str) -> InstrumentInfo:
        cached = self._instrument_cache.get(symbol)
        if cached is not None:
            return cached
        if self.dry_run and self._exchange is None:
            # Offline defaults keep dry-run usable without any market load.
            info = InstrumentInfo(symbol=symbol)
            self._instrument_cache[symbol] = info
            return info
        ex = self._get_exchange()
        await self._call("load_markets")
        market = ex.market(symbol)
        if market is None:
            raise LiveModeError(f"unknown market {symbol!r} on {self.exchange_id}")
        limits = market.get("limits") or {}
        amount_limits = limits.get("amount") or {}
        cost_limits = limits.get("cost") or {}
        precision = market.get("precision") or {}
        amount_prec = precision.get("amount")
        if (
            isinstance(amount_prec, (int,))
            and not isinstance(amount_prec, bool)
            and amount_prec > 0
        ):
            # decimal-places convention: 4 -> step 0.0001
            step_size = 10**-amount_prec
        else:
            # tick-size convention (float): 0.0001 IS the step
            step_size = amount_prec or amount_limits.get("step") or 1e-4
        price_prec = precision.get("price", 2)
        price_tick = (
            10**-price_prec
            if isinstance(price_prec, int) and not isinstance(price_prec, bool)
            else price_prec
        ) or 10**-2
        info = InstrumentInfo(
            symbol=symbol,
            min_qty=float(amount_limits.get("min", 0.001)),
            min_notional=float(cost_limits.get("min", 10.0)),
            step_size=float(step_size),
            price_precision=int(round(-math.log10(price_tick))),
        )
        self._instrument_cache[symbol] = info
        return info
