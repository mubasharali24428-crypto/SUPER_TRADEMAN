"""Tests for trading.synthetic.venue.SyntheticVenue.

This module previously shipped with a SyntaxError (stray paren in a duplicated
``def fetch_positions(()`` stub) that no test caught, and even once importable it
was an abstract class that could not instantiate (missing VenueAdapter overrides).
These tests close both holes: importing this module fails loudly if venue.py stops
parsing, and ``SyntheticVenue.__abstractmethods__`` must stay empty (F-0002, F-0003).

Fill determinism: SyntheticLOB applies a stochastic queue-slip check
(``random.random() > fill_probability`` -> order slips). All fixtures build the
LOB with ``base_fill_probability=1.0``, which makes the slip condition impossible
and every fill deterministic.
"""

import pytest

from trading.execution.venue_adapter import InstrumentInfo, VenueAdapter
from trading.risk.models import _ISSUER, ApprovedExit, ApprovedOrder, Side
from trading.synthetic.lob import LimitOrder, SyntheticLOB
from trading.synthetic.venue import SyntheticVenue

SEED_QTY = 100.0


def _make_order(
    asset: str = "BTC/USDT",
    side: Side = Side.LONG,
    entry_price: float = 50_000.0,
    position_size: float = 2.0,
) -> ApprovedOrder:
    """ApprovedOrder minted with the real Risk Engine token (repo test convention)."""
    return ApprovedOrder(
        asset=asset,
        asset_class="crypto",
        side=side,
        entry_price=entry_price,
        stop_price=entry_price * 0.98,
        target_price=entry_price * 1.05,
        position_size=position_size,
        risk_pct=0.01,
        issuer=_ISSUER,
    )


def _make_exit(asset: str = "BTC/USDT", reason: str = "test_exit") -> ApprovedExit:
    return ApprovedExit(
        asset=asset,
        asset_class="crypto",
        reason=reason,
        issuer=_ISSUER,
    )


@pytest.fixture()
def venue() -> SyntheticVenue:
    """Venue over a deterministic, deeply-seeded book.

    Seed levels sit +/- $1 around mid with SEED_QTY depth, so any order priced
    through a level fills fully and any order priced inside the spread rests.
    """
    lob = SyntheticLOB(initial_price=50_000.0, base_fill_probability=1.0)
    now_ms = 1_000_000.0
    lob.bids = [LimitOrder("seed_b1", "buy", 49_999.0, SEED_QTY, now_ms, "mm_seed")]
    lob.asks = [LimitOrder("seed_a1", "sell", 50_001.0, SEED_QTY, now_ms, "mm_seed")]
    return SyntheticVenue(lob=lob)


class TestInstantiation:
    def test_synthetic_venue_instantiates_concrete(self):
        """Regression gate for F-0002/F-0003: module imports AND class is concrete."""
        assert issubclass(SyntheticVenue, VenueAdapter)
        assert SyntheticVenue.__abstractmethods__ == frozenset()
        v = SyntheticVenue()
        assert isinstance(v, VenueAdapter)

    def test_default_lob_created_when_none_given(self):
        v = SyntheticVenue()
        assert isinstance(v.lob, SyntheticLOB)
        assert v.open_orders == {}
        assert v.open_positions == {}


class TestCreateOrder:
    async def test_marketable_order_fills_fully(self, venue):
        # Buy priced at the ask -> crosses -> full fill of 2.0
        resp = await venue.create_order(_make_order(entry_price=50_001.0), "coid-buy-1")

        assert resp["clientOrderId"] == "coid-buy-1"
        assert resp["id"].startswith("syn_")
        assert resp["status"] == "FILLED"
        assert resp["filled_qty"] == pytest.approx(2.0)
        assert len(resp["fills"]) == 1
        assert resp["fills"][0]["qty"] == pytest.approx(2.0)
        # Fill is registered venue-side and the order stays tracked
        assert venue.filled_qty_by_order["coid-buy-1"] == pytest.approx(2.0)
        assert "coid-buy-1" in venue.open_orders
        assert "BTC/USDT" in venue.open_positions

    async def test_non_marketable_order_rests(self, venue):
        # Buy priced well below the ask -> no crossing -> rests in book
        resp = await venue.create_order(_make_order(entry_price=49_900.0), "coid-rest")

        assert resp["status"] == "SUBMITTED"
        assert resp["filled_qty"] == pytest.approx(0.0)


class TestCreateExit:
    async def test_exit_flattens_existing_position_at_best_bid(self, venue):
        await venue.create_order(_make_order(entry_price=50_001.0), "coid-open")
        assert "BTC/USDT" in venue.open_positions

        resp = await venue.create_exit(_make_exit(reason="risk_cut"), "coid-exit")

        assert resp["clientOrderId"] == "coid-exit"
        assert resp["status"] == "FILLED"
        assert resp["filled_qty"] == pytest.approx(2.0)
        # Long exit sells into the best bid ($49,999 seed level)
        assert resp["price"] == pytest.approx(49_999.0)
        assert "BTC/USDT" not in venue.open_positions


