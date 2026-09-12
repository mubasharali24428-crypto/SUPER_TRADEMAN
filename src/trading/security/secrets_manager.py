"""Secrets Management & Dynamic Webhook Secret Rotation.

Fail-closed secrets access.

CONTRACT (pure environment):
    Secrets are read from process environment variables ONLY — this module
    never calls ``load_dotenv`` and never reads files. If you keep secrets in
    an env file, load it into the environment before starting the process
    (e.g. ``set -a; source /path/to/env; set +a`` or your secret manager).
    The repo's ``.env.example`` documents placeholder names only; real
    credentials live outside version control.

BEHAVIOR:
    * A required secret that is missing or empty raises :class:`SecretNotFoundError`
      on first access. The exception message contains ONLY the secret NAME,
      never any value.
    * Optional secrets return ``None`` explicitly when unset (no mock values).
    * There are NO hardcoded defaults anywhere in this module: an unconfigured
      system fails loudly instead of silently proceeding with fake credentials.
"""

import os
from typing import Dict, Optional

from trading.observability.logger import get_logger

__all__ = ["SecretNotFoundError", "SecretsManager"]

logger = get_logger("trading.security.secrets_manager")

# Secret name -> environment variable name. Values are NEVER stored here;
# this map is configuration, not credential material.
_SECRET_ENV_VARS: Dict[str, str] = {
    "EXCHANGE_API_KEY": "EXCHANGE_API_KEY",
    "EXCHANGE_API_SECRET": "EXCHANGE_API_SECRET",
    "SLACK_WEBHOOK_URL": "SLACK_WEBHOOK_URL",
    "PAGERDUTY_ROUTING_KEY": "PAGERDUTY_ROUTING_KEY",
}


class SecretNotFoundError(LookupError):
    """Raised when a REQUIRED secret is missing or empty from the environment.

    The message carries the secret NAME only — never any secret value.
    """


class SecretsManager:
    """Manages credentials and dynamic rotation of operational webhook tokens.

    Fail-closed by design:

    >>> mgr = SecretsManager()
    >>> mgr.get_secret("EXCHANGE_API_KEY")          # doctest: +SKIP
    Traceback (most recent call last):
        ...
    SecretNotFoundError: Required secret 'EXCHANGE_API_KEY' is not configured ...

    Use ``get_optional_secret`` for integrations that legitimately run without
    a credential; it returns ``None`` (never a placeholder) when unset.
    """

    def __init__(self) -> None:
        # No eager reads and no fallbacks: every access hits the current
        # environment, so exporting a variable later is picked up immediately.
        self._overrides: Dict[str, str] = {}

    def _lookup(self, key: str) -> Optional[str]:
        if key in self._overrides:
            value = self._overrides[key]
            return value if value else None  # rotation rejects empties anyway
        env_name = _SECRET_ENV_VARS.get(key)
        raw = os.environ.get(env_name) if env_name else None
        return raw if raw else None

    def get_secret(self, key: str) -> str:
        """Return a REQUIRED secret, raising if it is missing/empty.

        Raises:
            SecretNotFoundError: if ``key`` is unknown or not configured in the
                environment. Message contains the secret NAME only.
        """
        value = self._lookup(key)
        if value is None:
            logger.error(f"[SECRET_MISSING] Required secret not configured: {key}")
            raise SecretNotFoundError(
                f"Required secret '{key}' is not configured in the environment. "
                f"Set it via its environment variable (see .env.example for names; "
                f"real credentials live outside the repository)."
            )
        return value

    def get_optional_secret(self, key: str) -> Optional[str]:
        """Return a secret or explicitly ``None`` when unconfigured.

        Never returns a default/mock value; callers must handle ``None``.
        """
        return self._lookup(key)

    def is_configured(self, key: str) -> bool:
        """True when the named secret has a non-empty value available."""
        return self._lookup(key) is not None

    def rotate_webhook_secret(self, channel: str, new_secret: str) -> bool:
        """Dynamically rotate webhook secrets without restarting processes."""
        if not new_secret:
            logger.error(
                f"[SECRET_ROTATION_FAILED] Cannot set empty secret for channel {channel}."
            )
            return False

        target_key: Optional[str] = None
        if channel.lower() == "slack":
            target_key = "SLACK_WEBHOOK_URL"
        elif channel.lower() == "pagerduty":
            target_key = "PAGERDUTY_ROUTING_KEY"
        else:
            logger.error(f"[SECRET_ROTATION_FAILED] Unknown secret channel: {channel}")
            return False

        self._overrides[target_key] = new_secret
        logger.info(f"[SECRET_ROTATED] {channel} webhook secret rotated successfully.")
        return True
