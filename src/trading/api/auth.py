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
import time
from collections import deque
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

# Demo operator credential. NEVER active by default (R2 / VA-001 fail-closed):
# login requires OPERATOR_PASSWORD_HASH unless API_INSECURE_DEV=1 explicitly
# opts into this demo credential, which logs a CRITICAL warning when used.
DEMO_OPERATOR_PASSWORD = "operator"

# Password hashing (R2 / VA-003): PBKDF2-HMAC-SHA256, random per-call salt.
# Stored/env format: ``pbkdf2$<iterations>$<salt_hex>$<hash_hex>``.
PBKDF2_ITERATIONS = 100_000

# Pre-hashed demo credential for API_INSECURE_DEV=1 ONLY (public by design --
# it guards nothing outside explicitly opted-in local development):
# pbkdf2$100000$<salt>$<pbkdf2-sha256("operator", salt)>
INSECURE_DEV_OPERATOR_PASSWORD_HASH = (
    "pbkdf2$100000$5de10c0ffee57ba5e5ab1776dead17f0$"
    "3a371df9ff43a383dd81497a42f1ad0f949b4cf1022d3b278bf39267e562cd59"
)

# Brute-force defense for POST /api/auth/login (R2 / VA-002): slowapi-style
# fixed-window per-client limit, stdlib-only (no middleware dependency).
LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 60.0


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
        if len(secret.encode("utf-8")) < 16:
            logger.error(
                "API_SESSION_SECRET is too short (%d bytes, require >=16). "
                "Run: openssl rand -hex 32",
                len(secret.encode("utf-8")),
            )
            raise RuntimeError(
                f"API_SESSION_SECRET is too short ({len(secret.encode('utf-8'))} bytes). "
                "128-bit minimum required for HMAC signing strength. "
                f"Generate with: openssl rand -hex 32"
            )
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
    """Hash a password for the OPERATOR_PASSWORD_HASH convention.

    Format: ``pbkdf2$<iterations>$<salt_hex>$<hash_hex>`` where hash is
    PBKDF2-HMAC-SHA256 over the UTF-8 password with a fresh random 16-byte
    salt and ``iterations`` = PBKDF2_ITERATIONS (100_000). Verify with
    :func:`verify_password`.
    """
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _pbkdf2_verify(password: str, expected_hash: str) -> bool:
    parts = expected_hash.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2":
        return False
    try:
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = bytes.fromhex(parts[3])
    except ValueError:
        return False
    if iterations <= 0 or not salt or not expected:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def verify_password(password: str, expected_hash: Optional[str]) -> bool:
    """Constant-time verify of ``password`` against the configured hash.

    Accepts the current ``pbkdf2$...`` format. A legacy plain-sha256 hex
    digest is accepted ONLY to permit migration to pbkdf2; using one logs a
    deprecation warning telling the operator to re-hash.
    """
    if not expected_hash:
        return False
    stored = expected_hash.strip()
    if "$" in stored:
        return _pbkdf2_verify(password, stored)
    # Legacy single unsalted sha256 -- migration path only.
    logger.warning(
        "DEPRECATION: OPERATOR_PASSWORD_HASH is a legacy plain-sha256 digest "
        "(single fast round, unsalted). Re-hash it with "
        "trading.api.auth.hash_password(...) into the 'pbkdf2$...' format."
    )
    legacy = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy, stored.lower())


class SessionManager:
    """Signs/unsigns session payloads with an itsdangerous TimestampSigner."""

    def __init__(self, secret: str):
        self._signer = TimestampSigner(secret)
        # VA-004: in-memory token revocation denylist (SHA256 hashes).
        # Lost on restart, but prevents replay within same process lifetime.
        self._revoked_hashes: set = set()

    def revoke(self, token: str) -> None:
        """Add token hash to the VA-004 revocation denylist."""
        self._revoked_hashes.add(hashlib.sha256(token.encode()).hexdigest())

    def _is_revoked(self, token: str) -> bool:
        return hashlib.sha256(token.encode()).hexdigest() in self._revoked_hashes

    def sign(self, claims: SessionClaims) -> str:
        # TimestampSigner signs raw bytes (sign_object lives on Serializer
        # classes), so serialize deterministically ourselves.
        payload = claims.model_dump(mode="json")
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return self._signer.sign(blob).decode("ascii")

    def unsign(self, token: str) -> Optional[SessionClaims]:
        """Return claims; checks VA-004 revocation denylist."""
        if self._is_revoked(token):
            logger.info("Session token revoked (denylist hit)")
            return None
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


class LoginRateLimiter:
    """Slowapi-style per-client fixed-window rate limit, stdlib only.

    Keeps a bounded deque of attempt timestamps per client ip; when more than
    ``max_attempts`` attempts arrive within ``window_seconds`` the caller must
    answer 429 until the oldest timestamp leaves the window.
    """

    def __init__(
        self,
        max_attempts: int = LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
        window_seconds: float = LOGIN_RATE_LIMIT_WINDOW_SECONDS,
        clock: Any = time.monotonic,
    ):
        self.max_attempts = int(max_attempts)
        self.window_seconds = float(window_seconds)
        self._clock = clock
        self._attempts: Dict[str, deque] = {}

    def check(self, client_ip: str) -> bool:
        """Record an attempt; True if allowed, False if the limit is exceeded."""
        now = self._clock()
        bucket = self._attempts.setdefault(client_ip, deque())
        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()
        if len(bucket) >= self.max_attempts:
            return False
        bucket.append(now)
        return True


# Process-wide limiter shared by every create_app() instance (per-ip buckets).
login_rate_limiter = LoginRateLimiter()


def authenticate_operator(username: str, password: str) -> Optional[SessionClaims]:
    """Validate credentials against OPERATOR_PASSWORD_HASH.

    FAIL-CLOSED (R2 / VA-001): when the env var is unset there is NO default
    credential -- this returns None and callers must refuse login with
    AUTH_UNCONFIGURED / HTTP 503. Only an explicit ``API_INSECURE_DEV=1``
    permits the public demo credential ('operator'), logging CRITICAL each use.

    On success returns OPERATOR-role claims; on failure returns None. Any
    non-empty username is accepted for the single shared operator account.
    """
    expected_hash = os.environ.get(PASSWORD_HASH_ENV_VAR, "").strip()
    if not expected_hash:
        if os.environ.get(INSECURE_DEV_ENV_VAR) == "1":
            logger.critical(
                "OPERATOR_PASSWORD_HASH is NOT set but API_INSECURE_DEV=1 is "
                "active: accepting the PUBLIC demo operator password %r. This "
                "is NOT SAFE for production or any exposed network interface.",
                DEMO_OPERATOR_PASSWORD,
            )
            expected_hash = INSECURE_DEV_OPERATOR_PASSWORD_HASH
        else:
            logger.error(
                "Login refused: OPERATOR_PASSWORD_HASH is not configured "
                "(fail-closed). Set it via trading.api.auth.hash_password(...)."
            )
            return None
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
