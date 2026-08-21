"""Lightweight Contextual Bandit / Policy Gradient allocator for strategy routing.

Uses LearningGraph trade logs to adapt strategy allocation weights across
market regimes without deep learning dependencies.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional
from trading.learning.graph import LearningGraph


class ContextualBanditAllocator:
    """Softmax policy gradient / Thompson-sampling bandit for regime strategy routing.
    
    Maintains empirical payoff and probability distribution across available strategies
    (e.g., 'trend_following', 'mean_reversion', 'breakout').
    """

    def __init__(self, strategies: List[str], learning_rate: float = 0.1, temperature: float = 1.0):
        self.strategies = strategies
        self.lr = learning_rate
        self.temperature = max(1e-4, temperature)
        # Policy logits (theta) initialized to equal distribution
        self.weights: Dict[str, float] = {s: 0.0 for s in strategies}
        # Statistics: successes (alpha) and failures (beta) for Bayesian Thompson sampling
        self.successes: Dict[str, float] = {s: 1.0 for s in strategies}
        self.failures: Dict[str, float] = {s: 1.0 for s in strategies}
        self.total_rewards: Dict[str, float] = {s: 0.0 for s in strategies}
        self.counts: Dict[str, int] = {s: 0 for s in strategies}

    def get_action_probabilities(self) -> Dict[str, float]:
        """Softmax policy over strategy logits."""
        exp_weights = {s: math.exp(w / self.temperature) for s, w in self.weights.items()}
        total_exp = sum(exp_weights.values())
        return {s: exp_weights[s] / total_exp for s in self.strategies}

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
        
        import random
        r = random.random()
        cumulative = 0.0
        for strat, p in probs.items():
            cumulative += p
            if r <= cumulative:
                return strat
        return self.strategies[-1]

    def update_from_trade(self, strategy_name: Optional[str] = None, reward_r: float = 0.0, strategy: Optional[str] = None) -> None:
        """Policy gradient REINFORCE step using realized R-multiple payoff."""
        name = strategy_name or strategy or self.strategies[0]
        if name not in self.weights:
            return

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

    def fit_from_learning_graph(self, learning_graph: LearningGraph) -> None:
        """Batch-train the bandit policy using recorded trades from a LearningGraph."""
        for rec in learning_graph.get_trade_records():
            strat = rec.get("side") or "momentum" # Fallback strategy label
            pnl = rec.get("actual_pnl", 0.0)
            reward = 1.0 if pnl > 0 else -1.0
            self.update_from_trade(self.strategies[0] if pnl > 0 else self.strategies[-1], reward)

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
