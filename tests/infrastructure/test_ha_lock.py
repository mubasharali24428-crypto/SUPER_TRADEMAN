"""Tests for Active-Passive HA Distributed Lock and Failover Acquisition."""

import pytest

from trading.infrastructure.ha_lock import ActivePassiveManager


def test_ha_lock_acquisition_and_heartbeat():
    node1 = ActivePassiveManager(node_id="node_1", ttl_sec=15.0)

    # Node 1 acquires primary lock
    ok1 = node1.acquire_lock(current_time=100.0)
    assert ok1
    assert node1.is_active

    # Node 1 refreshes heartbeat
    assert node1.send_heartbeat(current_time=105.0)


def test_ha_lock_failover_takeover():
    node1 = ActivePassiveManager(node_id="node_1", ttl_sec=15.0)
    node2 = ActivePassiveManager(node_id="node_2", ttl_sec=15.0)

    # Node 1 acquires lock at t=100
    node1.acquire_lock(current_time=100.0)
    node2._global_lock_state = node1._global_lock_state

    # Node 2 tries to acquire lock at t=110 (within TTL 15s) -> fails
    ok2_fail = node2.acquire_lock(current_time=110.0)
    assert not ok2_fail
    assert not node2.is_active

    # Node 2 tries to acquire lock at t=120 (after TTL 15s) -> succeeds
    ok2_pass = node2.acquire_lock(current_time=120.0)
    assert ok2_pass
    assert node2.is_active
