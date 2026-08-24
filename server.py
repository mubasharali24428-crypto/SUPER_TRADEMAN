"""Thin launcher for the SUPER_TRADEMAN API server (ALEX-FORCE SUB-05).

Replaces the previous stdlib ``http.server`` dashboard server with the
authenticated FastAPI app from ``trading.api.app``.

Environment:
  BIND_HOST  bind address, default 127.0.0.1 (loopback; use deliberately to expose)
  API_PORT   bind port, default 8080

The session secret is enforced by ``trading.api.app.create_app``:
API_SESSION_SECRET must be set unless API_INSECURE_DEV=1 (logs CRITICAL).
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8080"))

    # Importing/creating the app here fails fast on a missing session secret,
    # before uvicorn binds any socket.
    from trading.api.app import create_app

    create_app()

    print("=" * 80)
    print(f"🚀 SUPER_TRADEMAN authenticated API + dashboard on http://{host}:{port}")
    print(f"   BIND_HOST={host} API_PORT={port}")
    print("=" * 80)
    uvicorn.run(
        "trading.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=os.environ.get("API_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
