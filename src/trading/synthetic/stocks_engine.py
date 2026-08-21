"""US Equities Regulatory Shield, Reg NMS Trade-Through, LULD State Machine, and Short Sale Restriction (SSR)."""

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from trading.observability.logger import get_logger

__all__ = [
    "SIPNBBO",
    "DirectFeedQuote",
    "SIPDirectFeedReconciler",
    "RegNMSTradeThroughGuard",
    "LULDStateMachine",
    "ShortSaleRestrictionTracker",
    "DarkPoolRouter",
    "StocksEngine",
]

logger = get_logger("trading.synthetic.stocks_engine")


@dataclass
class SIPNBBO:
    symbol: str
    bid_price: float
    ask_price: float
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000.0)


@dataclass
class DirectFeedQuote:
    symbol: str
    venue: str
    bid_price: float
    ask_price: float
    bid_qty: float = 100.0
    ask_qty: float = 100.0
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000.0)


class SIPDirectFeedReconciler:
    """
    HFT-grade reconciler detecting SIP latency anomalies using Dual-Speed Log-Normal EWMA.
    Features asymmetric side-specific dislocation tracking and auto-annealing for infrastructure regime shifts.
    """

    def __init__(
        self,
        symbol: str = "AAPL",
        lambda_fast: float = 0.10,
        lambda_slow: float = 0.01,
        z_threshold: float = 3.0,
        regime_shift_ticks: int = 20,
    ):
        self.symbol = symbol
        self.lambda_fast = lambda_fast
        self.lambda_slow = lambda_slow
        self.z_threshold = z_threshold
        self.regime_shift_ticks = regime_shift_ticks

        self.last_sip: Optional[SIPNBBO] = None
        self.direct_quotes: Dict[str, DirectFeedQuote] = {}
        self.discrepancies_flagged: List[Dict[str, Any]] = []

        # Dual-Speed Log-Normal Latency Model: ln(delta_t) ~ N(mu, sigma^2)
        self.mu_fast: float = 1.0
        self.mu_slow: float = 1.0
        self.var_slow: float = 0.25
        self.initialized: bool = False
        self.divergence_tick_counter: int = 0
        self.regime_annealing_count: int = 0

    def update_sip(self, sip: SIPNBBO) -> None:
        self.last_sip = sip

    def update_direct(self, quote: DirectFeedQuote) -> bool:
        """Returns True if a cross-venue discrepancy violating SIP NBBO is detected."""
        self.direct_quotes[quote.venue] = quote
        if self.last_sip is None:
            return False

        is_discrepancy = (quote.bid_price > self.last_sip.ask_price) or (quote.ask_price < self.last_sip.bid_price)

        if is_discrepancy:
            flag = {
                "venue": quote.venue,
                "direct_bid": quote.bid_price,
                "direct_ask": quote.ask_price,
                "sip_bid": self.last_sip.bid_price,
                "sip_ask": self.last_sip.ask_price,
                "timestamp_ms": quote.timestamp_ms,
            }
            self.discrepancies_flagged.append(flag)
            logger.warning(
                f"[SIP_DIRECT_DISCREPANCY] Venue {quote.venue} ({quote.bid_price:.2f}/{quote.ask_price:.2f}) "
                f"violates SIP NBBO ({self.last_sip.bid_price:.2f}/{self.last_sip.ask_price:.2f})."
            )

        return is_discrepancy

    def evaluate_feed_anomaly(
        self,
        direct_quote: DirectFeedQuote,
    ) -> Tuple[str, str, float]:
        """
        Evaluates feed latency and asymmetric dislocation.
        Returns: (state_classification, defensive_action, z_score).
        Defensive actions: 'PULL_BIDS', 'PULL_ASKS', 'PULL_BOTH', 'NONE'.
        """
        self.direct_quotes[direct_quote.venue] = direct_quote
        if self.last_sip is None:
            return "NO_SIP_DATA", "NONE", 0.0

        # 1. Compute physical latency delta in milliseconds
        delta_t_ms = max(0.01, direct_quote.timestamp_ms - self.last_sip.timestamp_ms)
        log_tau = math.log(delta_t_ms)

        # 2. Update Dual-Speed Log-Normal EWMA statistics
        if not self.initialized:
            self.mu_fast = log_tau
            self.mu_slow = log_tau
            self.var_slow = 0.25
            self.initialized = True
        else:
            self.mu_fast = (1.0 - self.lambda_fast) * self.mu_fast + self.lambda_fast * log_tau
            diff_slow = log_tau - self.mu_slow
            self.mu_slow = (1.0 - self.lambda_slow) * self.mu_slow + self.lambda_slow * log_tau
            self.var_slow = (1.0 - self.lambda_slow) * self.var_slow + self.lambda_slow * (diff_slow ** 2)

            # Check for permanent infrastructure regime shift (Dual-Speed Divergence)
            sigma_slow = math.sqrt(max(0.01, self.var_slow))
            divergence_z = abs(self.mu_fast - self.mu_slow) / sigma_slow
            if divergence_z > 2.5:
                self.divergence_tick_counter += 1
                if self.divergence_tick_counter >= self.regime_shift_ticks:
                    # Promote fast mean to slow mean (Regime Annealing)
                    self.mu_slow = self.mu_fast
                    self.divergence_tick_counter = 0
                    self.regime_annealing_count += 1
                    logger.info(
                        f"[INFRASTRUCTURE_REGIME_ANNEALED] New baseline latency established at "
                        f"exp({self.mu_slow:.2f})={math.exp(self.mu_slow):.2f}ms."
                    )
            else:
                self.divergence_tick_counter = max(0, self.divergence_tick_counter - 1)

        sigma_slow = math.sqrt(max(0.01, self.var_slow))
        z_score = (log_tau - self.mu_slow) / sigma_slow

        # 3. Independent Asymmetric Dislocation (Non-diluted Max Formulation)
        sip_spread = max(0.01, self.last_sip.ask_price - self.last_sip.bid_price)
        d_bid = max(0.0, direct_quote.bid_price - self.last_sip.ask_price) / sip_spread
        d_ask = max(0.0, self.last_sip.bid_price - direct_quote.ask_price) / sip_spread
        d_price = max(d_bid, d_ask)

        # 4. State classification and Asymmetric Defensive Action
        if z_score >= self.z_threshold and d_price > 0.5:
            if d_bid > 0.5 and d_ask > 0.5:
                action = "PULL_BOTH"
            elif d_bid > 0.5:
                action = "PULL_BIDS"  # Protect resting bids from being lifted/swept
            else:
                action = "PULL_ASKS"  # Protect resting asks from being hit

            logger.critical(
                f"[STALE_SIP_DESYNC] {self.symbol} on {direct_quote.venue}: "
                f"Latency Z={z_score:.2f} (delta={delta_t_ms:.2f}ms), MaxDislocation={d_price:.2f}x spread. "
                f"Action: {action}."
            )
            return "STALE_SIP_DISLOCATION", action, z_score

        elif z_score >= 2.0:
            logger.warning(f"[SIP_BURST_WEDGE] {self.symbol}: Elevated queueing latency Z={z_score:.2f}.")
            return "TRAFFIC_BURST", "NONE", z_score

        return "NORMAL_PROPAGATION", "NONE", z_score


