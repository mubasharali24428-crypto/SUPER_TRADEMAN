"""Regime Validator with Adaptive Parameter Drift, Ultra-Slow Structural Volatility Annealing, and Socratic Defense Lockout."""

import collections
import math
import time
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

from trading.observability.logger import get_logger

__all__ = [
    "MicrostructureRegime",
    "MicrostructureMetrics",
    "MacroVolatilityBaseline",
    "AdaptiveStressScaler",
    "AdaptiveEWMVTracker",
    "AdaptiveRegimeThresholds",
    "ReflexValidationResult",
    "CognitionValidationResult",
    "RegimeValidator",
]

logger = get_logger("trading.synthetic.regime_validator")


class MicrostructureRegime:
    CALM = "CALM"
    BUY_STRESS = "BUY_STRESS"
    SELL_STRESS = "SELL_STRESS"
    FRAGILE_BALANCED = "FRAGILE_BALANCED"
    ADVERSARIAL_SHOCK = "ADVERSARIAL_SHOCK"


@dataclass
class MicrostructureMetrics:
    ofi: float
    ofi_norm: float
    lwr: float
    lwr_ext: float
    stress_score: float
    regime: str
    regime_shift_detected: bool
    effective_window_ms: float
    window_truncated: bool
    events_analyzed: int
    confidence: str  # "HIGH", "MEDIUM", "LOW"
    k_dynamic: float = 2.0
    vol_ratio: float = 1.0
    sigma_micro: float = 0.0
    ultra_slow_vol: float = 0.0
    convergence_metric: float = 0.0


@dataclass
class ReflexValidationResult:
    shock_name: str
    execution_latency_ms: float
    issuer_token_used: bool
    data_staleness_halted: bool
    passed: bool


@dataclass
class CognitionValidationResult:
    campaign_name: str
    regime_detection_latency_min: float
    entries_blocked_pct: float
    max_drawdown_contained_pct: float
    passed: bool


class MacroVolatilityBaseline:
    """Tracks session realized volatility and ultra-slow structural volatility with decaying memory annealing."""

    def __init__(
        self,
        lambda_slow: float = 0.001,
        lambda_ultra_slow: float = 0.0001,
        warmup_ticks: int = 500,
        convergence_threshold: float = 0.15,
        annealing_rate: float = 0.005,
    ):
        self.lambda_slow = lambda_slow
        self.lambda_ultra_slow = lambda_ultra_slow
        self.warmup_ticks = warmup_ticks
        self.convergence_threshold = convergence_threshold
        self.annealing_rate = annealing_rate

        self.tick_count = 0
        self.ewma_vol: float = 0.0
        self.ewma_ultra_slow: float = 0.0
        self.baseline_vol: Optional[float] = None
        self.last_price: Optional[float] = None
        self.vol_samples: Deque[float] = collections.deque(maxlen=5000)

        # EMA decaying memory on convergence/divergence
        self.convergence_ema: float = 0.0
        self.annealing_applied_count: int = 0

    def update(self, current_price: float) -> None:
        self.tick_count += 1
        if self.last_price is None:
            self.last_price = current_price
            return

        ret = (current_price - self.last_price) / max(self.last_price, 1e-6)
        abs_ret = abs(ret)
        self.last_price = current_price

        # Update primary session EWMA vol
        if self.ewma_vol == 0.0:
            self.ewma_vol = abs_ret
        else:
            self.ewma_vol = (1.0 - self.lambda_slow) * self.ewma_vol + self.lambda_slow * abs_ret

        # Update ultra-slow structural EWMA vol (Task 1.1)
        if self.ewma_ultra_slow == 0.0:
            self.ewma_ultra_slow = abs_ret
        else:
            self.ewma_ultra_slow = (1.0 - self.lambda_ultra_slow) * self.ewma_ultra_slow + self.lambda_ultra_slow * abs_ret

        self.vol_samples.append(self.ewma_vol)

        if self.tick_count >= self.warmup_ticks and self.baseline_vol is None:
            self.baseline_vol = self.ewma_vol if self.ewma_vol > 0.0 else 0.0001

        # Track convergence metric with decaying memory (Task 1.2 & 1.3)
        if self.ewma_ultra_slow > 0:
            divergence = abs(self.ewma_vol - self.ewma_ultra_slow) / self.ewma_ultra_slow
            self.convergence_ema = 0.95 * self.convergence_ema + 0.05 * divergence

    def conditional_anneal(self, current_state: str, is_defense_locked: bool) -> bool:
        """Anneals baseline volatility upward to New Normal when market stabilizes and state is safe (Task 1.4)."""
        if is_defense_locked:
            return False

        if current_state not in ("NORMAL", "RECOVERING"):
            return False

        if self.baseline_vol is None or self.ewma_ultra_slow <= 0:
            return False

        # Check convergence condition
        if self.convergence_ema <= self.convergence_threshold:
            # Anneal baseline_vol towards structural ultra-slow volatility
            self.baseline_vol = (1.0 - self.annealing_rate) * self.baseline_vol + self.annealing_rate * self.ewma_ultra_slow
            self.annealing_applied_count += 1
            return True

        return False

    @property
    def vol_ratio(self) -> float:
        if self.baseline_vol is None or self.baseline_vol <= 0:
            return 1.0
        return self.ewma_vol / self.baseline_vol


