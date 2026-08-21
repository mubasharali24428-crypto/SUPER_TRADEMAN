"""Abstract Base Class for Synthetic Behavioral Market Agents."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from trading.synthetic.lob import LimitOrder, SyntheticLOB

__all__ = ["Agent"]


class Agent(ABC):
    """Abstract Base Class representing a synthetic behavioral market participant."""

    def __init__(self, agent_id: str, agent_type: str):
        self.agent_id = agent_id
        self.agent_type = agent_type

    @abstractmethod
    def decide_action(self, lob: SyntheticLOB) -> List[LimitOrder]:
        """Evaluates LOB state and returns orders to place or cancel."""
        pass

    @abstractmethod
    def update_beliefs(self, market_data: Dict[str, Any]) -> None:
        """Updates internal agent beliefs or state based on market updates."""
        pass
