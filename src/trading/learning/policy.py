"""Lightweight Contextual Bandit / Policy Gradient allocator for strategy routing.

Uses LearningGraph trade logs to adapt strategy allocation weights across
market regimes without deep learning dependencies.
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Sequence

from trading.learning.graph import LearningGraph


class ContextualBanditAllocator:
    """Softmax policy gradient / Thompson-sampling bandit for regime strategy routing.
    
    Maintains empirical payoff and probability distribution across available strategies
    (e.g., 'trend_following', 'mean_reversion', 'breakout').

    Action-space contract (SUB-07): any API receiving an action/strategy name
    validates it against ``self.strategies``; unknown actions raise
    ``ValueError`` listing the valid set instead of being silently ignored.
    """

    def __init__(self, strategies: List[str], learning_rate: float = 0.1, temperature: float = 1.0):
        if not strategies:
            raise ValueError("strategies must be a non-empty list of action names")
        # De-duplicate while preserving order so the action space stays well-defined.
        seen: Dict[str, None] = {}
        for s in strategies:
            seen[s] = None
        self.strategies = list(seen)
        self.lr = learning_rate
        self.temperature = max(1e-4, temperature)
        # Policy logits (theta) initialized to equal distribution
        self.weights: Dict[str, float] = {s: 0.0 for s in self.strategies}
        # Statistics: successes (alpha) and failures (beta) for Bayesian Thompson sampling
        self.successes: Dict[str, float] = {s: 1.0 for s in self.strategies}
        self.failures: Dict[str, float] = {s: 1.0 for s in self.strategies}
        self.total_rewards: Dict[str, float] = {s: 0.0 for s in self.strategies}
        self.counts: Dict[str, int] = {s: 0 for s in self.strategies}

    def get_action_probabilities(self) -> Dict[str, float]:
        """Softmax policy over strategy logits."""
        exp_weights = {s: math.exp(w / self.temperature) for s, w in self.weights.items()}
        total_exp = sum(exp_weights.values())
        return {s: exp_weights[s] / total_exp for s in self.strategies}

    def validate_action(self, name: Optional[str]) -> str:
        """Return ``name`` if it is a member of the action space.

        Raises:
            ValueError: if ``name`` is None or not one of ``self.strategies``;
                the message lists the full set of valid actions.
        """
        if name is None or name not in self.weights:
            valid = ", ".join(repr(s) for s in self.strategies)
            raise ValueError(
                f"Unknown action: {name!r}. Valid actions are [{valid}]"
            )
        return name

    def select_strategy(self, context: Optional[Sequence[float]] = None, deterministic: bool = False) -> str:
        """Selects the best strategy or samples according to policy distribution."""
        probs = self.get_action_probabilities()
        if context is not None and len(context) >= len(self.strategies):
            # Modulate logits by contextual regime weights
            adjusted_probs = {}
            for i, s in enumerate(self.strategies):
                regime_weight = context[i] if i < len(context) else 1.0 / len(self.strategies)
                adjusted_probs[s] = probs[s] * max(regime_weight, 0.01)
            tot = sum(adjusted_probs.values())
            probs = {s: p / tot for s, p in adjusted_probs.items()}

        if deterministic:
            return max(probs.keys(), key=lambda k: probs[k])

        r = random.random()
        cumulative = 0.0
        for strat, p in probs.items():
            cumulative += p
            if r <= cumulative:
                return strat
        return self.strategies[-1]

    def update_from_trade(self, strategy_name: Optional[str] = None, reward_r: float = 0.0, strategy: Optional[str] = None) -> None:
        """Policy gradient REINFORCE step using realized R-multiple payoff.

        Raises:
            ValueError: when neither ``strategy_name`` nor ``strategy`` names a
                member of the action space (unknown actions no longer pass
                silently).
        """
        raw_name = strategy_name if strategy_name is not None else strategy
        name = self.validate_action(raw_name)

        probs = self.get_action_probabilities()
        # Gradient of log-policy for chosen action
        # d/d_theta_i = (1 - p_i) if i == action else -p_i
        for strat in self.strategies:
            grad = (1.0 - probs[strat]) if strat == name else -probs[strat]
            # Policy gradient update scaled by reward
            self.weights[strat] += self.lr * reward_r * grad

        self.counts[name] += 1
        self.total_rewards[name] += reward_r
        if reward_r > 0:
            self.successes[name] += 1.0
        else:
            self.failures[name] += 1.0

    def fit_from_learning_graph(self, learning_graph: LearningGraph, unknown_action: str = "raise") -> None:
        """Batch-train the bandit policy using recorded trades from a LearningGraph.

        Recorded trades carry a ``side`` label which may fall outside this
        allocator's action space.  ``unknown_action="raise"`` (default) lets the
        underlying validation error propagate; ``"skip"`` skips such records.
        """
        skipped = 0
        for rec in learning_graph.get_trade_records():
            strat = rec.get("side") or "momentum"  # Fallback strategy label
            pnl = rec.get("actual_pnl", 0.0)
            reward = 1.0 if pnl > 0 else -1.0
            target = self.strategies[0] if pnl > 0 else self.strategies[-1]
            try:
                self.update_from_trade(target, reward)
            except ValueError:
                if unknown_action != "skip":
                    raise
                skipped += 1
                continue
        if skipped:
            import logging
            logging.getLogger(__name__).warning(
                "fit_from_learning_graph skipped %d trade(s) outside the action space",
                skipped,
            )

    def summary(self) -> Dict[str, dict]:
        """Returns empirical summary and Bayesian posterior mean for each strategy."""
        probs = self.get_action_probabilities()
        summary = {}
        for s in self.strategies:
            n = self.counts[s]
            avg_r = self.total_rewards[s] / n if n > 0 else 0.0
            bayesian_win_rate = self.successes[s] / (self.successes[s] + self.failures[s])
            summary[s] = {
                "policy_prob": probs[s],
                "trades": n,
                "avg_reward": avg_r,
                "bayesian_win_rate": bayesian_win_rate,
            }
        return summary
