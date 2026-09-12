"""Tests for execution mode gating AND credential-free Settings validation."""

import pytest
from pydantic import ValidationError

from trading.config import ExecutionMode, Settings, gate_execution_mode

# --- Execution mode gating -----------------------------------------------------


def test_mode_gating_pass():
    # PAPER >= PAPER -> pass
    gate_execution_mode(ExecutionMode.PAPER, ExecutionMode.PAPER)
    # LIVE_RESTRICTED >= PAPER -> pass
    gate_execution_mode(ExecutionMode.PAPER, ExecutionMode.LIVE_RESTRICTED)


def test_mode_gating_violation_raises():
    # Trying to execute LIVE_RESTRICTED action while in BACKTEST mode -> raise RuntimeError
    with pytest.raises(RuntimeError, match="ExecutionMode violation"):
        gate_execution_mode(ExecutionMode.LIVE_RESTRICTED, ExecutionMode.BACKTEST)


def test_full_hierarchy_is_monotonic():
    order = [
        ExecutionMode.BACKTEST,
        ExecutionMode.PAPER,
        ExecutionMode.SHADOW,
        ExecutionMode.LIVE_RESTRICTED,
        ExecutionMode.LIVE_FULL,
    ]
    for i, required in enumerate(order):
        for current in order[i:]:
            gate_execution_mode(required, current)  # must not raise
        for current in order[:i]:
            with pytest.raises(RuntimeError, match="ExecutionMode violation"):
                gate_execution_mode(required, current)


# --- Settings: required fields have NO default credentials ----------------------
# NOTE: intentionally-invalid Settings(...) constructions below carry
# `# type: ignore[call-arg]` — pydantic enforces them at RUNTIME, which is the
# behavior under test.


@pytest.fixture(autouse=True)
def clean_conn_env(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)


VALID = {
    "postgres_url": "postgresql://realuser:realpass@localhost:5432/realdb",
    "redis_url": "redis://:realpass@localhost:6379/0",
}

# Hermetic construction note: every Settings(...) below passes `_env_file=None`
# so repo-root .env (which may exist with real values) never leaks into these
# tests; they exercise the pure env/explicit-value contract. Pydantic enforces
# the invalid constructions at RUNTIME, hence the bare `# type: ignore`.


def test_missing_postgres_url_raises_validation_error(monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, redis_url=VALID["redis_url"])  # type: ignore[call-arg]
    assert "postgres_url" in str(excinfo.value)


def test_missing_redis_url_raises_validation_error():
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, postgres_url=VALID["postgres_url"])  # type: ignore[call-arg]
    assert "redis_url" in str(excinfo.value)


def test_missing_both_urls_raises_with_both_field_names():
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)  # type: ignore[call-arg]
    msg = str(excinfo.value)
    assert "postgres_url" in msg
    assert "redis_url" in msg


def test_settings_accept_explicit_values_without_defaults():
    s = Settings(
        _env_file=None, postgres_url=VALID["postgres_url"], redis_url=VALID["redis_url"]
    )
    assert s.postgres_url == VALID["postgres_url"]
    assert s.redis_url == VALID["redis_url"]
    assert s.execution_mode == ExecutionMode.BACKTEST


def test_settings_reads_pure_environment(monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", VALID["postgres_url"])
    monkeypatch.setenv("REDIS_URL", VALID["redis_url"])
    s = Settings(_env_file=None)
    assert s.postgres_url == VALID["postgres_url"]
    assert s.redis_url == VALID["redis_url"]


def test_no_embedded_default_credentials_in_module_source():
    # Semantic check: the connection fields must have NO defaults at all
    # (previously postgres_url defaulted to a credential-bearing DSN).
    fields = Settings.model_fields
    assert fields["postgres_url"].is_required() is True
    assert fields["redis_url"].is_required() is True

    import inspect

    import trading.config as mod

    src = inspect.getsource(mod)
    # The legacy committed placeholder default DSN must not exist in the module.
    assert "postgresql://user:" + "***@" not in src
    assert "super_trademan_secure_token" not in src


def test_embedded_default_credential_pattern_rejected():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            postgres_url="postgresql://user:***@localhost:5432/trading",
            redis_url=VALID["redis_url"],
        )
