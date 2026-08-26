"""Walk-forward validation tests: fold math, verdicts, tearsheet, scheduler."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trading.walkforward.report import folds_to_json, render_tearsheet  # noqa: E402
from trading.walkforward.scheduler import should_run  # noqa: E402
from trading.walkforward.validator import (  # noqa: E402
    FoldResult,
    WalkForwardConfig,
    generate_folds,
    run_walk_forward,
)


# ---------- fold generation ----------

def test_generate_folds_golden_boundaries():
    cfg = WalkForwardConfig(train_days=180, validate_days=60, step_days=30)
    folds = generate_folds(400, cfg)
    # first fold: train 0-180, validate 180-240
    assert folds[0] == ((0, 180), (180, 240))
    # each subsequent fold steps by 30
    assert folds[1][0] == (30, 210) and folds[1][1] == (210, 270)
    # last fold's validate end must not exceed total
    assert folds[-1][1][1] <= 400
    # starts {0,30,...,160} with valid_end<=400 -> 6 folds for a 400-day series
    assert len(folds) == 6


def test_generate_folds_insufficient_total():
    cfg = WalkForwardConfig(train_days=180, validate_days=60)
    assert generate_folds(200, cfg) == []


def test_scheduler_should_run_boundaries():
    assert should_run(None) is True
    now = 1_000_000.0
    day = 86_400.0
    assert should_run(now - 6 * day, now_ts=now, staleness_days=7) is False
    assert should_run(now - 7 * day, now_ts=now, staleness_days=7) is True
    assert should_run(now - 30 * day, now_ts=now, staleness_days=7) is True


# ---------- tearsheet ----------

def test_render_tearsheet_contains_summary_and_rows():
    folds = [
        FoldResult(0, (0, 180), (180, 240), 1.2, 0.9, 0.3, "PASS", "", 12),
        FoldResult(1, (30, 210), (210, 270), None, None, None, "SKIP", "insufficient data"),
        FoldResult(2, (60, 240), (240, 300), 0.1, 0.2, 0.8, "FAIL", "pbo 0.800 > 0.5"),
    ]
    md = render_tearsheet(folds, meta={"symbol": "BTC/USDT"})
    assert "# Walk-Forward Tearsheet" in md
    assert "| PASS | 1 |" in md
    assert "| SKIP | 1 |" in md
    assert "| FAIL | 1 |" in md
    assert "BTC/USDT" in md

    js = folds_to_json(folds)
    assert js[0]["verdict"] == "PASS"
    assert js[2]["reason"].startswith("pbo")
