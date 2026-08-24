"""Fail-closed secrets tests: missing env => raises; no mock defaults ever."""

import pytest

from trading.security.secrets_manager import SecretNotFoundError, SecretsManager

ALL_SECRET_KEYS = [
    "EXCHANGE_API_KEY",
    "EXCHANGE_API_SECRET",
    "SLACK_WEBHOOK_URL",
    "PAGERDUTY_ROUTING_KEY",
]

# Values that must NEVER appear as fallbacks in source or behavior.
FORBIDDEN_DEFAULTS = [
    "mock_api_key_default",
    "mock_api_secret_default",
    "https://hooks.slack.com/services/mock",
    "mock_pagerduty_key",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Guarantee a pristine environment for every test in this module."""
    for key in ALL_SECRET_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_missing_required_secret_raises():
    mgr = SecretsManager()
    with pytest.raises(SecretNotFoundError) as excinfo:
        mgr.get_secret("EXCHANGE_API_KEY")
    # Message carries the secret NAME, never any value.
    assert "EXCHANGE_API_KEY" in str(excinfo.value)


@pytest.mark.parametrize("key", ALL_SECRET_KEYS)
def test_every_required_secret_fails_closed_when_unset(key):
    mgr = SecretsManager()
    with pytest.raises(SecretNotFoundError):
        mgr.get_secret(key)


@pytest.mark.parametrize("key", ALL_SECRET_KEYS)
def test_optional_secrets_return_none_explicitly(key, monkeypatch):
    mgr = SecretsManager()
    assert mgr.get_optional_secret(key) is None
    assert mgr.is_configured(key) is False

    monkeypatch.setenv(key, "real-value-from-env")
    assert mgr.get_optional_secret(key) == "real-value-from-env"
    assert mgr.is_configured(key) is True


@pytest.mark.parametrize("key", ALL_SECRET_KEYS)
def test_required_secret_reads_pure_environment(key, monkeypatch):
    mgr = SecretsManager()
    monkeypatch.setenv(key, "env-provided-secret")
    assert mgr.get_secret(key) == "env-provided-secret"


def test_empty_env_var_treated_as_missing(monkeypatch):
    monkeypatch.setenv("EXCHANGE_API_KEY", "")
    mgr = SecretsManager()
    with pytest.raises(SecretNotFoundError):
        mgr.get_secret("EXCHANGE_API_KEY")
    assert mgr.get_optional_secret("EXCHANGE_API_KEY") is None


def test_no_mock_defaults_in_module_source():
    import inspect

    import trading.security.secrets_manager as mod

    src = inspect.getsource(mod)
    for bad in FORBIDDEN_DEFAULTS:
        assert bad not in src, f"hardcoded default leaked back into source: {bad}"


def test_unknown_secret_name_raises_not_silently_defaulted():
    mgr = SecretsManager()
    with pytest.raises(SecretNotFoundError):
        mgr.get_secret("TOTALLY_UNKNOWN_SECRET")


def test_exception_message_never_contains_values(monkeypatch):
    secret_value = "super-secret-do-not-leak-123"
    monkeypatch.setenv("EXCHANGE_API_KEY", secret_value)

    # Missing-secret error path (value absent) and unknown-key path both name-only.
    missing_mgr = SecretsManager()
    with pytest.raises(SecretNotFoundError) as e1:
        missing_mgr.get_secret("EXCHANGE_API_SECRET")
    assert secret_value not in str(e1.value)

    with pytest.raises(SecretNotFoundError) as e2:
        missing_mgr.get_secret("UNKNOWN_KEY_XYZ")
    assert secret_value not in str(e2.value)


# --- Rotation still works (kept from original contract) ----------------------

def test_rotation_slack_and_pagerduty():
    mgr = SecretsManager()

    new_url = "https://hooks.slack.com/services/rotated_secret"
    assert mgr.rotate_webhook_secret("slack", new_url) is True
    assert mgr.get_secret("SLACK_WEBHOOK_URL") == new_url

    new_pd = "rotated_pd_key_123"
    assert mgr.rotate_webhook_secret("pagerduty", new_pd) is True
    assert mgr.get_secret("PAGERDUTY_ROUTING_KEY") == new_pd


def test_rotation_rejects_empty_secret():
    mgr = SecretsManager()
    assert mgr.rotate_webhook_secret("slack", "") is False


def test_rotation_unknown_channel_fails():
    mgr = SecretsManager()
    assert mgr.rotate_webhook_secret("email", "whatever") is False


def test_rotation_then_env_precedence():
    # Rotation overrides the environment until process exit.
    import os

    os.environ["SLACK_WEBHOOK_URL"] = "env-url"
    try:
        mgr = SecretsManager()
        mgr.rotate_webhook_secret("slack", "rotated-url")
        assert mgr.get_secret("SLACK_WEBHOOK_URL") == "rotated-url"
    finally:
        os.environ.pop("SLACK_WEBHOOK_URL", None)
