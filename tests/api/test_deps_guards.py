"""Regression guards for Team BRAVO W6 API-side fixes (VA-060, VA-049).

VA-060/VA-054: require_role() with zero roles used to allow ANY authenticated
user (deny-by-default inverted in the empty case). The factory must refuse.

VA-049: alembic env must accept the legacy ``postgres://`` spelling and fail
loudly on unsupported schemes instead of dying inside SQLAlchemy mid-migration.

NOTE on strategy: ``alembic/env.py`` executes Alembic-runtime code at import
time (``alembic.context.config`` etc.) and cannot be imported under plain
pytest. These tests therefore load it against a *fake* alembic runtime whose
``is_offline_mode()`` is True, so only module top-level + ``_database_url()``
execute. Every sys.modules/sys.path injection is removed afterwards so no
other test module is affected (a prior non-hermetic version of this file
poisoned unrelated suites -- do not regress that).
"""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / "alembic" / "env.py"


@pytest.fixture()
def env_mod(monkeypatch):
    """Load alembic/env.py under a stub alembic runtime; clean up after."""
    # env.py's module body runs the (offline) migration entry point once, so a
    # resolvable URL must exist at load time; tests override it afterwards.
    monkeypatch.setenv(
        "POSTGRES_URL", "postgresql://bravo-stub@localhost:5432/bravo"
    )
    stub_names = ("alembic", "alembic.context")
    saved_modules = {n: sys.modules.get(n) for n in stub_names}
    path_before = list(sys.path)

    alembic_stub = types.ModuleType("alembic")
    ctx = types.ModuleType("alembic.context")
    # config_file_name=None keeps logging.config.fileConfig() out of the picture;
    # offline mode + no-op hooks mean only definitions + _database_url run.
    ctx.config = types.SimpleNamespace(config_file_name=None)
    ctx.is_offline_mode = lambda: True
    ctx.configure = lambda **kwargs: None
    ctx.begin_transaction = contextlib.nullcontext
    ctx.run_migrations = lambda: None
    alembic_stub.context = ctx

    sys.modules["alembic"] = alembic_stub
    sys.modules["alembic.context"] = ctx
    try:
        spec = importlib.util.spec_from_file_location("_bravo_alembic_env", ENV_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        # Remove any paths env.py prepended (it adds <repo>/src), then restore
        # the pre-existing alembic module entries exactly as they were.
        for p in list(sys.path):
            if p not in path_before:
                sys.path.remove(p)
        for n, original in saved_modules.items():
            if original is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = original


# ----------------------------------------------------------------- VA-060 ---


def test_require_role_with_no_roles_is_a_factory_error():
    from trading.api import deps

    with pytest.raises(ValueError, match="at least one role"):
        deps.require_role()


def test_require_role_convenience_bindings_still_construct():
    from trading.api import deps

    # The three shipped bindings each pass explicit roles; they must keep
    # working after the zero-arg guard landed.
    assert callable(deps.require_viewer)
    assert callable(deps.require_operator)
    assert callable(deps.require_admin)


def test_require_role_single_role_still_allowed():
    from trading.api.deps import require_role
    from trading.api.auth import Role

    dep = require_role(Role.ADMIN)
    assert callable(dep)


# ----------------------------------------------------------------- VA-049 ---


def test_postgres_legacy_scheme_is_upgraded_to_asyncpg(env_mod, monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgres://trader:pw@localhost:5432/trading")
    url = env_mod._database_url()
    assert url.startswith("postgresql+asyncpg://")


def test_postgresql_canonical_scheme_still_upgraded(env_mod, monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://trader:pw@localhost:5432/trading")
    assert env_mod._database_url().startswith("postgresql+asyncpg://")


def test_unsupported_scheme_fails_fast_with_clear_error(env_mod, monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "mysql+pymysql://x/y")
    with pytest.raises(RuntimeError, match="Unsupported database URL scheme"):
        env_mod._database_url()


def test_missing_url_still_raises_runtime_error(env_mod, monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="database URL"):
        env_mod._database_url()
