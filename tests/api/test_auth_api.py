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

from trading.api.auth import (DEMO_OPERATOR_PASSWORD, SESSION_COOKIE_NAME,
                              Role, SessionManager, build_session_claims,
                              hash_password, login_rate_limiter,
                              resolve_secret)

SECRET = "unit-test-secret-unit-test"  # 21 bytes >= 16 min (VA-056)

# R2/VA-001: there is no default credential anymore, so auth-positive tests
# pin an explicit pbkdf2 operator password hash.
DEFAULT_TEST_OPERATOR_PASSWORD = "s3cret-op-pass"


@pytest.fixture(autouse=True)
def _secret_env(monkeypatch):
    """Every test starts from an explicit, controlled secret environment."""
    monkeypatch.setenv("API_SESSION_SECRET", SECRET)
    monkeypatch.delenv("API_INSECURE_DEV", raising=False)
    monkeypatch.setenv(
        "OPERATOR_PASSWORD_HASH", hash_password(DEFAULT_TEST_OPERATOR_PASSWORD)
    )
    # R2/VA-002: per-ip attempt buckets are process-global; isolate per test.
    login_rate_limiter._attempts.clear()


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
        json={"username": "operator", "password": DEFAULT_TEST_OPERATOR_PASSWORD},
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


def test_config_update_rejects_out_of_bounds_values_va005(client):
    """VA-005: unbounded/negative config values must 422, not store verbatim."""
    login = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": DEFAULT_TEST_OPERATOR_PASSWORD},
    )
    assert login.status_code == 200

    for bad in [
        {"max_position_pct": -50.0},  # negative
        {"max_position_pct": 0.0},  # zero
        {"max_position_pct": 75.0},  # > 1.0 fraction cap
        {"risk_multiplier": 1e18},  # unbounded
        {"risk_multiplier": 0.0},  # zero
        {"risk_multiplier": -1.5},  # negative
    ]:
        resp = client.put("/api/config", json=bad)
        assert resp.status_code == 422, f"{bad} should 422, got {resp.status_code}"

    # In-bounds values still accepted
    ok = client.put("/api/config", json={"risk_multiplier": 1.25})
    assert ok.status_code == 200
    readback = client.get("/api/config")
    assert readback.json()["config"].get("risk_multiplier") == 1.25


def test_wrong_password_is_401_without_cookie(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": "operator", "password": "definitely-wrong"},
    )
    assert resp.status_code == 401
    assert not resp.cookies.get(SESSION_COOKIE_NAME)


def test_custom_password_hash_is_honored(client, monkeypatch):
    monkeypatch.setenv("OPERATOR_PASSWORD_HASH", hash_password("fresh-op-pass"))
    ok = client.post(
        "/api/auth/login", json={"username": "op", "password": "fresh-op-pass"}
    )
    assert ok.status_code == 200
    bad = client.post(
        "/api/auth/login", json={"username": "op", "password": DEMO_OPERATOR_PASSWORD}
    )
    assert bad.status_code == 401


# ---------------------------------------------------------------------------
# R2 wave-2 additions: VA-001 fail-closed credential, VA-003 PBKDF2 hashing,
# VA-002 login rate limiting.
# ---------------------------------------------------------------------------


def test_hash_password_pbkdf2_format_roundtrip():
    """pbkdf2$iterations$salt$hash format verifies; fresh salt per call."""
    from trading.api.auth import PBKDF2_ITERATIONS, verify_password

    first = hash_password("round-trip-pass")
    parts = first.split("$")
    assert len(parts) == 4
    assert parts[0] == "pbkdf2"
    assert int(parts[1]) == PBKDF2_ITERATIONS
    assert verify_password("round-trip-pass", first) is True
    assert verify_password("wrong", first) is False
    # Random per-call salt: same password yields different stored hash.
    second = hash_password("round-trip-pass")
    assert second != first
    assert verify_password("round-trip-pass", second) is True


def test_legacy_sha256_hash_accepted_only_as_migration_with_deprecation(
    client, monkeypatch, caplog
):
    """R2/VA-003: legacy plain-sha256 digests still verify BUT log a loud
    deprecation warning so operators re-hash into pbkdf2 format."""
    import hashlib as _hashlib

    monkeypatch.setenv(
        "OPERATOR_PASSWORD_HASH",
        _hashlib.sha256(b"legacy-op-pass").hexdigest(),
    )
    with caplog.at_level(logging.WARNING, logger="trading.api.auth"):
        ok = client.post(
            "/api/auth/login", json={"username": "op", "password": "legacy-op-pass"}
        )
    assert ok.status_code == 200
    assert any("DEPRECATION" in r.getMessage() for r in caplog.records)


def test_unset_password_hash_fails_closed_503_auth_unconfigured(
    client, monkeypatch, caplog
):
    """R2/VA-001: unset OPERATOR_PASSWORD_HASH must NOT activate any default
    credential -- the login route refuses with 503 AUTH_UNCONFIGURED."""
    monkeypatch.delenv("OPERATOR_PASSWORD_HASH", raising=False)
    with caplog.at_level(logging.ERROR, logger="trading.api.auth"):
        resp = client.post(
            "/api/auth/login",
            json={"username": "operator", "password": DEMO_OPERATOR_PASSWORD},
        )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "AUTH_UNCONFIGURED"
    assert not resp.cookies.get(SESSION_COOKIE_NAME)


def test_insecure_dev_flag_permits_demo_password_with_critical_warning(
    monkeypatch, caplog
):
    """R2/VA-001 escape hatch: API_INSECURE_DEV=1 permits the public demo
    credential, logging CRITICAL on every accepted use."""
    monkeypatch.delenv("OPERATOR_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("API_INSECURE_DEV", "1")
    from trading.api.app import create_app

    app = create_app()
    with caplog.at_level(logging.CRITICAL, logger="trading.api.auth"):
        fresh_client = TestClient(app)
        resp = fresh_client.post(
            "/api/auth/login",
            json={"username": "operator", "password": DEMO_OPERATOR_PASSWORD},
        )
    assert resp.status_code == 200
    criticals = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert any("demo operator" in r.getMessage() for r in criticals)


def test_login_rate_limited_429_after_five_attempts_per_ip(client):
    """R2/VA-002: more than 5 attempts inside the 60s window => 429."""
    for _ in range(5):
        limited = client.post(
            "/api/auth/login", json={"username": "op", "password": "wrong"}
        )
        assert limited.status_code == 401
    sixth = client.post(
        "/api/auth/login",
        json={"username": "op", "password": DEFAULT_TEST_OPERATOR_PASSWORD},
    )
    assert sixth.status_code == 429
    assert not sixth.cookies.get(SESSION_COOKIE_NAME)


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

    criticals = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
    assert criticals, "insecure-dev startup must log at CRITICAL level"
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200


def test_static_dashboard_served_with_security_headers(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
