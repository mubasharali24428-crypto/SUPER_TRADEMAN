"""Metrics auth tests: deny-when-unconfigured, timing-safe compare, accept path."""

import hmac

import pytest

from trading.observability.metrics_auth import MetricsAuthMiddleware

TOKEN = "test-token-abc123"
FORBIDDEN_DEFAULT = "super_trademan_secure_token"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("METRICS_BEARER_TOKEN", raising=False)


# --- Deny-all when unconfigured (F-0016) -------------------------------------


def test_unconfigured_middleware_denies_everything(monkeypatch):
    monkeypatch.delenv("METRICS_BEARER_TOKEN", raising=False)
    auth = MetricsAuthMiddleware()
    assert auth.enabled is False

    for presented in [
        None,
        "",
        "Bearer anything",
        TOKEN,
        f"Bearer {TOKEN}",
        FORBIDDEN_DEFAULT,
    ]:
        ok, status = auth.authenticate_request(presented, "127.0.0.1")
        assert ok is False
        assert status == "AUTH_UNCONFIGURED"


def test_unconfigured_logs_critical_exactly_once(caplog, monkeypatch):
    import logging

    import trading.observability.metrics_auth as mod

    # Simulate fresh process state for this test (the one-shot flag is
    # process-wide; another test's construction may already have consumed it).
    monkeypatch.setattr(mod, "_CRITICAL_EMITTED", False)

    with caplog.at_level(logging.CRITICAL, logger="trading.observability.metrics_auth"):
        mod.MetricsAuthMiddleware()
        mod.MetricsAuthMiddleware()  # second instance must not re-fire per-instance

    criticals = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(criticals) == 1


def test_no_default_token_in_module_source():
    import inspect

    import trading.observability.metrics_auth as mod

    src = inspect.getsource(mod)
    assert (
        FORBIDDEN_DEFAULT not in src
    ), "hardcoded default token leaked back into source"


# --- Correct-token accept path -------------------------------------------------


def test_correct_token_accepted_bearer_prefixed():
    auth = MetricsAuthMiddleware(bearer_token=TOKEN, allowed_ips=["127.0.0.1"])
    ok, status = auth.authenticate_request(f"Bearer {TOKEN}", "127.0.0.1")
    assert ok is True
    assert status == "AUTHORIZED"


def test_correct_token_accepted_raw_and_from_env(monkeypatch):
    monkeypatch.setenv("METRICS_BEARER_TOKEN", TOKEN)
    auth = MetricsAuthMiddleware(allowed_ips=["127.0.0.1"])

    ok_raw, _ = auth.authenticate_request(TOKEN, "127.0.0.1")
    assert ok_raw is True

    # Whitespace-padded env value is tolerated.
    monkeypatch.setenv("METRICS_BEARER_TOKEN", f"  {TOKEN}  ")
    auth2 = MetricsAuthMiddleware(allowed_ips=["127.0.0.1"])
    ok_pad, _ = auth2.authenticate_request(f"Bearer {TOKEN}", "127.0.0.1")
    assert ok_pad is True


# --- Reject paths --------------------------------------------------------------


def test_wrong_token_rejected():
    auth = MetricsAuthMiddleware(bearer_token=TOKEN, allowed_ips=["127.0.0.1"])
    for presented in ["Bearer wrong_token", TOKEN + "x", None, ""]:
        ok, status = auth.authenticate_request(presented, "127.0.0.1")
        assert ok is False
        assert status == "UNAUTHORIZED_TOKEN"


def test_forbidden_ip_rejected_even_with_valid_token():
    auth = MetricsAuthMiddleware(bearer_token=TOKEN, allowed_ips=["127.0.0.1"])
    ok, status = auth.authenticate_request(f"Bearer {TOKEN}", "192.168.1.99")
    assert ok is False
    assert status == "FORBIDDEN_IP"


# --- Timing-safe comparison ------------------------------------------------------


def test_comparison_is_timing_safe_compare_digest():
    import inspect

    import trading.observability.metrics_auth as mod

    src = inspect.getsource(mod)
    assert "hmac.compare_digest" in src
    assert " != f" not in src  # the old direct string-equality check is gone


def test_compare_digest_used_semantically(monkeypatch):
    """Behavioral probe: a token differing only at the last byte must fail."""
    monkeypatch.setenv("METRICS_BEARER_TOKEN", TOKEN)
    auth = MetricsAuthMiddleware(allowed_ips=["127.0.0.1"])
    almost = TOKEN[:-1] + ("A" if TOKEN[-1] != "A" else "B")
    ok, _ = auth.authenticate_request(f"Bearer {almost}", "127.0.0.1")
    assert ok is False
    # And hmac.compare_digest itself agrees with the middleware's verdict.
    assert hmac.compare_digest(almost.encode(), TOKEN.encode()) is False
