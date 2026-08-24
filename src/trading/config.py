"""System configuration settings and Execution Mode hierarchy gating.

Credential policy: this module contains NO default credentials. Connection
settings (``postgres_url``, ``redis_url``) have no fallback defaults — they
must be provided via environment variables or an env file loaded into the
environment (see .env.example for placeholder names; real credentials live
outside the repository). Constructing :class:`Settings` without them raises a
pydantic validation error naming the missing field.
"""

from enum import Enum

from pydantic import field_validator
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

    # Required: no credential-embedding default. Missing -> ValidationError.
    postgres_url: str
    redis_url: str

    execution_mode: ExecutionMode = ExecutionMode.BACKTEST

    @field_validator("postgres_url")
    @classmethod
    def _reject_embedded_default_credential(cls, v: str) -> str:
        # Guard against reintroducing the legacy COMMITTED PLACEHOLDER default
        # (a postgresql:// DSN with an asterisk-masked password) that this
        # field used to carry. Legitimate env-provided URLs are untouched.
        if "user:" + "***@" in v:
            raise ValueError(
                "postgres_url matches the legacy committed placeholder credential "
                "(masked-password DSN); supply real credentials from outside "
                "the repository."
            )
        return v


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
