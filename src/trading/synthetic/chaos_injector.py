"""Dual-Mode Chaos Injector: Instantaneous Shocks and Cascading Adversarial Pressure."""

import time
from typing import Dict, Any, List, Optional, Tuple

from trading.observability.logger import get_logger
from trading.synthetic.lob import LimitOrder, SyntheticLOB

__all__ = [
    "ChaosMode",
    "ChaosInjector",
]

logger = get_logger("trading.synthetic.chaos_injector")


class ChaosMode:
    INSTANTANEOUS = "instantaneous"
    CASCADING = "cascading"
    MIXED = "mixed"


class ChaosInjector:
    """Injects Instantaneous Shocks (Reflex Testing) and Cascading Adversarial Pressure (Cognition Testing)."""

    def __init__(self, lob: SyntheticLOB):
        self.lob = lob

    # --- WORKSTREAM 3.1: INSTANTANEOUS SHOCKS ---
    def inject_flash_crash(self, drop_pct: float = 0.15) -> float:
        """Flash Crash: Instant 15% price drop and 90% liquidity drop in single tick."""
        old_price = self.lob.current_price
        new_price = old_price * (1.0 - drop_pct)
        self.lob.current_price = new_price

        # Evaporate 90% of bids depth
        self.lob.bids = self.lob.bids[: max(1, len(self.lob.bids) // 10)]
        logger.warning(f"[CHAOS_FLASH_CRASH] Injected flash crash: {old_price} -> {new_price} (-{drop_pct*100:.1f}%)")
        return new_price

    def inject_liquidity_vacuum(self, widen_factor: float = 10.0) -> Tuple[float, float]:
        """Liquidity Vacuum: Widen bid-ask spread by factor of 10x."""
        b, a = self.lob.get_best_bid_ask()
        mid = (b + a) / 2.0
        new_b = mid - (mid - b) * widen_factor
        new_a = mid + (a - mid) * widen_factor

        self.lob.bids = [LimitOrder("vac_b", "buy", new_b, 1.0, time.time() * 1000, "chaos")]
        self.lob.asks = [LimitOrder("vac_a", "sell", new_a, 1.0, time.time() * 1000, "chaos")]
        logger.warning(f"[CHAOS_LIQUIDITY_VACUUM] Injected liquidity vacuum: spread widened {widen_factor}x ({new_b} - {new_a})")
        return new_b, new_a

    def inject_data_staleness(self) -> float:
        """Data Staleness: Delay websocket ticks > 10 seconds."""
        stale_delay_sec = 12.0
        logger.warning(f"[CHAOS_DATA_STALENESS] Simulated websocket data staleness of {stale_delay_sec}s.")
        return stale_delay_sec

    def inject_gap_open(self, gap_pct: float = 0.20) -> float:
        """Gap Open: Price gaps 20% overnight."""
        old_p = self.lob.current_price
        new_p = old_p * (1.0 + gap_pct)
        self.lob.current_price = new_p
        logger.warning(f"[CHAOS_GAP_OPEN] Injected gap open: {old_p} -> {new_p} (+{gap_pct*100:.1f}%)")
        return new_p

    # --- WORKSTREAM 3.2: CASCADING ADVERSARIAL PRESSURE ---
    def execute_spoof_and_dump_stage(self, stage: int) -> Dict[str, Any]:
        """Executes multi-stage Spoof-and-Dump campaign across 10 minutes."""
        if stage == 1:
            # Stage 1: Build fake buy depth
            b, a = self.lob.get_best_bid_ask()
            self.lob.bids.insert(0, LimitOrder("spoof_fake_buy", "buy", b, 100.0, time.time() * 1000, "spoofer"))
            return {"stage": 1, "action": "fake_buy_depth_built"}
        elif stage == 2:
            return {"stage": 2, "action": "system_long_entry_attempt"}
        elif stage == 3:
            # Stage 3: Spoofer cancels bids, retail dumps
            self.lob.bids = [ord_obj for ord_obj in self.lob.bids if ord_obj.agent_id != "spoofer"]
            return {"stage": 3, "action": "bids_canceled_retail_dump"}
        elif stage == 4:
            # Stage 4: Price collapses 8%
            self.lob.current_price *= 0.92
            return {"stage": 4, "action": "price_collapsed_8_pct"}
        return {"stage": stage, "action": "idle"}
