"""Authenticated FastAPI surface for SUPER_TRADEMAN (dashboard + JSON API).

Owned-by: ALEX-FORCE SUB-05. See sub05_status.md at the repo root.
"""

from trading.api.app import create_app

__all__ = ["create_app"]
