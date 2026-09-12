"""SUB-09 tests: wire-or-delete adjudication + LOB realism fixes.

Covers:
1. _rsi regression (Wilder smoothing + short-window NaN guard) -- the bug lived
   in src/trading/strategy/crypto.py, not src/trading/data/crypto.py as briefed;
   see tests/test_wire_or_delete.md deviation note.
2. fetch_ohlcv_range pagination termination with a fake ccxt client object +
   rows-fetched logging (src/trading/data/crypto.py).
3. Decision-log completeness: every orphan module must carry a Verdict and
   call-site Evidence lines in tests/test_wire_or_delete.md.
"""

import asyncio
import math
import re
from pathlib import Path

import pytest

from trading.data.crypto import fetch_ohlcv_range
from trading.strategy.crypto import _rsi

REPO_ROOT = Path(__file__).resolve().parents[2]
DECISION_LOG = Path(__file__).resolve().parents[1] / "test_wire_or_delete.md"


# --------------------------------------------------------------------------
# 1. _rsi regression tests
# --------------------------------------------------------------------------


def _wilder_rsi_reference(closes, period):
    """Independent reference implementation of Wilder's RSI."""
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    seed = deltas[:period]
    avg_gain = sum(d for d in seed if d > 0) / period
    avg_loss = sum(-d for d in seed if d < 0) / period
    for d in deltas[period:]:
        g = d if d > 0 else 0.0
        l = -d if d < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def test_rsi_short_window_returns_nan_not_garbage():
    # Fewer than period+1 closes: old code divided a truncated sum by `period`
    # and returned garbage; contract now is NaN.
    closes = [100.0, 101.0, 99.0]
    for period in (14, 5):
        assert math.isnan(_rsi(closes, period))
    # Exactly period+1 closes is the minimum valid window -> NOT NaN.
    closes = [100.0 + i for i in range(15)]
    assert not math.isnan(_rsi(closes, 14))


def test_rsi_nonpositive_period_returns_nan():
    assert math.isnan(_rsi([100.0, 101.0], 0))
    assert math.isnan(_rsi([100.0, 101.0], -3))


def test_rsi_uses_wilder_smoothing_not_plain_last_window_mean():
    # 40 falling bars then 40 rising bars. Plain "mean of last 14 deltas" sees
    # only gains -> avg_loss == 0 -> returns 100.0. Wilder smoothing carries the
    # early losses forward (exponentially decaying), so RSI must be < 100.
    closes = [200.0 - i for i in range(41)] + [160.0 + 2 * (i + 1) for i in range(39)]
    rsi = _rsi(closes, 14)
    assert (
        rsi < 100.0
    ), "early losses must survive Wilder smoothing (regression to plain-mean bug)"
    assert rsi > 50.0, "strong late rally should still dominate"
    expected = _wilder_rsi_reference(closes, 14)
    assert math.isclose(rsi, expected, rel_tol=1e-12)


def test_rsi_matches_wilder_reference_on_mixed_series():
    closes = [
        100,
        102,
        101,
        103,
        104,
        102,
        105,
        107,
        106,
        108,
        107,
        109,
        111,
        110,
        112,
        113,
        111,
        114,
        116,
        115,
        117,
        116,
        118,
        120,
        119,
        121,
        123,
        122,
        124,
        125,
    ]
    for period in (5, 14):
        assert math.isclose(
            _rsi([float(c) for c in closes], period),
            _wilder_rsi_reference([float(c) for c in closes], period),
            rel_tol=1e-12,
        )


def test_rsi_all_gains_still_caps_at_100():
    closes = [float(100 + i) for i in range(31)]
    assert _rsi(closes, 14) == 100.0


# --------------------------------------------------------------------------
# 2. fetch_ohlcv_range pagination tests (fake ccxt client object)
# --------------------------------------------------------------------------

TF_MS = 60_000


