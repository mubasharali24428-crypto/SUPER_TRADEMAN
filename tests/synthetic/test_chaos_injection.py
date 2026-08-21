"""Tests for Dual-Mode Chaos Injector (Reflex & Cognition Modes)."""

import pytest

from trading.synthetic.chaos_injector import ChaosInjector
from trading.synthetic.lob import SyntheticLOB


def test_chaos_injector_instantaneous_shocks():
    lob = SyntheticLOB(initial_price=50000.0)
    injector = ChaosInjector(lob)

    # Flash crash
    new_price = injector.inject_flash_crash(0.15)
    assert new_price == 50000.0 * 0.85

    # Liquidity vacuum
    nb, na = injector.inject_liquidity_vacuum(10.0)
    assert nb < na

    # Staleness
    stale_sec = injector.inject_data_staleness()
    assert stale_sec > 10.0

    # Gap open
    gap_p = injector.inject_gap_open(0.20)
    assert gap_p == 42500.0 * 1.20


def test_chaos_injector_cascading_attacks():
    lob = SyntheticLOB(initial_price=50000.0)
    injector = ChaosInjector(lob)

    st1 = injector.execute_spoof_and_dump_stage(1)
    assert st1["action"] == "fake_buy_depth_built"

    st2 = injector.execute_spoof_and_dump_stage(2)
    assert st2["action"] == "system_long_entry_attempt"

    st3 = injector.execute_spoof_and_dump_stage(3)
    assert st3["action"] == "bids_canceled_retail_dump"

    st4 = injector.execute_spoof_and_dump_stage(4)
    assert st4["action"] == "price_collapsed_8_pct"