class RegNMSTradeThroughGuard:
    """Enforces Reg NMS Rule 611 Trade-Through prevention and non-displayed routing options."""

    def evaluate_order(
        self,
        side: str,
        price: float,
        nbbo: SIPNBBO,
        allow_non_displayed: bool = False,
    ) -> Tuple[bool, str]:
        """Evaluates whether an order price violates protected SIP NBBO quotes."""
        if side.upper() == "BUY":
            if price < nbbo.ask_price:
                if allow_non_displayed:
                    return True, "ROUTED_NON_DISPLAYED"
                return False, "TRADE_THROUGH_VIOLATION_CANCELLED"
            return True, "ROUTED_DISPLAYED"
        else:  # SELL
            if price > nbbo.bid_price:
                if allow_non_displayed:
                    return True, "ROUTED_NON_DISPLAYED"
                return False, "TRADE_THROUGH_VIOLATION_CANCELLED"
            return True, "ROUTED_DISPLAYED"


class LULDStateMachine:
    """Monitors Limit-Up / Limit-Down bands, proximity triggers, and trading halts."""

    def __init__(self, reference_price: float = 100.0, band_pct: float = 0.05, proximity_threshold: float = 0.005):
        self.reference_price = reference_price
        self.band_pct = band_pct  # 5% Tier 1 NMS stock band
        self.proximity_threshold = proximity_threshold  # 0.5% proximity trigger

        self.lower_band = round(reference_price * (1.0 - band_pct), 2)
        self.upper_band = round(reference_price * (1.0 + band_pct), 2)
        self.is_halted: bool = False
        self.proximity_triggered: bool = False

    def update_price(self, current_price: float) -> Tuple[bool, bool]:
        """Returns (proximity_pull_quotes, is_halted)."""
        dist_to_lower = (current_price - self.lower_band) / self.reference_price
        dist_to_upper = (self.upper_band - current_price) / self.reference_price

        # Check LULD Halt trigger
        if current_price <= self.lower_band or current_price >= self.upper_band:
            self.is_halted = True
            logger.critical(f"[LULD_HALT] Price {current_price:.2f} breached band [{self.lower_band:.2f}, {self.upper_band:.2f}]. HALTED.")
            return True, True

        # Check 0.5% proximity trigger to pull quotes before official exchange halt
        if dist_to_lower <= self.proximity_threshold or dist_to_upper <= self.proximity_threshold:
            self.proximity_triggered = True
            logger.warning(f"[LULD_PROXIMITY_PULL] Price {current_price:.2f} within 0.5% of LULD band. Pulling quotes.")
            return True, False

        self.proximity_triggered = False
        return False, False


