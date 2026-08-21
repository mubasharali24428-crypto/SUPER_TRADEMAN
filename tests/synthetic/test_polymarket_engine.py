"""Unit tests for Task 2: Polymarket AMM Invariant, Probability Velocity, and MEV Defense."""

import pytest

from trading.synthetic.polymarket_engine import (
    AMMSlippageCalculator,
    AtomicBatchExecutor,
    EdgeAfterMEVValidator,
    MEVTaxCalculator,
    PolymarketEngine,
    PrivateMempoolRouter,
    ProbabilityVelocityTracker,
)


def test_probability_velocity_regime_shift():
    """Prove a jump from 40¢ to 60¢ in 1 second triggers a regime shift."""
    tracker = ProbabilityVelocityTracker(velocity_threshold=0.10)
    tracker.update_price(0.40, timestamp_ms=1000.0)

    # Jump to 0.60 in 1 second (delta_p = 0.20/sec > 0.10/sec)
    velocity, is_shift = tracker.update_price(0.60, timestamp_ms=2000.0)
    assert is_shift
    assert velocity == pytest.approx(0.20)


def test_amm_slippage_calculation():
    """Verify slippage calculation matches CPMM math for a given pool size."""
    calc = AMMSlippageCalculator(default_pool_liquidity=100_000.0)
    # Trade of $5,000 against $100,000 pool -> 5% slippage
    slippage = calc.calculate_slippage(trade_size=5000.0)
    assert slippage == pytest.approx(0.05)


def test_mev_tax_historical_rate():
    """Prove MEV Tax correctly incorporates 24h historical MEV rate."""
    mev_calc = MEVTaxCalculator(historical_mev_rate=0.02, pool_liquidity=100_000.0)
    # Trade size $1,000: Slippage = 1000/100000 = 0.01, Extraction = 0.02 * (1000/1000) = 0.02 -> Total Tax = 0.03
    mev_tax = mev_calc.calculate_mev_tax(trade_size=1000.0)
    assert mev_tax == pytest.approx(0.03)


def test_edge_after_mev_veto():
    """Verify Sniper vetoes a trade when MEV Tax exceeds expected edge."""
    mev_calc = MEVTaxCalculator(historical_mev_rate=0.03, pool_liquidity=50_000.0)
    validator = EdgeAfterMEVValidator(mev_calc)

    # Expected edge is only 2%, but MEV Tax is > 3%
    can_execute, tax = validator.validate_edge(expected_edge=0.02, trade_size=1000.0)
    assert not can_execute
    assert tax > 0.02


def test_edge_after_mev_acceptance():
    """Prove Sniper fires when edge > slippage + MEV Tax."""
    mev_calc = MEVTaxCalculator(historical_mev_rate=0.01, pool_liquidity=100_000.0)
    validator = EdgeAfterMEVValidator(mev_calc)

    # Expected edge is 10% (0.10), MEV Tax is ~0.02 -> accepted
    can_execute, tax = validator.validate_edge(expected_edge=0.10, trade_size=1000.0)
    assert can_execute
    assert tax < 0.10


def test_private_mempool_routing():
    """Verify transactions are routed through simulated Flashbots RPC."""
    router = PrivateMempoolRouter(rpc_endpoint="https://rpc.flashbots.net/fast")
    tx = router.submit_private_transaction({"action": "SWAP", "amount": 500})

    assert tx["status"] == "INCLUDED_IN_BLOCK"
    assert tx["mev_protected"]
    assert tx["tx_hash"].startswith("0x_fb_")


def test_atomic_batch_execution():
    """Prove approval + swap are batched into a single simulated transaction."""
    router = PrivateMempoolRouter()
    batch_exec = AtomicBatchExecutor(router)

    res = batch_exec.execute_atomic_batch(
        market_id="POL_CPI_OCT",
        outcome="YES",
        token_amount=1000.0,
        target_price=0.55,
    )
    assert res["status"] == "INCLUDED_IN_BLOCK"
    assert len(batch_exec.executed_batches) == 1
    assert len(batch_exec.executed_batches[0]["calls"]) == 2


def test_polymarket_oracle_latency_arbitrage():
    """Verify Sniper acts on external oracle data before AMM rebalances."""
    engine = PolymarketEngine(market_id="US_FED_RATE", pool_liquidity=100_000.0)
    # Fast oracle shows 75¢ probability, while AMM is lagging at 50¢ (25¢ edge)
    tx = engine.evaluate_oracle_latency_arbitrage(
        fast_oracle_prob=0.75,
        current_amm_prob=0.50,
        trade_size=1000.0,
    )
    assert tx is not None
    assert tx["status"] == "INCLUDED_IN_BLOCK"
