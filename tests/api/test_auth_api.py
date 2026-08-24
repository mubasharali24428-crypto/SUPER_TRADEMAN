"""API auth/liveness tests (ALEX-FORCE SUB-05).

Covers: unauthenticated 401 on protected route, VIEWER cannot mutate (403),
operator login roundtrip sets signed session and passes mutation, health 200,
and fail-closed secret enforcement (missing API_SESSION_SECRET without
API_INSECURE_DEV=1 refuses to start).
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from trading.api.auth import (
    DEMO_OPERATOR_PASSWORD,
    SESSION_COOKIE_NAME,
    Role,
    SessionManager,
    build_session_claims,
    resolve_secret,
)

SECRET = "unit-test-secret"


@pytest.fixture(autouse=True)
def _secret_env(monkeypatch):
    """Every test starts from an explicit, controlled secret environment."""
    monkeypatch.setenv("API_SESSION_SECRET", SECRET)
    monkeypatch.delenv("API_INSECURE_DEV", raising=False)
    monkeypatch.delenv("OPERATOR_PASSWORD_HASH", raising=False)


@pytest.fixture()
def client():
    from trading.api.app import create_app

    return TestClient(create_app())


def _viewer_cookie() -> str:
    """White-box helper: sign a VIEWER session directly (no VIEWER login route)."""
    manager = SessionManager(resolve_secret())
    claims = build_session_claims("audit-viewer", Role.VIEWER)
    return manager.sign(claims)


def test_health_endpoint_returns_200(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in {"HEALTHY", "DEGRADED", "CRITICAL"}
    assert isinstance(body["components"], list) and body["components"]
    assert body.get("source") == "trading.ops.health_service"


def test_unauthenticated_mutation_is_401(client):
    resp = client.put("/api/config", json={"risk_multiplier": 1.5})
    assert resp.status_code == 401


def test_unauthenticated_config_read_is_401(client):
    assert client.get("/api/config").status_code == 401


def test_viewer_cannot_mutate_403(client):
    cookie = _viewer_cookie()
    resp = client.put(
        "/api/config",
        json={"risk_multiplier": 2.0},
        cookies={SESSION_COOKIE_NAME: cookie},
    )
    assert resp.status_code == 403
    # ...but viewers keep read access to the same route family
    assert (
        client.get("/api/config", cookies={SESSION_COOKIE_NAME: cookie}).status_code
        == 200
    )


def test_tampered_session_cookie_is_rejected(client):
    cookie = _viewer_cookie()
    resp = client.put(
        "/api/config",
        json={"risk_multiplier": 9.9},
        cookies={SESSION_COOKIE_NAME: cookie[:-2] + "xx"},
    )
    assert resp.status_code == 401


def test_operator_login_roundtrip_sets_session_and_passes(client):
    login = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": DEMO_OPERATOR_PASSWORD},
    )
    assert login.status_code == 200
    assert SESSION_COOKIE_NAME in login.cookies
    assert login.json()["role"] == Role.OPERATOR.value

    mut = client.put("/api/config", json={"risk_multiplier": 1.25})
    assert mut.status_code == 200
    assert mut.json()["applied_by"]["role"] == Role.OPERATOR.value

    readback = client.get("/api/config")
    assert readback.status_code == 200
    assert readback.json()["config"].get("risk_multiplier") == 1.25

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert client.get("/api/config").status_code == 401


def test_wrong_password_is_401_without_cookie(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "definitely-wrong"},
    )
    assert resp.status_code == 401
    assert not resp.cookies.get(SESSION_COOKIE_NAME)


def test_custom_password_hash_is_honored(client, monkeypatch):
    import hashlib

    monkeypatch.setenv(
        "OPERATOR_PASSWORD_HASH",
        hashlib.sha256(b"s3cret-op-pass").hexdigest(),
    )
    ok = client.post(
        "/api/auth/login", json={"username": "op", "password": "s3cret-op-pass"}
    )
    assert ok.status_code == 200
    bad = client.post(
        "/api/auth/login", json={"username": "op", "password": DEMO_OPERATOR_PASSWORD}
    )
    assert bad.status_code == 401


def test_missing_secret_without_flag_refuses_to_start(monkeypatch, caplog):
    monkeypatch.delenv("API_SESSION_SECRET", raising=False)
    monkeypatch.delenv("API_INSECURE_DEV", raising=False)
    from trading.api.app import create_app

    with caplog.at_level(logging.CRITICAL, logger="trading.api.auth"):
        with pytest.raises(RuntimeError, match="API_SESSION_SECRET"):
            create_app()


def test_insecure_dev_flag_starts_but_logs_critical(monkeypatch, caplog):
    monkeypatch.delenv("API_SESSION_SECRET", raising=False)
    monkeypatch.setenv("API_INSECURE_DEV", "1")
    from trading.api.app import create_app

    with caplog.at_level(logging.CRITICAL, logger="trading.api.auth"):
        app = create_app()

    criticals = [
        r for r in caplog.records if r.levelno >= logging.CRITICAL
    ]
    assert criticals, "insecure-dev startup must log at CRITICAL level"
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200


def test_static_dashboard_served_with_security_headers(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
