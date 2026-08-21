"""Tests for Operational Kill-Switch Drills."""

import pytest

from trading.config import ExecutionMode
from trading.ops.drills import OperationalDrillHarness


@pytest.mark.asyncio
async def test_operational_drill_harness():
    harness = OperationalDrillHarness(mode=ExecutionMode.SHADOW)
    results = await harness.run_all_drills()

    assert len(results) == 7
    for res in results:
        assert res.status == "PASS"
        assert len(res.events_observed) >= 1
