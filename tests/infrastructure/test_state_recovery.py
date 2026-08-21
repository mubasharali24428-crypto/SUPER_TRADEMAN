"""Tests for State Recovery Engine & Mismatch Flattening via _ISSUER."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from trading.execution.venue_adapter import MockVenueAdapter
from trading.infrastructure.state_recovery import StateRecoveryEngine
from trading.risk.models import _ISSUER


@pytest.mark.asyncio
async def test_state_recovery_reconciled():
    venue = MockVenueAdapter()
    engine = StateRecoveryEngine(venue_adapter=venue)

    # Local positions match venue mock
    res = await engine.recover_state(
        local_positions=[{"asset": "BTC", "position_size": 0.0}],
        local_open_orders=[],
    )

    assert not res.quarantine_triggered
    assert not res.flatten_triggered
    assert len(res.exits_dispatched) == 0


@pytest.mark.asyncio
async def test_state_recovery_severe_mismatch_triggers_flatten():
    venue = MagicMock()
    venue.fetch_positions = AsyncMock(return_value=[{"symbol": "BTC", "amount": 2.0}, {"symbol": "ETH", "amount": 10.0}])
    venue.fetch_open_orders = AsyncMock(return_value=[])

    engine = StateRecoveryEngine(venue_adapter=venue)

    # Local positions zero vs venue positions -> severe mismatch -> Tier 3 Flatten via _ISSUER
    res = await engine.recover_state(
        local_positions=[{"asset": "BTC", "position_size": 0.0}, {"asset": "ETH", "position_size": 0.0}],
        local_open_orders=[],
    )

    assert res.quarantine_triggered
    assert res.flatten_triggered
    assert len(res.exits_dispatched) == 2
    for exit_obj in res.exits_dispatched:
        assert exit_obj.issuer is _ISSUER
