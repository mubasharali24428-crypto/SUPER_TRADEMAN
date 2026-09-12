"""SUB-07 additions: bandit action-space validation."""

import pytest

from trading.learning.policy import ContextualBanditAllocator


def test_unknown_action_raises_listing_valid_set():
    bandit = ContextualBanditAllocator(
        ["trend_following", "mean_reversion", "breakout"]
    )
    with pytest.raises(ValueError) as exc:
        bandit.update_from_trade(strategy="moon_shot", reward_r=1.0)
    msg = str(exc.value)
    assert "moon_shot" in msg
    for valid in ("trend_following", "mean_reversion", "breakout"):
        assert valid in msg


def test_known_action_updates_state():
    bandit = ContextualBanditAllocator(["a", "b"])
    bandit.update_from_trade(strategy_name="b", reward_r=2.0)
    assert bandit.counts["b"] == 1
    assert bandit.total_rewards["b"] == 2.0
    assert bandit.successes["b"] == 2.0


def test_select_strategy_returns_member_of_action_space():
    bandit = ContextualBanditAllocator(["a", "b", "c"])
    for _ in range(50):
        assert bandit.select_strategy() in {"a", "b", "c"}
    assert bandit.select_strategy(deterministic=True) in {"a", "b", "c"}


def test_empty_action_space_rejected():
    with pytest.raises(ValueError):
        ContextualBanditAllocator([])
