"""SUB-07 additions: crossing-order rejection + RNG-injected determinism."""

import numpy as np
import pytest

from trading.synthetic.lob import (BookIntegrityViolation, LimitOrder,
                                   SyntheticLOB)


class _AlwaysSlipRng:
    """Duck-typed RNG double that always loses the friction roll (slips)."""

    def __init__(self):
        self.calls = 0

    def random(self) -> float:
        self.calls += 1
        return 1.0


class _NeverSlipRng:
    """Duck-typed RNG double that always wins the friction roll (fills)."""

    def random(self) -> float:
        return 0.0


def test_crossing_buy_order_slipped_is_rejected_not_resting():
    """A crossing buy that hits queue-slip friction must NOT rest (old bug)."""
    lob = SyntheticLOB(
        symbol="T",
        initial_price=100.0,
        base_fill_probability=0.5,
        rng=_AlwaysSlipRng(),
    )
    best_b, best_a = lob.get_best_bid_ask()
    cross_buy = LimitOrder("x-buy", "buy", best_a + 5.0, 3.0, 1000.0, "agent_test")
    with pytest.raises(BookIntegrityViolation):
        lob.place_order(cross_buy)

    assert all(o.order_id != "x-buy" for o in lob.bids)  # residual rejected
    assert cross_buy.filled_qty == 0.0  # nothing filled pre-slip
    assert cross_buy.priority_timestamp_ms == 1050.0  # friction penalty applied
    assert lob.integrity_violation_count == 1  # metric hook fired


def test_crossing_sell_order_slipped_is_rejected_not_resting():
    lob = SyntheticLOB(
        symbol="T",
        initial_price=100.0,
        base_fill_probability=0.5,
        rng=_AlwaysSlipRng(),
    )
    best_b, best_a = lob.get_best_bid_ask()
    cross_sell = LimitOrder("x-sell", "sell", best_b - 5.0, 3.0, 1000.0, "agent_test")
    with pytest.raises(BookIntegrityViolation):
        lob.place_order(cross_sell)
    assert all(o.order_id != "x-sell" for o in lob.asks)
    assert lob.integrity_violation_count == 1


def test_non_crossing_residual_still_rests_normally():
    """Passive residuals (limit below best ask) rest as before — no regression."""
    lob = SyntheticLOB(
        symbol="T",
        initial_price=100.0,
        base_fill_probability=1.0,
        rng=_NeverSlipRng(),
    )
    best_b, best_a = lob.get_best_bid_ask()
    passive = LimitOrder("p-buy", "buy", best_a - 2.0, 1.0, 1000.0, "agent_test")
    fills = lob.place_order(passive)
    assert fills == []
    assert any(o.order_id == "p-buy" for o in lob.bids)  # rested legally


def test_fully_filled_aggressive_order_does_not_raise():
    lob = SyntheticLOB(
        symbol="T",
        initial_price=100.0,
        base_fill_probability=1.0,
        rng=_NeverSlipRng(),
    )
    best_b, best_a = lob.get_best_bid_ask()
    small_buy = LimitOrder("ok-buy", "buy", best_a + 5.0, 1.0, 1000.0, "agent_test")
    fills = lob.place_order(small_buy)
    assert len(fills) == 1
    assert not any(o.order_id == "ok-buy" for o in lob.bids)


def test_post_insert_integrity_guard_raises_on_crossed_book():
    """Direct guard: best_bid >= best_ask after an insert is flagged."""
    lob = SyntheticLOB(
        symbol="T",
        initial_price=100.0,
        base_fill_probability=1.0,
        rng=np.random.default_rng(0),
    )
    # Simulate an insert that crosses: rest a bid above the best ask, then run
    # the same post-insert guard place_order uses.
    lob.bids.append(LimitOrder("m", "buy", lob.current_price + 5.0, 1.0, 1.0, "mm"))
    lob.bids.sort(key=lambda o: (-o.price, o.priority_timestamp_ms))
    with pytest.raises(BookIntegrityViolation):
        lob._check_book_integrity()
    assert lob.integrity_violation_count == 1


def test_rng_injection_determinism_same_seed_identical_fills():
    """Same injected seed => identical fill/violation trace; differs across seeds."""

    def run(seed):
        lob = SyntheticLOB(
            symbol="DET",
            initial_price=100.0,
            base_fill_probability=0.6,
            rng=np.random.default_rng(seed),
        )
        out = []
        for i in range(15):
            # Aggressive sweep order: friction roll decides per-level slip.
            o = LimitOrder(f"o{i}", "buy", 106.0, 10.0, 1000.0 + i, "agent_det")
            try:
                fills = lob.place_order(o)
                out.append(("fills", [(f["price"], f["qty"]) for f in fills]))
            except BookIntegrityViolation:
                out.append(("violated", round(o.filled_qty, 6)))
            finally:
                lob._seed_initial_book()  # restore depth for next iteration
        return out

    a = run(1234)
    b = run(1234)
    c = run(99)
    assert a == b  # same seed -> identical outcomes
    assert a != c  # different seed -> divergent friction
    kinds = {kind for kind, _ in a}
    assert kinds <= {"fills", "violated"} and len(kinds) >= 1


def test_rng_double_is_accepted():
    """Duck-typed rng doubles pass through for friction-path testing."""
    lob = SyntheticLOB(
        symbol="T",
        initial_price=100.0,
        base_fill_probability=1.0,
        rng=_NeverSlipRng(),
    )
    best_b, best_a = lob.get_best_bid_ask()
    o = LimitOrder("d1", "buy", best_a + 1.0, 0.5, 1000.0, "a")
    fills = lob.place_order(o)
    assert isinstance(fills, list)


def test_fill_probability_floor_default_zero_and_configurable():
    # Default floor is now 0.0: stress can push fills arbitrarily low.
    lob = SyntheticLOB(initial_price=100.0, base_fill_probability=0.30)
    assert lob.get_effective_fill_probability(stress_score=1.0) == pytest.approx(0.05)
    assert lob.get_effective_fill_probability(stress_score=0.0) == 0.30

    # Old hardcoded 0.20 floor is opt-in.
    lob20 = SyntheticLOB(
        initial_price=100.0,
        base_fill_probability=0.30,
        fill_probability_floor=0.20,
    )
    assert lob20.get_effective_fill_probability(stress_score=1.0) == pytest.approx(0.20)

    # Floor never lifts probability above the unstressed value.
    lob9 = SyntheticLOB(base_fill_probability=0.05, fill_probability_floor=0.90)
    assert lob9.get_effective_fill_probability(stress_score=0.0) == 0.05
    assert lob9.get_effective_fill_probability(stress_score=1.0) == pytest.approx(0.05)

    # base_fill_probability <= 0 stays hard-zero regardless of floor.
    lob0 = SyntheticLOB(base_fill_probability=0.0, fill_probability_floor=0.9)
    assert lob0.get_effective_fill_probability(stress_score=0.0) == 0.0


def test_int_seed_accepted_for_rng():
    lob = SyntheticLOB(initial_price=100.0, rng=42)
    assert hasattr(lob.rng, "random")