class AdaptiveStressScaler:
    """Michaelis-Menten stress scaler with volatility-adaptive threshold and Socratic Defense Lockout."""

    def __init__(
        self,
        k_base: float = 2.0,
        k_floor: float = 0.5,
        k_ceiling: float = 10.0,
        macro_vol_tracker: Optional[MacroVolatilityBaseline] = None,
    ):
        self.k_base = k_base
        self.k_floor = k_floor
        self.k_ceiling = k_ceiling
        self.macro_vol_tracker = macro_vol_tracker or MacroVolatilityBaseline(lambda_slow=0.001)
        self.defense_lockout_active: bool = False

    def set_defense_lockout(self, active: bool) -> None:
        """Socratic Lockout: Freezes k_dynamic at k_base during defensive states to prevent self-sabotaging suppression."""
        self.defense_lockout_active = active

    def update_baseline(self, current_price: float, current_state: str = "NORMAL") -> None:
        if not self.defense_lockout_active:
            self.macro_vol_tracker.update(current_price)
            self.macro_vol_tracker.conditional_anneal(
                current_state=current_state,
                is_defense_locked=self.defense_lockout_active,
            )

    @property
    def k_dynamic(self) -> float:
        if self.defense_lockout_active:
            return self.k_base
        ratio = self.macro_vol_tracker.vol_ratio
        k = self.k_base * ratio
        return max(self.k_floor, min(self.k_ceiling, k))

    def calculate_stress(self, lwr_ext: float, ofi_norm: float) -> float:
        x = max(0.0, 0.50 * lwr_ext + 1.0 * abs(ofi_norm))
        k = self.k_dynamic
        return round(x / (x + k), 4)


class AdaptiveEWMVTracker:
    """Adaptive Exponentially Weighted Moving Variance tracker with volatility-scaled lambda."""

    def __init__(
        self,
        lambda_base: float = 0.05,
        alpha: Optional[float] = None,
        initial_price: Optional[float] = None,
        macro_vol_tracker: Optional[MacroVolatilityBaseline] = None,
    ):
        self.lambda_base = alpha if alpha is not None else lambda_base
        self.macro_vol_tracker = macro_vol_tracker
        self.mu: float = initial_price if initial_price is not None else 0.0
        self.variance: float = 0.0
        self.initialized = bool(initial_price is not None)

    @property
    def lambda_adaptive(self) -> float:
        if self.macro_vol_tracker is None:
            return self.lambda_base
        ratio = self.macro_vol_tracker.vol_ratio
        adaptive = self.lambda_base * min(ratio, 3.0)
        return max(0.01, min(0.20, adaptive))

    def update(self, price: float) -> float:
        lam = self.lambda_adaptive
        if not self.initialized:
            self.mu = price
            self.variance = 0.0
            self.initialized = True
            return 0.0

        diff = price - self.mu
        self.mu = (1.0 - lam) * self.mu + lam * price
        self.variance = (1.0 - lam) * self.variance + lam * (diff ** 2)
        return math.sqrt(max(0.0, self.variance))

    @property
    def sigma(self) -> float:
        return math.sqrt(max(0.0, self.variance))

    @property
    def std_dev(self) -> float:
        return self.sigma