class FakeCcxtExchange:
    """Fake ccxt client: serves a scripted list of OHLCV pages, records calls."""

    def __init__(self, pages, fail_after=None):
        self._pages = list(pages)
        self._fail_after = fail_after
        self.calls = []

    def parse_timeframe(self, timeframe):
        return TF_MS // 1000

    def fetch_ohlcv(self, symbol, timeframe, since, limit):
        self.calls.append(
            {"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit}
        )
        if self._fail_after is not None and len(self.calls) > self._fail_after:
            raise AssertionError(
                "fetch_ohlcv called after pagination should have terminated"
            )
        return self._pages.pop(0) if self._pages else []


def _page(start_ts, n):
    return [[start_ts + i * TF_MS, 1.0, 2.0, 0.5, 1.5, 10.0] for i in range(n)]


def test_pagination_stops_when_page_shorter_than_limit():
    # Exchange history exhausted: second page returns fewer rows than requested.
    ex = FakeCcxtExchange([_page(0, 3), _page(3 * TF_MS, 2)])
    until = 100 * TF_MS  # far beyond available data
    candles = asyncio.run(fetch_ohlcv_range(ex, "BTC/USDT", "1m", 0, until, limit=3))
    assert len(ex.calls) == 2, "must stop after the short page, not keep requesting"
    assert [c[0] for c in candles] == [0, TF_MS, 2 * TF_MS, 3 * TF_MS, 4 * TF_MS]


def test_pagination_stops_on_empty_page():
    ex = FakeCcxtExchange([_page(0, 3)])
    candles = asyncio.run(
        fetch_ohlcv_range(ex, "BTC/USDT", "1m", 0, 100 * TF_MS, limit=3)
    )
    # Call 1: full page -> cursor advances; call 2: exchange returns nothing ->
    # must terminate there (no 3rd call).
    assert len(ex.calls) == 2
    assert len(candles) == 3


def test_pagination_stops_on_exhausted_nonadvancing_cursor(caplog):
    import logging

    # Pathological exchange answers with a FULL page whose newest candle is
    # OLDER than the requested cursor (fully-stale data): next_cursor <= cursor.
    # Must terminate immediately instead of looping forever.
    stale = _page(-3 * TF_MS, 3)  # timestamps strictly before since=0
    ex = FakeCcxtExchange([stale])
    with caplog.at_level(logging.WARNING, logger="trading.data.crypto"):
        candles = asyncio.run(
            fetch_ohlcv_range(ex, "BTC/USDT", "1m", 0, 100 * TF_MS, limit=3)
        )
    assert len(ex.calls) == 1, "non-advancing cursor must terminate pagination"
    assert any("did not advance" in r.message for r in caplog.records)


def test_pagination_filters_rows_at_or_beyond_until():
    ex = FakeCcxtExchange([_page(0, 3)])
    until = 2 * TF_MS
    candles = asyncio.run(fetch_ohlcv_range(ex, "BTC/USDT", "1m", 0, until, limit=10))
    assert all(c[0] < until for c in candles)
    assert [c[0] for c in candles] == [0, TF_MS]


def test_pagination_logs_rows_fetched(caplog):
    import logging

    ex = FakeCcxtExchange([_page(0, 3), _page(3 * TF_MS, 2)])
    with caplog.at_level(logging.INFO, logger="trading.data.crypto"):
        candles = asyncio.run(
            fetch_ohlcv_range(ex, "BTC/USDT", "1m", 0, 100 * TF_MS, limit=3)
        )
    total_line = [r.message for r in caplog.records if "complete" in r.message]
    assert total_line, "final rows-fetched log line missing"
    assert "5 rows fetched" in total_line[-1]
    assert any("-> 3 rows" in r.message for r in caplog.records)


# --------------------------------------------------------------------------
# 3. Decision-log completeness assert
# --------------------------------------------------------------------------

ORPHAN_MODULES = [
    "backtest/funding.py",
    "backtest/impact.py",
    "synthetic/stale_protection.py",
    "synthetic/ecology.py",
    "synthetic/event_ingestor.py",
]


def test_decision_log_covers_every_orphan_with_verdict_and_evidence():
    text = DECISION_LOG.read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.MULTILINE)[1:]
    by_target = {}
    for sec in sections:
        header = sec.splitlines()[0].strip()
        for mod in ORPHAN_MODULES:
            if header.endswith(mod):
                by_target[mod] = sec
                break
    missing = [m for m in ORPHAN_MODULES if m not in by_target]
    assert not missing, f"decision log missing sections for: {missing}"
    for mod, sec in by_target.items():
        verdicts = re.findall(r"^Verdict:\s*(\S+)", sec, flags=re.MULTILINE)
        evidence_lines = re.findall(r"^Evidence:", sec, flags=re.MULTILINE)
        assert verdicts, f"{mod}: no 'Verdict:' line"
        assert verdicts[0] in {
            "DELETE",
            "KEEP",
        }, f"{mod}: verdict must be DELETE or KEEP, got {verdicts[0]}"
        assert (
            len(evidence_lines) >= 1
        ), f"{mod}: no 'Evidence:' call-site citation lines"
