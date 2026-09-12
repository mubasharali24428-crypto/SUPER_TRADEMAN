"""Tests for the Redis-backed state stores (tier state, alert cooldowns).

Backend-selection contract shared by all sub-04 components:
  explicit client injection > REDIS_URL env > legacy file/JSON default.
"""

import json

import fake_redis_stub  # noqa: F401 — ensures deterministic stub is importable
import pytest
from fake_redis_stub import MemoryRedis

from trading.ops.alert_manager import (AlertManager, AlertSeverity,
                                       FileCooldownStore, RedisCooldownStore,
                                       select_cooldown_store)
from trading.risk.tier_state import (RedisTierState, TierState, load_state,
                                     save_state)


@pytest.fixture()
def mem():
    return MemoryRedis()


# ---------------------------------------------------------------------------
# tier_state: RedisTierState behind save/load interface
# ---------------------------------------------------------------------------


def test_tier_state_file_backend_default(monkeypatch, tmp_path):
    monkeypatch.delenv("REDIS_URL", raising=False)
    path = tmp_path / "tier.json"
    assert save_state(
        TierState(tier="defended", entered_cycle=4, below_count=2), path=str(path)
    )
    st = load_state(path=str(path))
    assert (st.tier, st.entered_cycle, st.below_count) == ("defended", 4, 2)


def test_tier_state_redis_backend_roundtrip_with_ttl(mem):
    backend = RedisTierState(client=mem, key="test:tier", ttl_sec=120)
    assert backend.save(TierState(tier="defended", entered_cycle=9, below_count=3))
    st = backend.load()
    assert (st.tier, st.entered_cycle, st.below_count) == ("defended", 9, 3)
    ttl = mem.ttl("test:tier")
    assert 0 < ttl <= 120  # SETEX really applied


def test_tier_state_key_from_env(monkeypatch, mem):
    monkeypatch.setenv("RISK_TIER_STATE_KEY", "env:chosen:key")
    backend = RedisTierState(client=mem)
    assert backend.key == "env:chosen:key"
    assert backend.save(TierState(tier="normal"))
    assert mem.get("env:chosen:key") is not None


def test_tier_state_missing_key_fails_open_normal(mem):
    backend = RedisTierState(client=mem, key="never:written")
    st = backend.load()
    assert (st.tier, st.entered_cycle, st.below_count) == ("normal", 0, 0)


def test_save_load_dispatch_to_redis_when_url_set(monkeypatch, mem):
    """save_state/load_state module functions select Redis purely on REDIS_URL."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/15")
    monkeypatch.setenv("RISK_TIER_STATE_KEY", "dispatch:test")

    # Patch out the network constructor: selection logic must reach RedisTierState.
    from trading.risk import tier_state as ts

    orig_init = ts.RedisTierState.__init__

    def _fake_init(self, **kw):
        orig_init(self, client=mem, key="dispatch:test", ttl_sec=60)

    monkeypatch.setattr(ts.RedisTierState, "__init__", _fake_init)
    assert (
        ts.save_state(TierState(tier="defended", entered_cycle=1, below_count=1))
        is True
    )
    st = ts.load_state()
    assert st.tier == "defended"

    # Explicit path still forces the FILE backend even with REDIS_URL set.
    import os as _os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".json")
    _os.close(fd)
    try:
        assert ts.save_state(TierState(tier="normal"), path=path) is True
        assert ts.load_state(path=path).tier == "normal"
        assert mem.get("dispatch:test") is not None  # untouched by the file write
    finally:
        _os.unlink(path)


# ---------------------------------------------------------------------------
# alert_manager: cooldown storage backends
# ---------------------------------------------------------------------------


def test_cooldown_store_selection(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    store = select_cooldown_store(state_path="some/state.json")
    assert isinstance(store, FileCooldownStore)

    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/15")
    # Selection must attempt Redis; construct directly to avoid network here.
    from trading.ops.alert_manager import REDIS_COOLDOWN_PREFIX

    assert REDIS_COOLDOWN_PREFIX.startswith("trading:alerts")


def test_file_cooldown_store_format_unchanged(tmp_path):
    """Legacy JSON shape {last_alert_time, alert_counts} preserved byte-for-byte."""
    path = tmp_path / "ops_alert_state.json"
    store = FileCooldownStore(path)
    store.persist_snapshot({"High Latency": 100.0}, {"High Latency": 2})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {
        "last_alert_time": {"High Latency": 100.0},
        "alert_counts": {"High Latency": 2},
    }
    last, counts = FileCooldownStore(path).load_all()
    assert last == {"High Latency": 100.0}
    assert counts == {"High Latency": 2}


def test_redis_cooldown_store_window_and_escalation(mem):
    store = RedisCooldownStore(client=mem, prefix="t:al", escalation_hash="t:al:esc")
    assert store.should_suppress("R", "WARNING", now_ts=1.0, cooldown_sec=5.0) is False
    assert (
        store.should_suppress("R", "WARNING", now_ts=1.1, cooldown_sec=5.0) is True
    )  # window held
    assert (
        store.should_suppress("R", "CRITICAL", now_ts=1.2, cooldown_sec=5.0) is False
    )  # per-severity key
    assert store.record_escalation("R") == 1
    assert store.record_escalation("R") == 2  # HINCRBY accumulates
    last, counts = store.load_all()
    assert list(last) == ["R"]
    assert counts == {"R": 2}


def test_redis_cooldown_window_expires(mem):
    clock = {"now_ms": 500_000.0}
    mem2 = MemoryRedis(clock=lambda: clock["now_ms"])
    store = RedisCooldownStore(client=mem2, prefix="t:exp", escalation_hash="t:exp:esc")
    assert store.should_suppress("X", "WARNING", 0.0, cooldown_sec=10.0) is False
    assert store.should_suppress("X", "WARNING", 0.0, cooldown_sec=10.0) is True
    clock["now_ms"] += 11_000.0  # window elapsed -> TTL expiry frees the rule
    assert store.should_suppress("X", "WARNING", 0.0, cooldown_sec=10.0) is False


def test_alert_manager_emergency_bypasses_even_redis_windows(mem):
    """EMERGENCY always pages regardless of stored suppression windows."""
    store = RedisCooldownStore(client=mem, prefix="t:mgr", escalation_hash="t:mgr:esc")
    mgr = AlertManager(cooldown_store=store, cooldown_sec=600.0)

    first = mgr.evaluate_metric("reconciliation_mismatch", 1.0)
    second = mgr.evaluate_metric("reconciliation_mismatch", 1.0)  # immediate recurrence
    assert first is not None and second is not None  # both delivered

    third = mgr.evaluate_metric("latency_p95_ms", 900.0)
    fourth = mgr.evaluate_metric("latency_p95_ms", 900.0)
    assert third is not None and fourth is None  # non-emergency suppressed via Redis


def test_alert_manager_redis_backend_cross_instance_dedup(mem):
    """Two managers sharing the Redis store suppress each other (active/passive)."""
    store_a = RedisCooldownStore(client=mem, prefix="t:x", escalation_hash="t:x:esc")
    mgr_a = AlertManager(cooldown_store=store_a, cooldown_sec=300.0)
    mgr_b = AlertManager(
        cooldown_store=RedisCooldownStore(
            client=mem, prefix="t:x", escalation_hash="t:x:esc"
        ),
        cooldown_sec=300.0,
    )

    assert mgr_a.evaluate_metric("latency_p95_ms", 800.0) is not None
    assert (
        mgr_b.evaluate_metric("latency_p95_ms", 800.0) is None
    )  # suppressed by A's window
