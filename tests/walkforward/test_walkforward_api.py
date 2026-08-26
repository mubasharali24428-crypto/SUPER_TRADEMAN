"""RBAC + integration tests for walk-forward API routes."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("API_SESSION_SECRET", "test-secret")
os.environ.setdefault("OPERATOR_PASSWORD_HASH",
                      hashlib.sha256(b"operator-pass").hexdigest())

from fastapi.testclient import TestClient  # noqa: E402

from trading.api.app import create_app  # noqa: E402

client = TestClient(create_app())


def _login(password: str):
    return client.post("/api/auth/login", json={"username": "op", "password": password})


def test_status_requires_auth():
    r = client.get("/api/walkforward/status")
    assert r.status_code == 401


def test_trigger_requires_auth():
    r = client.post("/api/walkforward/trigger")
    assert r.status_code == 401


def test_viewer_can_read_status():
    _login("operator-pass")
    r = client.get("/api/walkforward/status")
    assert r.status_code == 200
    body = r.json()
    assert "due" in body and "last_run" in body


def test_operator_can_trigger():
    _login("operator-pass")
    r = client.post("/api/walkforward/trigger")
    assert r.status_code == 202 if r.status_code != 200 else r.status_code == 200
    # either 202 accepted or 200 ok depending on implementation detail; both are authorized
