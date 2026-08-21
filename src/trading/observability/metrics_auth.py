"""Endpoint Security Authentication Middleware for /metrics and /health."""

import os
from typing import List, Optional, Tuple

from trading.observability.logger import get_logger

__all__ = ["MetricsAuthMiddleware"]

logger = get_logger("trading.observability.metrics_auth")


class MetricsAuthMiddleware:
    """Validates Bearer token and IP whitelist for monitoring endpoints."""

    def __init__(
        self,
        bearer_token: Optional[str] = None,
        allowed_ips: Optional[List[str]] = None,
    ):
        self.bearer_token = bearer_token or os.getenv("METRICS_BEARER_TOKEN", "super_trademan_secure_token")
        self.allowed_ips = allowed_ips or ["127.0.0.1", "localhost", "::1"]

    def authenticate_request(
        self,
        token: Optional[str],
        client_ip: str,
    ) -> Tuple[bool, str]:
        """Authenticates request via IP whitelist and Bearer token."""
        # 1. Check IP Whitelist
        if client_ip not in self.allowed_ips and "*" not in self.allowed_ips:
            logger.warning(f"[METRICS_AUTH_FAILED] Client IP {client_ip} not in allowed IP list.")
            return False, "FORBIDDEN_IP"

        # 2. Check Bearer Token
        if token != f"Bearer {self.bearer_token}" and token != self.bearer_token:
            logger.warning(f"[METRICS_AUTH_FAILED] Invalid bearer token from {client_ip}.")
            return False, "UNAUTHORIZED_TOKEN"

        return True, "AUTHORIZED"
