"""Tests for Metrics and Health Endpoint Authentication Middleware."""

import pytest

from trading.observability.metrics_auth import MetricsAuthMiddleware


def test_metrics_auth_middleware():
    auth = MetricsAuthMiddleware(bearer_token="secret_token_123", allowed_ips=["127.0.0.1"])

    # Authorized request
    ok, status = auth.authenticate_request("Bearer secret_token_123", "127.0.0.1")
    assert ok
    assert status == "AUTHORIZED"

    # Forbidden IP
    ok_ip, status_ip = auth.authenticate_request("Bearer secret_token_123", "192.168.1.99")
    assert not ok_ip
    assert status_ip == "FORBIDDEN_IP"

    # Invalid token
    ok_tok, status_tok = auth.authenticate_request("Bearer wrong_token", "127.0.0.1")
    assert not ok_tok
    assert status_tok == "UNAUTHORIZED_TOKEN"
