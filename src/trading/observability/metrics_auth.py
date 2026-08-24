"""Endpoint Security Authentication Middleware for /metrics and /health.

Fail-closed bearer auth:

* If ``METRICS_BEARER_TOKEN`` is not configured (and no token is passed to the
  constructor), the middleware enters a DISABLED state: every request is
  DENIED regardless of presented credentials. A CRITICAL log line is emitted
  exactly once at construction so operators notice the misconfiguration.
* Token comparison uses :func:`hmac.compare_digest` (timing-safe).
* There is NO default token in this module — an unconfigured metrics endpoint
  must be unreachable, never publicly readable.
"""

import hmac
import os
from typing import List, Optional, Tuple

from trading.observability.logger import get_logger

__all__ = ["MetricsAuthMiddleware"]

logger = get_logger("trading.observability.metrics_auth")

# Process-wide one-shot flag so the misconfiguration CRITICAL fires exactly
# ONCE no matter how many middleware instances get constructed.
_CRITICAL_EMITTED = False


class MetricsAuthMiddleware:
    """Validates Bearer token and IP whitelist for monitoring endpoints."""

    def __init__(
        self,
        bearer_token: Optional[str] = None,
        allowed_ips: Optional[List[str]] = None,
    ):
        global _CRITICAL_EMITTED

        configured = bearer_token if bearer_token else os.getenv("METRICS_BEARER_TOKEN") or ""
        self.bearer_token = configured.strip()
        self.enabled = bool(self.bearer_token)

        self.allowed_ips = list(allowed_ips) if allowed_ips is not None else ["127.0.0.1", "localhost", "::1"]

        if not self.enabled and not _CRITICAL_EMITTED:
            # F-0016: previously fell back to a hardcoded public default token.
            # Now: deny-all + one-time CRITICAL so the outage is visible.
            _CRITICAL_EMITTED = True
            logger.critical(
                "[METRICS_AUTH_DISABLED] METRICS_BEARER_TOKEN is not configured; "
                "the metrics/health endpoints will REJECT ALL requests until a "
                "token is provided. Generate one with `openssl rand -base64 32`."
            )

    def authenticate_request(
        self,
        token: Optional[str],
        client_ip: str,
    ) -> Tuple[bool, str]:
        """Authenticates request via IP whitelist and Bearer token.

        Returns ``(True, "AUTHORIZED")`` only when the middleware is
        configured, the client IP is whitelisted, AND the presented token
        matches. Any other combination is denied.
        """
        # 0. Fail closed when unconfigured (deny-all).
        if not self.enabled:
            logger.warning("[METRICS_AUTH_FAILED] Middleware disabled: METRICS_BEARER_TOKEN not set.")
            return False, "AUTH_UNCONFIGURED"

        # 1. Check IP Whitelist
        if client_ip not in self.allowed_ips and "*" not in self.allowed_ips:
            logger.warning(f"[METRICS_AUTH_FAILED] Client IP {client_ip} not in allowed IP list.")
            return False, "FORBIDDEN_IP"

        # 2. Check Bearer Token — timing-safe comparison.
        #    Accepted forms: "Bearer <token>" (canonical) or the bare token.
        if not token or not (
            hmac.compare_digest(str(token).encode("utf-8"), f"Bearer {self.bearer_token}".encode("utf-8"))
            or hmac.compare_digest(str(token).encode("utf-8"), self.bearer_token.encode("utf-8"))
        ):
            logger.warning("[METRICS_AUTH_FAILED] Invalid or missing bearer token.")
            return False, "UNAUTHORIZED_TOKEN"

        return True, "AUTHORIZED"
