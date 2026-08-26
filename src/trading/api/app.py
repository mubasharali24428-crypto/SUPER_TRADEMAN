"""FastAPI application for SUPER_TRADEMAN: dashboard + authenticated JSON API.

ALEX-FORCE SUB-05. Build with ``create_app()``; launch with the thin
``server.py`` runner (uvicorn), or programmatically::

    uvicorn.run("trading.api.app:create_app", factory=True)
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from trading.api.auth import (
    SESSION_COOKIE_NAME,
    Role,
    SessionManager,
    authenticate_operator,
    cookie_options,
    login_rate_limiter,
    resolve_secret,
)
from trading.api.deps import require_admin, require_operator, require_viewer

logger = logging.getLogger("trading.api.app")

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_DIR = REPO_ROOT / "web"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class LoginRequest(BaseModel):
    username: str = ""
    password: str


class ConfigUpdate(BaseModel):
    """Placeholder mutation payload demonstrating role-gated writes."""

    max_position_pct: float | None = None
    risk_multiplier: float | None = None


def _load_health_payload() -> dict:
    """Component statuses from ops.health_service when importable.

    Falls back to an explicit DEGRADED payload (never a fake HEALTHY) so the
    endpoint stays truthful even without the full trading stack.
    """
    try:
        from trading.ops.health_service import HealthService
        from trading.config import ExecutionMode
        from trading.execution.venue_adapter import MockVenueAdapter

        # The hardened HealthService REQUIRES an explicit venue_adapter and its
        # own constructor only auto-selects MockVenueAdapter in simulation mode
        # (fail-closed against fabricated exchange health). We mirror that rule
        # exactly: build it only in simulation mode, with the same mock its
        # constructor would pick; the component then reports itself truthfully
        # as "Simulation venue ... no live API probed".
        sim = getattr(ExecutionMode, "SIMULATION", ExecutionMode.BACKTEST)
        _status, _components, response = HealthService(
            venue_adapter=MockVenueAdapter(), execution_mode=sim
        ).evaluate_system_health()
        response["source"] = "trading.ops.health_service"
        return response
    except Exception as exc:  # pragma: no cover - depends on optional stack
        logger.warning("ops.health_service unavailable (%s); reporting DEGRADED", exc)
        return {
            "status": "DEGRADED",
            "timestamp_utc": None,
            "components": [
                {"name": "health_service", "status": "DEGRADED", "latency_ms": 0.0,
                 "details": f"unavailable: {exc}"},
            ],
            "source": "fallback",
        }


def create_app() -> FastAPI:
    """App factory. Raises RuntimeError before binding if no session secret.

    The secret check runs HERE (not lazily per-request) so misconfiguration is
    fatal at startup rather than discovered by the first login attempt.
    """
    secret = resolve_secret()  # fail-closed unless API_INSECURE_DEV=1
    manager = SessionManager(secret)

    app = FastAPI(
        title="SUPER_TRADEMAN API",
        version="0.1.0",
        description="Authenticated control surface for the SUPER_TRADEMAN engine.",
    )

    @app.on_event("startup")
    def _log_security_posture() -> None:
        if os.environ.get("API_INSECURE_DEV") == "1" and not os.environ.get(
            "API_SESSION_SECRET"
        ):
            logger.critical(
                "Running in INSECURE DEV mode (ephemeral session secret). "
                "Do not expose beyond loopback."
            )
        else:
            logger.info("Session secret loaded; signed-cookie auth active.")

    # --- request tracing / correlation --------------------------------------
    # OT-1: one middleware, two jobs — stamp every request with a correlation
    # id (the existing ContextVar consumed by ops.logging_config JSON logs)
    # and open one span per request when real OTel tracing was configured via
    # trading.observability.otel.setup_tracing(). Both degrade gracefully:
    # no OTel extras installed => no-op spans, logging keeps working.
    from trading.ops.logging_config import set_correlation_id
    from trading.observability import otel as _otel

    _otel.setup_tracing("super_trademan-api")  # active only w/ OTLP endpoint env
    _request_tracer = _otel.get_request_tracer()

    @app.middleware("http")
    async def request_tracing(request: Request, call_next):
        request_id = os.environ.get("API_REQUEST_ID_HEADER", "X-Request-ID")
        cid = request.headers.get(request_id, "") or uuid.uuid4().hex[:12]
        set_correlation_id(cid)
        with _request_tracer.start_as_current_span(
            f"{request.method} {request.url.path}",
            attributes={
                "http.request.method": request.method,
                "url.path": request.url.path,
                "request.correlation_id": cid,
            },
        ):
            response = await call_next(request)
        response.headers.setdefault("X-Request-ID", cid)
        return response

    # --- security headers on every response ---------------------------------
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    # Same-origin API by default; origins configurable via env for local dev.
    allowed_origins = [
        o.strip()
        for o in os.environ.get(
            "API_CORS_ORIGINS", f"http://localhost:{os.environ.get('API_PORT', '8080')}"
        ).split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- health --------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict:
        return _load_health_payload()

    # --- auth ------------------------------------------------------------------
    @app.post("/api/auth/login")
    def login(body: LoginRequest, request: Request, response: Response):
        # R2 / VA-002: brute-force defense -- fixed window per client ip
        # (5 attempts / 60s, then 429 until a timestamp exits the window).
        client_ip = request.client.host if request.client else "unknown"
        if not login_rate_limiter.check(client_ip):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts; retry later",
            )
        claims = authenticate_operator(body.username, body.password)
        if claims is None:
            if not os.environ.get("OPERATOR_PASSWORD_HASH", "").strip() and os.environ.get(
                "API_INSECURE_DEV"
            ) != "1":
                # R2 / VA-001 fail-closed: no credential configured at all.
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="AUTH_UNCONFIGURED",
                )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        secure = os.environ.get("BIND_HOST", "127.0.0.1") not in ("127.0.0.1", "localhost")
        response.set_cookie(
            value=manager.sign(claims), **cookie_options(secure=secure)
        )
        return {"status": "ok", "username": claims.sub, "role": claims.role.value}

    @app.post("/api/auth/logout")
    def logout(response: Response):
        response.delete_cookie(SESSION_COOKIE_NAME, path="/")
        return {"status": "ok"}

    # --- role-gated demo mutation ---------------------------------------------
    _config_state: dict = {}

    @app.put("/api/config")
    def put_config(
        body: ConfigUpdate,
        claims=Depends(require_operator),
    ):
        _config_state.update(body.model_dump(exclude_none=True))
        return {
            "status": "updated",
            "applied_by": {"sub": claims.sub, "role": claims.role.value},
            "config": dict(_config_state),
        }

    @app.get("/api/config")
    def get_config(claims=Depends(require_viewer)):
        # Read-only: any authenticated role (VIEWER included) may inspect config.
        return {"config": dict(_config_state)}

    @app.delete("/api/config")
    def reset_config(_claims=Depends(require_admin)):
        _config_state.clear()
        return {"status": "reset"}

    # --- walk-forward routes (Phase-3) -----------------------------------------
    from trading.api import walkforward_routes  # noqa: E402 — local import avoids cycles

    app.include_router(walkforward_routes.router)

    # --- static dashboard ------------------------------------------------------
    # Mounted last so /api/* routes win.
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")

    app.state.session_manager = manager
    return app
