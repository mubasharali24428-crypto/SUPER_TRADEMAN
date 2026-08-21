"""Tests for Regime Validator Dual-Mode Stress Evaluation, Microstructure Metrics, and Adaptive Drift."""

import collections
import time
import pytest

from trading.synthetic.regime_validator import (
    AdaptiveStressScaler,
    MacroVolatilityBaseline,
    MicrostructureRegime,
    RegimeValidator,
)


def test_regime_validator_reflex_and_cognition():
    validator = RegimeValidator()

    reflex = validator.validate_reflex("Flash Crash", execution_latency_ms=150.0)
    assert reflex.passed

    cognition = validator.validate_cognition("Spoof-and-Dump", regime_detection_latency_min=2.0, entries_blocked_pct=0.90, max_drawdown_pct=0.04)
    assert cognition.passed


def test_calculate_microstructure_metrics_calm_zero_stress():
    validator = RegimeValidator(min_trade_qty=5.0)
    now_ms = time.time() * 1000.0

    # Perfect calm: equal trades, zero cancels
    log = collections.deque([
        {"event_type": "trade", "side": "buy", "price": 50000.0, "qty": 10.0, "timestamp_ms": now_ms},
        {"event_type": "trade", "side": "sell", "price": 50000.0, "qty": 10.0, "timestamp_ms": now_ms},
    ])

    metrics = validator.calculate_microstructure_metrics(log, window_ms=60000)
    assert metrics.regime == MicrostructureRegime.CALM
    assert not metrics.regime_shift_detected
    assert metrics.ofi_norm == 0.0
    assert metrics.lwr_ext == 0.0
    assert metrics.stress_score == 0.0


def test_calculate_microstructure_metrics_endogeneity_filtering():
    validator = RegimeValidator(min_trade_qty=5.0)
    now_ms = time.time() * 1000.0

    # Internal strategy cancelled 50 units, but external market only cancelled 1 unit
    log = collections.deque([
        {"event_type": "trade", "side": "buy", "price": 50000.0, "qty": 10.0, "timestamp_ms": now_ms, "source": "EXTERNAL_MARKET"},
        {"event_type": "trade", "side": "sell", "price": 50000.0, "qty": 10.0, "timestamp_ms": now_ms, "source": "EXTERNAL_MARKET"},
        {"event_type": "cancel", "side": "buy", "price": 49990.0, "qty": 50.0, "timestamp_ms": now_ms, "source": "INTERNAL_STRATEGY"},
        {"event_type": "cancel", "side": "sell", "price": 50010.0, "qty": 1.0, "timestamp_ms": now_ms, "source": "EXTERNAL_MARKET"},
    ])

    metrics = validator.calculate_microstructure_metrics(log, window_ms=60000)
    assert metrics.lwr == pytest.approx(2.55)
    assert metrics.lwr_ext == pytest.approx(0.05)
    assert metrics.regime == MicrostructureRegime.CALM
    assert not metrics.regime_shift_detected


def test_calculate_microstructure_metrics_sell_stress():
    validator = RegimeValidator(min_trade_qty=5.0)
    now_ms = time.time() * 1000.0

    log = collections.deque([
        {"event_type": "trade", "side": "sell", "price": 49900.0, "qty": 20.0, "timestamp_ms": now_ms, "source": "EXTERNAL_MARKET"},
        {"event_type": "trade", "side": "buy", "price": 49950.0, "qty": 2.0, "timestamp_ms": now_ms, "source": "EXTERNAL_MARKET"},
        {"event_type": "cancel", "side": "buy", "price": 49980.0, "qty": 60.0, "timestamp_ms": now_ms, "source": "EXTERNAL_MARKET"},
    ])

    metrics = validator.calculate_microstructure_metrics(log, window_ms=60000)
    assert metrics.regime == MicrostructureRegime.SELL_STRESS
    assert metrics.regime_shift_detected
    assert metrics.ofi_norm < -0.30
    assert metrics.lwr_ext > 2.5


def test_adaptive_volatility_stretches_k():
    macro_vol = MacroVolatilityBaseline(warmup_ticks=10)
    macro_vol.baseline_vol = 0.001
    macro_vol.ewma_vol = 0.003  # 3x baseline vol

    scaler = AdaptiveStressScaler(k_base=2.0, macro_vol_tracker=macro_vol)
    # k_dynamic = 2.0 * 3.0 = 6.0
    assert scaler.k_dynamic == 6.0