class ShortSaleRestrictionTracker:
    """Enforces SEC Rule 201 (Alternative Uptick Rule) blocking short sells at or below national best bid."""

    def __init__(self, previous_close: float = 100.0):
        self.previous_close = previous_close
        self.ssr_active: bool = False

    def update_price(self, current_price: float) -> bool:
        """Triggers SSR if stock drops >= 10% from previous close."""
        drop_pct = (self.previous_close - current_price) / self.previous_close
        if drop_pct >= 0.10:
            if not self.ssr_active:
                self.ssr_active = True
                logger.warning(f"[SSR_TRIGGERED] Stock dropped {drop_pct*100:.1f}% >= 10.0%. Rule 201 active.")
        return self.ssr_active

    def validate_short_order(self, order_price: float, best_bid: float) -> Tuple[bool, str]:
        """Short sell must be strictly priced above best bid when SSR is active."""
        if not self.ssr_active:
            return True, "ALLOWED"

        if order_price <= best_bid:
            logger.warning(f"[SSR_SHORT_BLOCKED] Short price {order_price:.2f} <= best bid {best_bid:.2f} under Rule 201.")
            return False, "BLOCKED_RULE_201"

        return True, "ALLOWED_UPTICK"


class DarkPoolRouter:
    """Routes institutional-sized equity orders to Dark Pools / ATS to minimize displayed market impact."""

    def __init__(self, dark_pool_threshold: float = 10000.0):
        self.dark_pool_threshold = dark_pool_threshold
        self.routed_orders: List[Dict[str, Any]] = []

    def route_order(self, symbol: str, side: str, qty: float, limit_price: float) -> Dict[str, Any]:
        use_dark_pool = bool(qty >= self.dark_pool_threshold)
        destination = "DARK_POOL_ATS" if use_dark_pool else "LIT_EXCHANGE_NASDAQ"

        order_record = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": limit_price,
            "destination": destination,
            "is_dark_pool": use_dark_pool,
            "timestamp_ms": time.time() * 1000.0,
        }
        self.routed_orders.append(order_record)
        logger.info(f"[EQUITY_ROUTED] {qty} {symbol} -> {destination}.")
        return order_record


class StocksEngine:
    """Consolidated US Equities Regulatory & Execution Engine with Freshness Gating and Minimum Depth Filters."""

    def __init__(self, symbol: str = "AAPL", reference_price: float = 150.0, prev_close: float = 150.0):
        self.symbol = symbol
        self.sip_reconciler = SIPDirectFeedReconciler(symbol=symbol)
        self.trade_through_guard = RegNMSTradeThroughGuard()
        self.luld_machine = LULDStateMachine(reference_price=reference_price)
        self.ssr_tracker = ShortSaleRestrictionTracker(previous_close=prev_close)
        self.dark_router = DarkPoolRouter()
        self.entries_frozen: bool = False

    def freeze_entries_on_macro(self) -> None:
        self.entries_frozen = True
        logger.info(f"[STOCKS_ENTRIES_FROZEN] Macro uncertainty detected. Freezing equity entry orders.")

    def unfreeze_entries(self) -> None:
        self.entries_frozen = False

    def evaluate_sniper_iso(
        self,
        venue: str,
        side: str,
        limit_price: float,
        target_qty: float,
        current_time_ms: Optional[float] = None,
        max_quote_age_ms: float = 0.05,  # 50 microseconds (0.05 ms)
        min_contra_depth: float = 100.0,  # Round-lot 100 shares minimum threshold
    ) -> Tuple[bool, str]:
        """
        Evaluates whether an Intermarket Sweep Order (ISO) IOC should be dispatched.
        Enforces:
        1. Quote Freshness Gate (< 50 microseconds).
        2. Minimum Contra-Side Depth Gate (>= 100 shares).
        """
        now_ms = current_time_ms if current_time_ms is not None else time.time() * 1000.0
        quote = self.sip_reconciler.direct_quotes.get(venue)

        if quote is None:
            return False, "NO_DIRECT_QUOTE"

        quote_age_ms = now_ms - quote.timestamp_ms
        if quote_age_ms > max_quote_age_ms:
            logger.info(f"[SNIPER_ISO_ABORTED_STALE] Direct quote age {quote_age_ms*1000:.1f}us > 50us threshold.")
            return False, "ABORTED_STALE_QUOTE"

        contra_qty = quote.ask_qty if side.upper() == "BUY" else quote.bid_qty
        if contra_qty < min_contra_depth or target_qty < min_contra_depth:
            logger.info(f"[SNIPER_ISO_ABORTED_DEPTH] Contra depth {contra_qty} < {min_contra_depth} min round lot.")
            return False, "ABORTED_INSUFFICIENT_DEPTH"

        return True, "DISPATCH_ISO_IOC"
