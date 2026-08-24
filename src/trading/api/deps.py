"""FastAPI dependencies for session-based auth (ALEX-FORCE SUB-05)."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from trading.api.auth import SESSION_COOKIE_NAME, Role, SessionManager

_FORBIDDEN_DETAIL = "Role {role} is not permitted to perform this action"


def get_session_manager(request: Request) -> SessionManager:
    """The app factory stores the SessionManager on app.state at startup."""
    return request.app.state.session_manager


def get_session_claims(
    request: Request,
    manager: SessionManager = Depends(get_session_manager),
) -> Optional[object]:
    """Best-effort claims extraction; None when unauthenticated/invalid."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return manager.unsign(token)


def require_role(*allowed: Role):
    """Dependency factory: allow only sessions whose role is in `allowed`.

    Unauthenticated -> 401; authenticated but wrong role -> 403.
    """

    async def _dependency(claims=Depends(get_session_claims)):
        if claims is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Session"},
            )
        if allowed and claims.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=_FORBIDDEN_DETAIL.format(role=claims.role.value),
            )
        return claims

    return _dependency


# Convenience bindings used by route declarations.
require_viewer = require_role(Role.VIEWER, Role.OPERATOR, Role.ADMIN)
require_operator = require_role(Role.OPERATOR, Role.ADMIN)
require_admin = require_role(Role.ADMIN)
