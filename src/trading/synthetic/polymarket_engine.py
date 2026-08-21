"""Polymarket Prediction Market AMM Invariants, Probability Velocity, and MEV Defense."""

import collections
import time
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from trading.observability.logger import get_logger

__all__ = [
    "ProbabilityVelocityTracker",
    "AMMSlippageCalculator",
    "MEVTaxCalculator",
    "EdgeAfterMEVValidator",
    "PrivateMempoolRouter",
    "AtomicBatchExecutor",
    "PolymarketEngine",
]

logger = get_logger("trading.synthetic.polymarket_engine")


class ProbabilityVelocityTracker:
    """Tracks probability rate of change (Delta P / Delta t) for binary outcome contracts."""

    def __init__(self, velocity_threshold: float = 0.10):
        self.velocity_threshold = velocity_threshold  # 10 cents per second triggers regime shift
        self.history: Deque[Tuple[float, float]] = collections.deque(maxlen=1000)  # (timestamp_ms, price)

    def update_price(self, price: float, timestamp_ms: Optional[float] = None) -> Tuple[float, bool]:
        """Returns (velocity_per_sec, is_regime_shift)."""
        now_ms = timestamp_ms if timestamp_ms is not None else time.time() * 1000.0
        self.history.append((now_ms, price))

        if len(self.history) < 2:
            return 0.0, False

        prev_ms, prev_price = self.history[-2]
        delta_t_sec = max(1e-3, (now_ms - prev_ms) / 1000.0)
        delta_p = abs(price - prev_price)
        velocity = delta_p / delta_t_sec

        is_regime_shift = bool(velocity >= self.velocity_threshold)
        if is_regime_shift:
            logger.warning(
                f"[POLYMARKET_PROBABILITY_VELOCITY_SHIFT] Velocity {velocity:.3f}/sec >= threshold {self.velocity_threshold:.3f}/sec "
                f"(Price {prev_price:.2f} -> {price:.2f} in {delta_t_sec:.2f}s)."
            )

        return velocity, is_regime_shift


class AMMSlippageCalculator:
    """Calculates price impact and slippage against Constant Product Market Maker (CPMM) curve."""

    def __init__(self, default_pool_liquidity: float = 100000.0):
        self.default_pool_liquidity = default_pool_liquidity

    def calculate_slippage(self, trade_size: float, pool_liquidity: Optional[float] = None) -> float:
        """Slippage = trade_size / pool_liquidity (CPMM linear approximation for delta P)."""
        liq = pool_liquidity if pool_liquidity is not None else self.default_pool_liquidity
        if liq <= 0:
            return 1.0
        return min(1.0, trade_size / liq)


class MEVTaxCalculator:
    """Tracks 24h rolling historical MEV extraction rates and calculates total transaction MEV Tax."""

    def __init__(self, historical_mev_rate: float = 0.02, pool_liquidity: float = 100000.0):
        self.historical_mev_rate = historical_mev_rate
        self.slippage_calc = AMMSlippageCalculator(default_pool_liquidity=pool_liquidity)
        self.recent_mev_extractions: Deque[float] = collections.deque(maxlen=1000)

    def record_mev_observation(self, rate: float) -> None:
        self.recent_mev_extractions.append(rate)
        if len(self.recent_mev_extractions) > 10:
            self.historical_mev_rate = sum(self.recent_mev_extractions) / len(self.recent_mev_extractions)

    def calculate_mev_tax(self, trade_size: float, pool_liquidity: Optional[float] = None) -> float:
        """MEV Tax = Slippage + (Historical MEV Rate * Trade Size / Reference Size)."""
        slippage = self.slippage_calc.calculate_slippage(trade_size, pool_liquidity)
        extraction_cost = self.historical_mev_rate * (trade_size / max(1000.0, trade_size))
        total_mev_tax = slippage + extraction_cost
        return round(total_mev_tax, 4)


class EdgeAfterMEVValidator:
    """Enforces strict sniper execution rule: Expected Edge > Slippage + MEV Tax."""

    def __init__(self, mev_calculator: MEVTaxCalculator):
        self.mev_calc = mev_calculator

    def validate_edge(self, expected_edge: float, trade_size: float, pool_liquidity: Optional[float] = None) -> Tuple[bool, float]:
        """Returns (can_execute, mev_tax)."""
        mev_tax = self.mev_calc.calculate_mev_tax(trade_size, pool_liquidity)
        net_edge = expected_edge - mev_tax
        can_execute = bool(net_edge > 0.0)

        if not can_execute:
            logger.info(
                f"[POLYMARKET_MEV_VETO] Expected edge {expected_edge:.4f} <= MEV Tax {mev_tax:.4f}. Vetoed trade."
            )
        else:
            logger.info(
                f"[POLYMARKET_EDGE_ACCEPTED] Expected edge {expected_edge:.4f} > MEV Tax {mev_tax:.4f} (Net={net_edge:.4f})."
            )

        return can_execute, mev_tax


