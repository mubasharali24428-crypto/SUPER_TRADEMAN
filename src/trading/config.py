"""System configuration settings and Execution Mode hierarchy gating."""

from enum import Enum
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["ExecutionMode", "Settings", "gate_execution_mode"]


class ExecutionMode(Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    SHADOW = "shadow"
    LIVE_RESTRICTED = "live_restricted"
    LIVE_FULL = "live_full"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_url: str = "postgresql://user:pass@localhost:5432/trading"
    redis_url: str = "redis://localhost:6379/0"
    execution_mode: ExecutionMode = ExecutionMode.BACKTEST


def gate_execution_mode(required_mode: ExecutionMode, current_mode: ExecutionMode) -> None:
    """Gates live functions based on system execution mode hierarchy.

    Hierarchy order: BACKTEST < PAPER < SHADOW < LIVE_RESTRICTED < LIVE_FULL
    """
    hierarchy = {
        ExecutionMode.BACKTEST: 0,
        ExecutionMode.PAPER: 1,
        ExecutionMode.SHADOW: 2,
        ExecutionMode.LIVE_RESTRICTED: 3,
        ExecutionMode.LIVE_FULL: 4,
    }
    if hierarchy[current_mode] < hierarchy[required_mode]:
        raise RuntimeError(
            f"ExecutionMode violation: Current mode '{current_mode.value}' is lower than required mode '{required_mode.value}'."
        )