class AdaptiveRegimeThresholds:
    """Regime classification thresholds that stretch or compress adaptively with macro volatility."""

    def __init__(self, macro_vol_tracker: MacroVolatilityBaseline):
        self.macro_vol_tracker = macro_vol_tracker
        self.base_lwr_stress = 2.5
        self.base_lwr_fragile = 3.0
        self.base_lwr_shock = 5.0
        self.base_ofi_threshold = 0.30

    @property
    def lwr_stress_threshold(self) -> float:
        return self.base_lwr_stress * self.macro_vol_tracker.vol_ratio

    @property
    def lwr_fragile_threshold(self) -> float:
        return self.base_lwr_fragile * self.macro_vol_tracker.vol_ratio

    @property
    def lwr_shock_threshold(self) -> float:
        return self.base_lwr_shock * self.macro_vol_tracker.vol_ratio

    def classify_regime(self, lwr_ext: float, ofi_norm: float, stress_score: float) -> str:
        if lwr_ext > self.lwr_shock_threshold or stress_score >= 0.75:
            return MicrostructureRegime.ADVERSARIAL_SHOCK
        elif lwr_ext > self.lwr_fragile_threshold and abs(ofi_norm) <= self.base_ofi_threshold:
            return MicrostructureRegime.FRAGILE_BALANCED
        elif lwr_ext > self.lwr_stress_threshold and ofi_norm < -self.base_ofi_threshold:
            return MicrostructureRegime.SELL_STRESS
        elif lwr_ext > self.lwr_stress_threshold and ofi_norm > self.base_ofi_threshold:
            return MicrostructureRegime.BUY_STRESS
        else:
            return MicrostructureRegime.CALM


