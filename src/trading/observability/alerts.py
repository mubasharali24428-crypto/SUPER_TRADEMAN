"""Alert notification system for critical system events."""

from typing import Optional

from trading.observability.logger import get_logger

__all__ = ["send_alert"]

logger = get_logger("trading.alerts")


def send_alert(title: str, message: str, level: str = "ERROR") -> None:
    """Dispatches a system alert to standard observability channels."""
    logger.error(f"[ALERT - {level}] {title}: {message}")
