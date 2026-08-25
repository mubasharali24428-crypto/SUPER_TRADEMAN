"""R2 wave-2 regression test for VA-022: fit_from_learning_graph must
attribute wins/losses to strategies BY NAME (the recorded ``strategy`` label),
never by list position or outcome sign.

Pre-fix behavior: rec['side'] was read as the strategy label and rewards were
assigned positionally (strategies[0] on win, strategies[-1] on loss), moving
weights for strategies that never traded.
"""

import pytest

from trading.learning.graph import LearningGraph
from trading.learning.policy import ContextualBanditAllocator


def _two_strategy_graph(tmp_path):
    """alpha wins +10, then loses -5; beta loses -5, then wins +10.

    Uses a private storage path so the repo-root learning_graph.jsonl (which
    holds an old unlabeled backtest record) never leaks into these counts.
    """
    lg = LearningGraph(storage_path=tmp_path / "lg.jsonl")
    lg.add_trade(trade_id="a1", strategy="alpha", net_pnl=+10.0)
    lg.add_trade(trade_id="b1", strategy="beta", net_pnl=-5.0)
    lg.add_trade(trade_id="a2", strategy="alpha", net_pnl=-5.0)
    lg.add_trade(trade_id="b2", strategy="beta", net_pnl=+10.0)
    return lg


def test_records_expose_strategy_label_from_signal_payload(tmp_path):
    """The record schema itself carries the strategy field (schema fix)."""
    lg = _two_strategy_graph(tmp_path)
    by_id = {rec["trade_id"]: rec for rec in lg.get_trade_records()}
    assert by_id["a1"]["strategy"] == "alpha"
    assert by_id["b1"]["strategy"] == "beta"


def test_fit_attributes_rewards_by_name_not_position(tmp_path):
    """Two strategies with symmetric outcomes -> identical per-strategy stats;
    neither gains an advantage from sitting at index 0 vs index 1."""
    allocator = ContextualBanditAllocator(strategies=["beta", "alpha"])  # alpha LAST
    allocator.fit_from_learning_graph(_two_strategy_graph(tmp_path))

    summary = allocator.summary()
    # Both strategies traded exactly twice with symmetric (+1,-1) outcomes.
    assert summary["alpha"]["trades"] == 2
    assert summary["beta"]["trades"] == 2
    assert summary["alpha"]["avg_reward"] == pytest.approx(summary["beta"]["avg_reward"])
    assert summary["alpha"]["avg_reward"] == pytest.approx(0.0)  # (+1-1)/2

    # Positional corruption would have credited ALL wins to strategies[0]
    # ('beta') and all losses to strategies[-1] ('alpha').


def test_positional_corruption_regression_exact_counts(tmp_path):
    """Direct pin of the pre-fix bug: a graph where ONLY the last-listed
    strategy trades must still credit that strategy, not strategies[0]."""
    lg = LearningGraph(storage_path=tmp_path / "lg.jsonl")
    lg.add_trade(trade_id="w1", strategy="zulu", net_pnl=+7.0)
    lg.add_trade(trade_id="w2", strategy="zulu", net_pnl=+3.0)

    allocator = ContextualBanditAllocator(strategies=["alpha", "zulu"])  # zulu is LAST
    allocator.fit_from_learning_graph(lg)

    summary = allocator.summary()
    assert summary["zulu"]["trades"] == 2          # correct attribution
    assert summary["alpha"]["trades"] == 0         # untouched by zulu's wins
    assert summary["zulu"]["avg_reward"] == pytest.approx(1.0)
    assert summary["alpha"]["avg_reward"] == 0.0   # never updated


def test_unknown_labels_follow_unknown_action_policy(tmp_path):
    lg = LearningGraph(storage_path=tmp_path / "lg.jsonl")
    lg.add_trade(trade_id="x1", strategy="ghost", net_pnl=+1.0)

    with pytest.raises(ValueError, match="ghost"):
        ContextualBanditAllocator(["alpha"]).fit_from_learning_graph(lg, unknown_action="raise")

    skipper = ContextualBanditAllocator(["alpha"])
    skipper.fit_from_learning_graph(lg, unknown_action="skip")
    assert skipper.counts["alpha"] == 0  # skipped, not mis-attributed
