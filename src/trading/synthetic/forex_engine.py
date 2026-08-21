"""FOREX Multi-Venue Aggregation, Last Look Defense, and Smart Order Routing (SOR)."""

import collections
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from trading.observability.logger import get_logger

__all__ = [
    "LPQuote",
    "SyntheticNBBO",
    "VirtualConsolidatedBook",
    "LastLookFilter",
    "ForexSOR",
    "LPReputationTracker",
    "ForexEngine",
]

logger = get_logger("trading.synthetic.forex_engine")


@dataclass
class LPQuote:
    lp_id: str
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000.0)


@dataclass
class SyntheticNBBO:
    symbol: str
    best_bid: float
    best_bid_lp: str
    best_bid_qty: float
    best_ask: float
    best_ask_lp: str
    best_ask_qty: float
    spread_pips: float
    total_depth_bid: float
    total_depth_ask: float
    has_liquidity: bool = True


class VirtualConsolidatedBook:
    """Aggregates multiple Liquidity Provider (LP) order books into a consolidated synthetic NBBO."""

    def __init__(self, symbol: str = "EUR/USD", pip_value: float = 0.0001):
        self.symbol = symbol
        self.pip_value = pip_value
        self.lp_quotes: Dict[str, LPQuote] = {}

    def update_lp_quote(self, quote: LPQuote) -> None:
        self.lp_quotes[quote.lp_id] = quote

    def get_consolidated_nbbo(self) -> SyntheticNBBO:
        """Calculates synthetic best bid and best ask across all active LPs."""
        valid_bids = [q for q in self.lp_quotes.values() if q.bid_qty > 0 and q.bid_price > 0]
        valid_asks = [q for q in self.lp_quotes.values() if q.ask_qty > 0 and q.ask_price > 0]

        if not valid_bids or not valid_asks:
            return SyntheticNBBO(
                symbol=self.symbol,
                best_bid=0.0,
                best_bid_lp="NONE",
                best_bid_qty=0.0,
                best_ask=0.0,
                best_ask_lp="NONE",
                best_ask_qty=0.0,
                spread_pips=0.0,
                total_depth_bid=0.0,
                total_depth_ask=0.0,
                has_liquidity=False,
            )

        best_bid_quote = max(valid_bids, key=lambda q: q.bid_price)
        best_ask_quote = min(valid_asks, key=lambda q: q.ask_price)

        total_depth_bid = sum(q.bid_qty for q in valid_bids)
        total_depth_ask = sum(q.ask_qty for q in valid_asks)
        spread_pips = round((best_ask_quote.ask_price - best_bid_quote.bid_price) / self.pip_value, 2)

        return SyntheticNBBO(
            symbol=self.symbol,
            best_bid=best_bid_quote.bid_price,
            best_bid_lp=best_bid_quote.lp_id,
            best_bid_qty=best_bid_quote.bid_qty,
            best_ask=best_ask_quote.ask_price,
            best_ask_lp=best_ask_quote.lp_id,
            best_ask_qty=best_ask_quote.ask_qty,
            spread_pips=spread_pips,
            total_depth_bid=total_depth_bid,
            total_depth_ask=total_depth_ask,
            has_liquidity=True,
        )


class LastLookFilter:
    """Models LP Last Look rejection behavior during simulated latency and auto-cancels toxic orders."""

    def __init__(self, latency_ms: float = 50.0, pip_value: float = 0.0001, max_rejection_prob: float = 0.70):
        self.latency_ms = latency_ms
        self.pip_value = pip_value
        self.max_rejection_prob = max_rejection_prob

    def calculate_rejection_probability(self, initial_price: float, current_micro_price: float, side: str) -> float:
        """Calculates probability that the LP rejects or holds the order under last-look price drift."""
        if side.upper() == "BUY":
            drift_pips = (current_micro_price - initial_price) / self.pip_value
        else:
            drift_pips = (initial_price - current_micro_price) / self.pip_value

        if drift_pips <= 0.0:
            return 0.05  # Baseline benign latency rejection probability

        # Rejection probability rises sharply when market moves against the LP by > 1.5 - 2 pips
        prob = 1.0 / (1.0 + math.exp(-3.0 * (drift_pips - 1.5)))
        return min(1.0, max(0.0, prob))

    def evaluate_order(self, initial_price: float, current_micro_price: float, side: str) -> Tuple[bool, float]:
        """Returns (should_send, rejection_prob). If rejection_prob > max_rejection_prob, self-cancel."""
        rejection_prob = self.calculate_rejection_probability(initial_price, current_micro_price, side)
        if rejection_prob > self.max_rejection_prob:
            logger.info(
                f"[LAST_LOOK_SELF_CANCEL] Micro-drift projected rejection {rejection_prob*100:.1f}% > {self.max_rejection_prob*100:.1f}%. Self-canceling IOC."
            )
            return False, rejection_prob
        return True, rejection_prob


