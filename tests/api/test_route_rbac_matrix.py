"""VA-029: every API route must carry an auth dependency or be on the public allowlist.

Parametrized over app.routes at import time so FUTURE routes are covered
automatically - adding a route without a dependency fails this test unless
the route is added to PUBLIC_ALLOWLIST explicitly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# NOTE: only the session secret is pinned. This module inspects the route
# table and never logs in, so it must NOT touch OPERATOR_PASSWORD_HASH -
# an import-time setdefault here would poison that env var for every later
# test module that relies on setdefault (they would silently inherit ours).
os.environ.setdefault("API_SESSION_SECRET", "route-rbac-test-secret-0123456789")

from fastapi.routing import APIRoute

from trading.api.app import create_app

# Routes intentionally reachable without a session (login itself, health).
PUBLIC_ALLOWLIST = {"/api/health", "/api/auth/login"}

app = create_app()


def _api_routes():
    return [r for r in app.routes if isinstance(r, APIRoute)]


def test_every_non_public_route_requires_auth():
    """Any route not in PUBLIC_ALLOWLIST must have at least one dependency.

    VA-029: closes the gap where future routes added without a
    Depends(require_*) silently ship unauthenticated.
    """
    uncovered = []
    for route in _api_routes():
        if route.path in PUBLIC_ALLOWLIST:
            continue
        dependant = getattr(route, "dependant", None)
        # A route is protected when it declares sub-dependencies (Depends(...))
        has_auth = bool(getattr(dependant, "dependencies", None))
        if not has_auth:
            uncovered.append(route.path)
    assert uncovered == [], (
        f"Routes without any dependency (add Depends(require_*) or extend "
        f"PUBLIC_ALLOWLIST): {uncovered}"
    )


def test_public_allowlist_entries_exist():
    """Entries in PUBLIC_ALLOWLIST must be real routes (prevent drift)."""
    paths = {r.path for r in _api_routes()}
    for p in PUBLIC_ALLOWLIST:
        assert p in paths, f"PUBLIC_ALLOWLIST entry {p} is not a registered route"
