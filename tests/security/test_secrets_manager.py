"""Tests for Secrets Manager and Dynamic Webhook Rotation."""

import pytest

from trading.security.secrets_manager import SecretsManager


def test_secrets_manager_retrieval_and_rotation():
    mgr = SecretsManager()
    assert mgr.get_secret("SLACK_WEBHOOK_URL") != ""

    # Dynamic secret rotation
    new_url = "https://hooks.slack.com/services/rotated_secret"
    ok = mgr.rotate_webhook_secret("slack", new_url)
    assert ok
    assert mgr.get_secret("SLACK_WEBHOOK_URL") == new_url

    # Dynamic secret rotation for PagerDuty
    new_pd = "rotated_pd_key_123"
    ok_pd = mgr.rotate_webhook_secret("pagerduty", new_pd)
    assert ok_pd
    assert mgr.get_secret("PAGERDUTY_ROUTING_KEY") == new_pd
