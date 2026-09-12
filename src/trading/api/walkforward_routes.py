"""API routes for walk-forward validation (RBAC-protected)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from trading.api.deps import require_admin, require_operator, require_viewer
from trading.walkforward.scheduler import should_run

router = APIRouter(prefix="/api/walkforward", tags=["walkforward"])

# Placeholder evidence store — replaced by deployment_metrics persistence in a later wave.
LAST_RUN: dict[str, Any] = {
    "last_run": None,
    "last_run_ts": None,
    "verdict_counts": None,
}


@router.get("/status")
def status(claims: Any = Depends(require_viewer)) -> dict[str, Any]:
    return {
        "last_run": LAST_RUN.get("last_run"),
        "verdict_counts": LAST_RUN.get("verdict_counts"),
        "due": should_run(LAST_RUN.get("last_run_ts")),
    }


@router.post("/trigger")
async def trigger(claims: Any = Depends(require_operator)) -> dict[str, Any]:
    """Kick a walk-forward cycle. Full orchestration lands with the scheduler service."""
    return {"status": "triggered"}


@router.delete("/status")
def reset_status(_: Any = Depends(require_admin)) -> dict[str, str]:
    LAST_RUN["last_run"] = None
    LAST_RUN["last_run_ts"] = None
    LAST_RUN["verdict_counts"] = None
    return {"status": "reset"}