class PrivateMempoolRouter:
    """Routes prediction market transactions through Flashbots Protect RPC to prevent front-running."""

    def __init__(self, rpc_endpoint: str = "https://rpc.flashbots.net/fast"):
        self.rpc_endpoint = rpc_endpoint
        self.routed_transactions: List[Dict[str, Any]] = []

    def submit_private_transaction(self, tx_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Simulates submission through private mempool endpoint."""
        tx_hash = f"0x_fb_{int(time.time()*1000.0)}_{len(self.routed_transactions)}"
        result = {
            "tx_hash": tx_hash,
            "status": "INCLUDED_IN_BLOCK",
            "private_mempool": self.rpc_endpoint,
            "mev_protected": True,
            "payload": tx_payload,
        }
        self.routed_transactions.append(result)
        logger.info(f"[FLASHBOTS_PROTECT_ROUTED] Tx {tx_hash} submitted via {self.rpc_endpoint}.")
        return result


class AtomicBatchExecutor:
    """Executes multi-call batched transaction (Token Approval + AMM Swap) in a single atomic bundle."""

    def __init__(self, private_router: PrivateMempoolRouter):
        self.router = private_router
        self.executed_batches: List[Dict[str, Any]] = []

    def execute_atomic_batch(
        self,
        market_id: str,
        outcome: str,
        token_amount: float,
        target_price: float,
    ) -> Dict[str, Any]:
        """Bundles ERC20 approval and swap call in one atomic transaction."""
        calls = [
            {"target": "ERC20_COLLATERAL", "call_data": f"approve({market_id}, {token_amount})"},
            {"target": "CTF_EXCHANGE", "call_data": f"buy({outcome}, {token_amount}, {target_price})"},
        ]
        bundle = {
            "market_id": market_id,
            "calls": calls,
            "is_atomic": True,
            "timestamp_ms": time.time() * 1000.0,
        }
        tx_result = self.router.submit_private_transaction(bundle)
        self.executed_batches.append(bundle)
        return tx_result


class PolymarketEngine:
    """Institutional Polymarket Prediction Market Microstructure & MEV Defense Engine."""

    def __init__(self, market_id: str = "US_CPI_OCT_2026", pool_liquidity: float = 100000.0):
        self.market_id = market_id
        self.pool_liquidity = pool_liquidity
        self.velocity_tracker = ProbabilityVelocityTracker()
        self.slippage_calc = AMMSlippageCalculator(default_pool_liquidity=pool_liquidity)
        self.mev_calc = MEVTaxCalculator(pool_liquidity=pool_liquidity)
        self.edge_validator = EdgeAfterMEVValidator(self.mev_calc)
        self.private_router = PrivateMempoolRouter()
        self.batch_executor = AtomicBatchExecutor(self.private_router)
        self.sniper_armed: bool = False

    def arm_sniper_on_macro(self) -> None:
        """Arms prediction market sniper for macro contracts upon news trigger."""
        self.sniper_armed = True
        logger.info(f"[POLYMARKET_SNIPER_ARMED] Sniper armed for contract {self.market_id} upon macro event.")

    def evaluate_oracle_latency_arbitrage(
        self,
        fast_oracle_prob: float,
        current_amm_prob: float,
        trade_size: float = 1000.0,
    ) -> Optional[Dict[str, Any]]:
        """Acts on fast off-chain oracle signal before on-chain AMM rebalances."""
        discrepancy = fast_oracle_prob - current_amm_prob
        expected_edge = abs(discrepancy)

        can_execute, mev_tax = self.edge_validator.validate_edge(
            expected_edge=expected_edge,
            trade_size=trade_size,
            pool_liquidity=self.pool_liquidity,
        )

        if can_execute:
            direction = "YES" if discrepancy > 0 else "NO"
            return self.batch_executor.execute_atomic_batch(
                market_id=self.market_id,
                outcome=direction,
                token_amount=trade_size,
                target_price=current_amm_prob,
            )

        return None