class RegimeValidator:
    """Validates system reflexes, cognition, order flow metrics, and multi-regime classification."""

    def __init__(
        self,
        min_trade_qty: float = 5.0,
        half_saturation_k: float = 2.0,
        epsilon: float = 1e-6,
    ):
        self.min_trade_qty = min_trade_qty
        self.epsilon = epsilon
        self.macro_vol_tracker = MacroVolatilityBaseline(lambda_slow=0.001)
        self.stress_scaler = AdaptiveStressScaler(
            k_base=half_saturation_k,
            macro_vol_tracker=self.macro_vol_tracker,
        )
        self.thresholds = AdaptiveRegimeThresholds(self.macro_vol_tracker)
        self.ewmv_tracker = AdaptiveEWMVTracker(
            lambda_base=0.05,
            macro_vol_tracker=self.macro_vol_tracker,
        )
        self.last_defensive_ts: float = 0.0

    def on_tick(self, current_price: float, micro_price: float, current_state: str = "NORMAL") -> float:
        """Processes live price tick to update macro volatility and micro-price EWMV."""
        self.stress_scaler.update_baseline(current_price, current_state=current_state)
        return self.ewmv_tracker.update(micro_price)

    def set_defense_lockout(self, active: bool) -> None:
        """Activates Socratic Defense Lockout to prevent self-induced threshold expansion."""
        self.stress_scaler.set_defense_lockout(active)

    def calculate_microstructure_metrics(
        self,
        event_log: Deque[Dict[str, Any]],
        window_ms: int = 60000,
    ) -> MicrostructureMetrics:
        """Analyzes LOB event log with endogeneity filtering, adaptive stress scaling, and adaptive thresholds."""
        current_time_ms = time.time() * 1000.0
        cutoff_time_ms = current_time_ms - window_ms

        recent_events = [e for e in event_log if e.get("timestamp_ms", 0.0) >= cutoff_time_ms]

        events_analyzed = len(recent_events)
        effective_window_ms = (
            (current_time_ms - min(e.get("timestamp_ms", current_time_ms) for e in recent_events))
            if recent_events
            else 0.0
        )
        maxlen = getattr(event_log, "maxlen", None)
        window_truncated = bool(maxlen is not None and len(event_log) >= maxlen and effective_window_ms < (window_ms * 0.95))

        # Volume aggregations
        buy_trade_vol = sum(e.get("qty", 0.0) for e in recent_events if e.get("event_type") == "trade" and e.get("side") == "buy")
        sell_trade_vol = sum(e.get("qty", 0.0) for e in recent_events if e.get("event_type") == "trade" and e.get("side") == "sell")
        
        # Endogeneity filtering: separate external market cancellations from internal strategy cancellations
        all_cancel_vol = sum(e.get("qty", 0.0) for e in recent_events if e.get("event_type") == "cancel")
        ext_cancel_vol = sum(
            e.get("qty", 0.0)
            for e in recent_events
            if e.get("event_type") == "cancel" and e.get("source", "EXTERNAL_MARKET") != "INTERNAL_STRATEGY"
        )

        total_trade_vol = buy_trade_vol + sell_trade_vol

        # 1. Normalized Order Flow Imbalance (OFI_norm in [-1, +1])
        ofi = buy_trade_vol - sell_trade_vol
        ofi_norm = (buy_trade_vol - sell_trade_vol) / (total_trade_vol + self.epsilon)

        # 2. Damped Liquidity Withdrawal Rate (LWR and LWR_ext)
        effective_denominator = max(total_trade_vol, self.min_trade_qty)
        lwr = all_cancel_vol / effective_denominator
        lwr_ext = ext_cancel_vol / effective_denominator

        # 3. Adaptive Michaelis-Menten Stress Score
        stress_score = self.stress_scaler.calculate_stress(lwr_ext, ofi_norm)

        # 4. Adaptive Multi-Regime Classification Taxonomy
        regime = self.thresholds.classify_regime(lwr_ext, ofi_norm, stress_score)
        regime_shift_detected = bool(regime != MicrostructureRegime.CALM)

        # Confidence assessment
        if events_analyzed >= 50 and not window_truncated:
            confidence = "HIGH"
        elif events_analyzed >= 10:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        if regime_shift_detected:
            logger.info(
                f"[MICROSTRUCTURE_REGIME_SHIFT] Regime={regime}, OFI_norm={ofi_norm:.2f}, "
                f"LWR_ext={lwr_ext:.2f}, StressScore={stress_score:.3f}, k_dynamic={self.stress_scaler.k_dynamic:.2f}"
            )

        return MicrostructureMetrics(
            ofi=round(ofi, 2),
            ofi_norm=round(ofi_norm, 4),
            lwr=round(lwr, 2),
            lwr_ext=round(lwr_ext, 2),
            stress_score=stress_score,
            regime=regime,
            regime_shift_detected=regime_shift_detected,
            effective_window_ms=round(effective_window_ms, 1),
            window_truncated=window_truncated,
            events_analyzed=events_analyzed,
            confidence=confidence,
            k_dynamic=round(self.stress_scaler.k_dynamic, 2),
            vol_ratio=round(self.macro_vol_tracker.vol_ratio, 2),
            sigma_micro=round(self.ewmv_tracker.sigma, 4),
            ultra_slow_vol=round(self.macro_vol_tracker.ewma_ultra_slow, 6),
            convergence_metric=round(self.macro_vol_tracker.convergence_ema, 4),
        )

    def validate_reflex(
        self,
        shock_name: str,
        execution_latency_ms: float,
        issuer_token_used: bool = True,
        data_staleness_halted: bool = True,
    ) -> ReflexValidationResult:
        """Verifies system reflex execution speed (< 500ms) and _ISSUER sentinel usage."""
        passed = execution_latency_ms <= 500.0 and issuer_token_used and data_staleness_halted
        res = ReflexValidationResult(
            shock_name=shock_name,
            execution_latency_ms=execution_latency_ms,
            issuer_token_used=issuer_token_used,
            data_staleness_halted=data_staleness_halted,
            passed=passed,
        )
        logger.info(f"[REFLEX_VALIDATED] Shock={shock_name}, Latency={execution_latency_ms:.1f}ms, Passed={passed}")
        return res

    def validate_cognition(
        self,
        campaign_name: str,
        regime_detection_latency_min: float,
        entries_blocked_pct: float,
        max_drawdown_pct: float,
    ) -> CognitionValidationResult:
        """Verifies regime shift detection speed (< 3 min) and proactive entry blocking."""
        passed = regime_detection_latency_min <= 3.0 and entries_blocked_pct >= 0.80 and max_drawdown_pct < 0.08
        res = CognitionValidationResult(
            campaign_name=campaign_name,
            regime_detection_latency_min=regime_detection_latency_min,
            entries_blocked_pct=entries_blocked_pct,
            max_drawdown_contained_pct=max_drawdown_pct,
            passed=passed,
        )
        logger.info(f"[COGNITION_VALIDATED] Campaign={campaign_name}, DetectionLatency={regime_detection_latency_min:.1f}m, Passed={passed}")
        return res