class LPReputationTracker:
    """Tracks individual LP acceptance rates and computes dynamic smart routing weights."""

    def __init__(self, initial_lps: Optional[List[str]] = None):
        self.lp_stats: Dict[str, Dict[str, int]] = {}
        for lp in (initial_lps or ["LP_BARCLAYS", "LP_CITI", "LP_JPM", "LP_DEUTSCHE", "LP_UBS"]):
            self.lp_stats[lp] = {"sent": 100, "rejected": 5}

    def record_outcome(self, lp_id: str, rejected: bool) -> None:
        if lp_id not in self.lp_stats:
            self.lp_stats[lp_id] = {"sent": 0, "rejected": 0}
        self.lp_stats[lp_id]["sent"] += 1
        if rejected:
            self.lp_stats[lp_id]["rejected"] += 1

    def get_routing_weights(self) -> Dict[str, float]:
        """Calculates dynamic routing weights proportional to LP acceptance scores."""
        scores = {}
        for lp_id, stat in self.lp_stats.items():
            sent = max(1, stat["sent"])
            rejection_rate = stat["rejected"] / sent
            acceptance_rate = max(0.01, 1.0 - rejection_rate)
            scores[lp_id] = acceptance_rate

        total_score = sum(scores.values())
        if total_score <= 0:
            equal_w = 1.0 / len(scores) if scores else 1.0
            return {lp: equal_w for lp in scores}

        return {lp: round(score / total_score, 4) for lp, score in scores.items()}


class ForexSOR:
    """Smart Order Router (SOR) executing TWAP/VWAP order splitting across multiple Liquidity Providers."""

    def __init__(self, consolidated_book: VirtualConsolidatedBook, reputation_tracker: LPReputationTracker):
        self.book = consolidated_book
        self.reputation = reputation_tracker

    def split_twap_order(self, total_qty: float, num_slices: int = 5, side: str = "BUY") -> List[Dict[str, Any]]:
        """Splits large order across LPs weighted by reputation and time-sliced chunks."""
        weights = self.reputation.get_routing_weights()
        active_lps = [lp for lp, w in weights.items() if w > 0]

        if not active_lps:
            return []

        slices = []
        slice_qty = total_qty / max(1, num_slices)

        for slice_idx in range(num_slices):
            for lp_id in active_lps:
                lp_allocation = round(slice_qty * weights[lp_id], 2)
                if lp_allocation > 0:
                    slices.append({
                        "slice_idx": slice_idx,
                        "lp_id": lp_id,
                        "side": side,
                        "qty": lp_allocation,
                        "weight": weights[lp_id],
                    })

        return slices


class ForexEngine:
    """Consolidated institutional FOREX Microstructure & Execution subsystem."""

    def __init__(self, symbol: str = "EUR/USD", num_lps: int = 5):
        self.symbol = symbol
        self.consolidated_book = VirtualConsolidatedBook(symbol=symbol)
        lps = [f"LP_{i+1}" for i in range(num_lps)]
        self.reputation_tracker = LPReputationTracker(initial_lps=lps)
        self.last_look_filter = LastLookFilter()
        self.sor = ForexSOR(self.consolidated_book, self.reputation_tracker)
        self.spread_multiplier: float = 1.0

    def on_news_macro_widen(self, widening_factor: float = 2.0) -> None:
        """Widening FOREX spreads immediately upon macro news event."""
        self.spread_multiplier = widening_factor
        logger.info(f"[FOREX_NEWS_WIDEN] Macro event detected. Widening quote spreads by {widening_factor:.1f}x.")

    def reset_spread(self) -> None:
        self.spread_multiplier = 1.0