# --- Category 4 spec suite additions ---


def test_macro_volatility_baseline_warmup():
    baseline = MacroVolatilityBaseline(warmup_ticks=500, lambda_slow=0.001)
    price = 100.0
    for _ in range(499):
        price += 0.01
        baseline.update(price)
    assert baseline.tick_count == 499
    assert baseline.baseline_vol is None

    price += 0.01
    baseline.update(price)  # 500th tick: warmup completes
    assert baseline.tick_count == 500
    assert baseline.baseline_vol is not None
    assert baseline.baseline_vol == pytest.approx(baseline.ewma_vol)


def test_macro_volatility_baseline_tracking():
    baseline = MacroVolatilityBaseline(lambda_slow=0.001)
    baseline.update(100.0)  # primes last_price only

    baseline.update(101.0)  # first real tick: bootstrap sets ewma_vol = abs_ret directly
    assert baseline.ewma_vol == pytest.approx(0.01)

    baseline.update(101.0)  # second tick: ret=0.0, now lambda-blended
    expected = (1 - 0.001) * 0.01 + 0.001 * 0.0
    assert baseline.ewma_vol == pytest.approx(expected)


def test_vol_ratio_calculation():
    baseline = MacroVolatilityBaseline(warmup_ticks=2, lambda_slow=0.5)
    baseline.update(100.0)
    baseline.update(101.0)  # tick 2 -> warms up, baseline_vol fixed at this ewma_vol
    baseline_vol_at_warmup = baseline.baseline_vol

    baseline.update(103.0)  # bigger move -> ewma_vol grows, baseline_vol stays fixed (no anneal called)
    assert baseline.vol_ratio == pytest.approx(baseline.ewma_vol / baseline_vol_at_warmup)
    assert baseline.vol_ratio > 1.0


def test_adaptive_stress_scaler_stretch():
    macro = MacroVolatilityBaseline(warmup_ticks=2, lambda_slow=0.5)
    scaler = AdaptiveStressScaler(k_base=2.0, macro_vol_tracker=macro)

    macro.update(100.0)
    macro.update(102.0)  # warms up baseline
    macro.update(110.0)  # sharp move -> vol_ratio stretches k_dynamic proportionally

    assert macro.vol_ratio > 1.0
    assert scaler.k_dynamic == pytest.approx(scaler.k_base * macro.vol_ratio)


def test_adaptive_stress_scaler_clamp():
    macro_high = MacroVolatilityBaseline(warmup_ticks=2, lambda_slow=0.9)
    scaler_high = AdaptiveStressScaler(k_base=2.0, k_floor=0.5, k_ceiling=10.0, macro_vol_tracker=macro_high)
    macro_high.update(100.0)
    macro_high.update(100.5)  # warms up with a tiny baseline_vol
    macro_high.update(200.0)  # violent move -> vol_ratio would blow k_dynamic far past 10.0

    assert scaler_high.k_dynamic == pytest.approx(10.0)

    macro_low = MacroVolatilityBaseline(warmup_ticks=2, lambda_slow=0.9)
    scaler_low = AdaptiveStressScaler(k_base=2.0, k_floor=0.5, k_ceiling=10.0, macro_vol_tracker=macro_low)
    macro_low.update(100.0)
    macro_low.update(110.0)  # warms up with a large baseline_vol
    macro_low.update(110.001)  # near-zero subsequent move -> vol_ratio collapses toward 0

    assert scaler_low.k_dynamic == pytest.approx(0.5)


def test_socratic_defense_lockout_prevents_feedback_loop():
    macro_vol = MacroVolatilityBaseline(warmup_ticks=10)
    macro_vol.baseline_vol = 0.001
    macro_vol.ewma_vol = 0.004  # 4x baseline vol

    scaler = AdaptiveStressScaler(k_base=2.0, macro_vol_tracker=macro_vol)
    assert scaler.k_dynamic == 8.0

    # Activate Socratic Defense Lockout
    scaler.set_defense_lockout(True)
    # k_dynamic must freeze at k_base (2.0) to prevent premature de-escalation!
    assert scaler.k_dynamic == 2.0
