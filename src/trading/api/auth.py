"""Session auth primitives for the SUPER_TRADEMAN API (ALEX-FORCE SUB-05).

Signed-cookie sessions via itsdangerous ``TimestampSigner``; roles enforced with
a pydantic enum. Secrets come from the environment and the app refuses to start
without them unless ``API_INSECURE_DEV=1`` (which logs a CRITICAL warning).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from pydantic import BaseModel

logger = logging.getLogger("trading.api.auth")

SESSION_COOKIE_NAME = "std_session"
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60  # 12h

SECRET_ENV_VAR = "API_SESSION_SECRET"
INSECURE_DEV_ENV_VAR = "API_INSECURE_DEV"
PASSWORD_HASH_ENV_VAR = "OPERATOR_PASSWORD_HASH"

# Demo operator: sha256("operator") -- override via OPERATOR_PASSWORD_HASH in prod.
DEMO_OPERATOR_PASSWORD = "operator"
DEFAULT_OPERATOR_PASSWORD_HASH = hashlib.sha256(
    DEMO_OPERATOR_PASSWORD.encode("utf-8")
).hexdigest()


class Role(str, Enum):
    """Access tiers. VIEWER is read-only; OPERATOR may mutate; ADMIN supervises."""

    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"


class SessionClaims(BaseModel):
    """Payload carried inside the signed session cookie."""

    sub: str
    role: Role
    iat: int  # epoch seconds


def resolve_secret() -> str:
    """Return API_SESSION_SECRET; refuse (RuntimeError) without it.

    Setting ``API_INSECURE_DEV=1`` bypasses the refusal for local development,
    substituting an ephemeral per-process secret. The bypass ALWAYS logs at
    CRITICAL level so it can never happen silently.
    """
    secret = os.environ.get(SECRET_ENV_VAR, "").strip()
    if secret:
        return secret

    if os.environ.get(INSECURE_DEV_ENV_VAR) == "1":
        logger.critical(
            "API_SESSION_SECRET is NOT set and API_INSECURE_DEV=1 is active: "
            "starting with an EPHEMERAL in-memory session secret. Sessions will "
            "not survive restarts and this configuration is NOT SAFE for "
            "production or any exposed network interface."
        )
        return f"insecure-dev-{os.getpid()}"

    raise RuntimeError(
        f"Refusing to start: {SECRET_ENV_VAR} env var is required for signed "
        f"session cookies. Set it (e.g. `export {SECRET_ENV_VAR}=$(openssl rand -hex 32)`) "
        f"or explicitly opt into insecure local development with "
        f"{INSECURE_DEV_ENV_VAR}=1."
    )


def hash_password(password: str) -> str:
    """sha256 hex digest used for the OPERATOR_PASSWORD_HASH convention."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(password: str, expected_hash: Optional[str]) -> bool:
    """Constant-time compare of sha256(password) against the configured hash."""
    if not expected_hash:
        return False
    return hmac.compare_digest(hash_password(password), expected_hash.strip().lower())


class SessionManager:
    """Signs/unsigns session payloads with an itsdangerous TimestampSigner."""

    def __init__(self, secret: str):
        self._signer = TimestampSigner(secret)

    def sign(self, claims: SessionClaims) -> str:
        # TimestampSigner signs raw bytes (sign_object lives on Serializer
        # classes), so serialize deterministically ourselves.
        payload = claims.model_dump(mode="json")
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return self._signer.sign(blob).decode("ascii")

    def unsign(self, token: str) -> Optional[SessionClaims]:
        """Return claims for a fresh, valid signature; None otherwise."""
        try:
            blob = self._signer.unsign(
                token.encode("ascii"), max_age=SESSION_MAX_AGE_SECONDS
            )
            payload = json.loads(blob.decode())
        except (BadSignature, SignatureExpired):
            return None
        except (UnicodeEncodeError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        try:
            return SessionClaims.model_validate(payload)
        except Exception:  # malformed payload inside a valid signature
            logger.warning("Session cookie had valid signature but bad payload")
            return None


def build_session_claims(username: str, role: Role) -> SessionClaims:
    return SessionClaims(
        sub=username,
        role=role,
        iat=int(datetime.now(timezone.utc).timestamp()),
    )


def authenticate_operator(username: str, password: str) -> Optional[SessionClaims]:
    """Validate credentials against OPERATOR_PASSWORD_HASH (env).

    On success returns OPERATOR-role claims; on failure returns None. Any
    non-empty username is accepted for the single shared operator account.
    """
    expected_hash = os.environ.get(PASSWORD_HASH_ENV_VAR) or DEFAULT_OPERATOR_PASSWORD_HASH
    if not username or not verify_password(password, expected_hash):
        return None
    return build_session_claims(username, Role.OPERATOR)


def cookie_options(secure: bool = False) -> Dict[str, Any]:
    """Recommended Set-Cookie attributes for the session cookie."""
    return {
        "key": SESSION_COOKIE_NAME,
        "max_age": SESSION_MAX_AGE_SECONDS,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
        "secure": secure,
    }