class TestCancelOrder:
    async def test_cancel_resting_order(self, venue):
        await venue.create_order(_make_order(entry_price=49_900.0), "coid-cancel")
        assert "coid-cancel" in venue.open_orders

        resp = await venue.cancel_order("coid-cancel", "BTC/USDT")

        assert resp == {
            "clientOrderId": "coid-cancel",
            "symbol": "BTC/USDT",
            "status": "canceled",
        }
        assert "coid-cancel" not in venue.open_orders
        assert await venue.fetch_order("coid-cancel", "BTC/USDT") is None

    async def test_cancel_unknown_order_reports_not_found(self, venue):
        resp = await venue.cancel_order("never-existed", "BTC/USDT")
        assert resp["status"] == "not_found"


class TestFetchOrder:
    async def test_known_order_lifecycle_open_then_filled(self, venue):
        # Resting first...
        await venue.create_order(_make_order(entry_price=49_900.0), "coid-life")
        resting = await venue.fetch_order("coid-life", "BTC/USDT")
        assert resting["status"] == "open"
        assert resting["filled_qty"] == pytest.approx(0.0)

    async def test_filled_order_reports_filled(self, venue):
        await venue.create_order(_make_order(entry_price=50_001.0), "coid-fill")
        fetched = await venue.fetch_order("coid-fill", "BTC/USDT")
        assert fetched["status"] == "filled"
        assert fetched["filled_qty"] == pytest.approx(2.0)

    async def test_unknown_order_returns_none(self, venue):
        assert await venue.fetch_order("ghost", "BTC/USDT") is None


class TestFetchPositions:
    async def test_long_and_short_positions_report_signed_amounts(self, venue):
        await venue.create_order(
            _make_order(
                asset="BTC/USDT",
                side=Side.LONG,
                entry_price=50_001.0,
                position_size=2.0,
            ),
            "coid-long",
        )
        await venue.create_order(
            _make_order(
                asset="ETH/USDT",
                side=Side.SHORT,
                entry_price=49_999.0,
                position_size=5.0,
            ),
            "coid-short",
        )

        positions = {p["symbol"]: p for p in await venue.fetch_positions()}

        assert positions["BTC/USDT"]["amount"] == pytest.approx(2.0)
        assert positions["ETH/USDT"]["amount"] == pytest.approx(-5.0)
        assert positions["BTC/USDT"]["entry_price"] == pytest.approx(50_001.0)

    async def test_no_positions_returns_empty_list(self, venue):
        assert await venue.fetch_positions() == []


class TestFetchOpenOrders:
    async def test_lists_all_resting_orders_with_their_ids(self, venue):
        await venue.create_order(_make_order(entry_price=49_900.0), "coid-a")
        await venue.create_order(
            _make_order(asset="ETH/USDT", entry_price=49_800.0), "coid-b"
        )

        open_orders = await venue.fetch_open_orders()

        ids = {o["client_order_id"] for o in open_orders}
        assert ids == {"coid-a", "coid-b"}
        by_id = {o["client_order_id"]: o for o in open_orders}
        assert by_id["coid-a"]["symbol"] == "BTC/USDT"

    async def test_symbol_filter_narrows_results(self, venue):
        await venue.create_order(_make_order(entry_price=49_900.0), "coid-btc")
        await venue.create_order(
            _make_order(asset="ETH/USDT", entry_price=49_800.0), "coid-eth"
        )

        btc_only = await venue.fetch_open_orders(symbol="BTC/USDT")

        assert [o["client_order_id"] for o in btc_only] == ["coid-btc"]


class TestGetInstrumentInfo:
    async def test_unknown_symbol_gets_default_filters(self, venue):
        info = await venue.get_instrument_info("DOGE/USDT")
        assert isinstance(info, InstrumentInfo)
        assert info.symbol == "DOGE/USDT"
        assert info.min_qty == InstrumentInfo(symbol="X").min_qty

    async def test_registered_symbol_returns_custom_filters(self, venue):
        custom = InstrumentInfo(symbol="BTC/USDT", min_qty=0.01, min_notional=25.0)
        venue.instrument_info_map["BTC/USDT"] = custom

        info = await venue.get_instrument_info("BTC/USDT")

        assert info is custom
        assert info.min_qty == 0.01


class TestSubmitApprovedOrderBackwardCompat:
    async def test_wrapper_without_explicit_id_generates_one(self, venue):
        """Legacy call shape still works: id derived venue-side, response echoes it."""
        resp = await venue.submit_approved_order(_make_order(entry_price=50_001.0))

        assert resp["client_order_id"].startswith("ord_BTC/USDT_")
        assert resp["status"] == "FILLED"
        assert resp["client_order_id"] in venue.open_orders
