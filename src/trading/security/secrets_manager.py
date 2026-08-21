"""Secrets Management & Dynamic Webhook Secret Rotation."""

import os
from typing import Dict, Optional

from trading.observability.logger import get_logger

__all__ = ["SecretsManager"]

logger = get_logger("trading.security.secrets_manager")


class SecretsManager:
    """Manages credentials and dynamic rotation of operational webhook tokens."""

    def __init__(self):
        self._secrets: Dict[str, str] = {
            "EXCHANGE_API_KEY": os.getenv("EXCHANGE_API_KEY", "mock_api_key_default"),
            "EXCHANGE_API_SECRET": os.getenv("EXCHANGE_API_SECRET", "mock_api_secret_default"),
            "SLACK_WEBHOOK_URL": os.getenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/mock"),
            "PAGERDUTY_ROUTING_KEY": os.getenv("PAGERDUTY_ROUTING_KEY", "mock_pagerduty_key"),
        }

    def get_secret(self, key: str, default: str = "") -> str:
        """Retrieves a secret securely."""
        return self._secrets.get(key, default)

    def rotate_webhook_secret(self, channel: str, new_secret: str) -> bool:
        """Dynamically rotates webhook secrets without restarting system processes."""
        if not new_secret:
            logger.error(f"[SECRET_ROTATION_FAILED] Cannot set empty secret for channel {channel}.")
            return False

        if channel.lower() == "slack":
            self._secrets["SLACK_WEBHOOK_URL"] = new_secret
            logger.info("[SECRET_ROTATED] Slack webhook URL rotated successfully.")
            return True
        elif channel.lower() == "pagerduty":
            self._secrets["PAGERDUTY_ROUTING_KEY"] = new_secret
            logger.info("[SECRET_ROTATED] PagerDuty routing key rotated successfully.")
            return True

        logger.error(f"[SECRET_ROTATION_FAILED] Unknown secret channel: {channel}")
        return False
