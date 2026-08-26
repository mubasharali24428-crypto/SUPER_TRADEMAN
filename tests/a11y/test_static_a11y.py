"""
AX-1 static accessibility checks (node-free).

Grep-level guarantees so the accessibility gate runs even in CI jobs without
node/npm installed. Complements `tests/a11y/axe_check.mjs`, which does the
real rendered-DOM axe-core scan when a browser is available.

Run: .venv/bin/python -m pytest tests/a11y -q
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB = REPO_ROOT / "web"

_html = (WEB / "index.html").read_text(encoding="utf-8")
_js = (WEB / "app.js").read_text(encoding="utf-8")
_css = (WEB / "style.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------- index.html -

def test_semantic_landmarks_present():
    for tag in ("<header", "<nav", "<main", "<footer"):
        assert tag in _html, f"missing semantic landmark {tag}"


def test_skip_link_present_and_targets_main():
    assert 'class="skip-link"' in _html, "skip-link missing"
    assert 'href="#main-content"' in _html
    assert 'id="main-content"' in _html, "skip-link target #main-content missing"


def test_aria_live_polite_status_region_exists():
    assert 'aria-live="polite"' in _html, "no polite live region in DOM"
    assert 'role="status"' in _html, "live region lacks role=status"


def test_every_canvas_has_text_alternative():
    # W7: only the force-directed learning-graph canvas remains hand-drawn.
    # The two time-series canvases (#tradingCanvas / #equityCanvas) were
    # replaced by vendored lightweight-charts hosts exposing role="img" +
    # aria-label instead (see test_chart_hosts_expose_role_img_and_label).
    canvases = re.findall(r"<canvas\b[^>]*>", _html)
    assert len(canvases) == 1, "expected exactly the learning-graph canvas"
    for tag in canvases:
        assert "aria-label=" in tag, f"canvas without text alternative: {tag}"


def test_interactive_controls_have_aria_labels():
    # Every <button ...> must carry an aria-label or have inner text; here we
    # assert the icon-only / symbol-only controls are labeled explicitly.
    for btn_id in ("btnCloseModal", "btnCenterGraph", "btnClearGraph"):
        m = re.search(rf'<button[^>]*id="{btn_id}"[^>]*>', _html)
        assert m, f"button #{btn_id} vanished from index.html"
        assert "aria-label=" in m.group(0), f"#{btn_id} missing aria-label"


def test_form_labels_are_programmatically_associated():
    for control_id in (
        "assetSelect",
        "modalCandleCount",
        "modalRiskPct",
        "modalMaxDd",
        "modalAtrMult",
    ):
        assert f'id="{control_id}"' in _html, f"#{control_id} missing"
        assert f'for="{control_id}"' in _html, (
            f"label for={control_id} not associated"
        )


def test_tablist_and_dialog_semantics():
    assert 'role="tablist"' in _html
    assert _html.count('role="tab"') >= 3
    assert 'role="tabpanel"' in _html
    assert 'role="dialog"' in _html and 'aria-modal="true"' in _html
    assert "aria-labelledby=" in _html


def test_key_ax_regions_have_data_testids():
    for tid in (
        "data-testid=\"header\"",
        "data-testid=\"main\"",
        "data-testid=\"sidebar\"",
        "data-testid=\"ledger\"",
        "data-testid=\"ledger-table-body\"",
        "data-testid=\"live-region\"",
        "data-testid=\"footer\"",
    ):
        assert tid in _html, f"missing {tid} hook for axe harness"


def test_honest_simulation_labeling():
    assert "SIMULATED" in _html.upper(), (
        "dashboard must be visibly/honestly labeled as simulated data"
    )


# ------------------------------------------------------------------- app.js --

def test_no_bare_innerhtml_sinks_remain():
    sinks = re.findall(r"\.innerHTML\s*=", _js)
    assert not sinks, f"innerHTML assignment sinks remain: {len(sinks)}"


def test_rows_built_with_dom_apis_not_string_html():
    assert "createElement" in _js, "ledger/benchmark rows not built via DOM APIs"
    assert "textContent" in _js
    assert "replaceChildren" in _js


def test_win_loss_badges_use_icon_plus_text():
    up = "▲" in _js or r"\u25B2" in _js
    down = "▼" in _js or r"\u25BC" in _js
    assert up and down, "direction glyphs missing from status badges"
    assert "WIN" in _js and "LOSS" in _js


def test_custom_controls_have_keyboard_handlers():
    assert "keydown" in _js, "no keydown handlers (tablist/modal)"


def test_js_updates_the_status_region():
    assert "engineStatusRegion" in _js, "live region never updated by app.js"


# ---------------------------------------------------------------------- W7 --
# Wave-7 additions: interactive lightweight-charts (Workstream A) and the KPI
# stats-card band (Workstream B). Static grep-level guarantees mirroring the
# AX-1 approach so the gate runs without node.

_charts_js = (WEB / "charts.js").read_text(encoding="utf-8")
_vendor = WEB / "vendor" / "lightweight-charts.standalone.js"


def test_w7_lightweight_charts_is_vendored_not_cdn():
    assert _vendor.exists(), "web/vendor/lightweight-charts.standalone.js missing"
    vendor_text = _vendor.read_text(encoding="utf-8", errors="replace")
    assert "TradingView Lightweight Charts" in vendor_text, (
        "vendored file does not look like the genuine library"
    )
    # index.html loads it same-origin only
    assert 'src="vendor/lightweight-charts.standalone.js"' in _html
    # no external script URLs anywhere (CSP default-src 'self')
    external_scripts = re.findall(r'<script[^>]*src="(https?:)?//[^"]*"', _html)
    assert not external_scripts, f"CDN script tags present: {external_scripts}"


def test_w7_chart_hosts_expose_role_img_and_label():
    for host_id in ("priceChartHost", "equityChartHost"):
        m = re.search(rf'<div[^>]*id="{host_id}"[^>]*>', _html)
        assert m, f"#{host_id} missing from index.html"
        tag = m.group(0)
        assert 'role="img"' in tag, f"#{host_id} lost role=img"
        assert "aria-label=" in tag, f"#{host_id} lost its text alternative"


def test_w7_hidden_data_tables_present_for_screen_readers():
    for tbl_id, body_id in (("priceDataTable", "priceTableBody"),
                            ("equityDataTable", "equityTableBody")):
        assert f'id="{tbl_id}"' in _html, f"hidden table #{tbl_id} missing"
        assert f'id="{body_id}"' in _html, f"hidden table body #{body_id} missing"
    # tables are visually hidden but machine-present (WCAG mirror of chart data)
    assert re.search(r'<table[^>]*class="visually-hidden"[^>]*id="priceDataTable"',
                     _html), "price data table not visually-hidden"
    # rows are populated by charts.js via DOM APIs
    assert "priceTableBody" in _charts_js and "equityTableBody" in _charts_js


def test_w7_stats_band_exists_with_aria_live():
    m = re.search(r'<section[^>]*id="statsBand"[^>]*>', _html)
    assert m, "#statsBand section missing"
    tag = m.group(0)
    assert 'aria-live="polite"' in tag, "stats band lacks aria-live=polite"
    assert "aria-label=" in tag, "stats band lacks accessible name"
    assert _html.count('class="stat-card') == 4, "expected exactly 4 stat cards"
    for val_id in ("statEquityValue", "statPnlValue", "statWinRateValue",
                   "statOpenRiskValue"):
        assert f'id="{val_id}"' in _html, f"KPI value #{val_id} missing"
    # throttled spoken summary inside the live region
    assert 'id="statsLiveSummary"' in _html
    assert "statsLiveSummary" in _js, "app.js never updates the stats live region"


def test_w7_stat_values_use_tabular_numerals():
    assert "font-variant-numeric: tabular-nums" in _css, (
        "stat values lack tabular-nums (digits jitter as they tick)"
    )


def test_w7_delta_arrows_carry_glyph_and_words():
    up = "\u25b2" in _js or r"\u25B2" in _js
    down = "\u25bc" in _js or r"\u25BC" in _js
    assert up and down, "W7 delta arrows missing glyph coverage"
    assert "vs open" in _js, "delta text lacks non-color direction words"


def test_w7_chart_data_isolated_behind_getchartdata():
    assert "function getChartData()" in _js, "getChartData() provider missing"
    assert "init({ getChartData })" in _js, "provider not injected into W7Charts"
    # charts.js must be fully decoupled: no direct reach into STATE/generator
    assert "STATE" not in _charts_js, "charts.js reaches into app state directly"
    assert "Math.random" not in _charts_js, "charts.js generates its own data"


def test_w7_charts_respect_reduced_motion():
    assert "prefers-reduced-motion" in _charts_js, (
        "charts.js ignores prefers-reduced-motion"
    )


def test_w7_charts_module_has_no_innerhtml_sinks():
    sinks = re.findall(r"\.innerHTML\s*=", _charts_js)
    assert not sinks, f"innerHTML sinks in charts.js: {len(sinks)}"
    assert "createElement" in _charts_js and "textContent" in _charts_js
    assert "replaceChildren" in _charts_js


# --------------------------------------------------------------- style.css --

def test_global_focus_visible_ring_present():
    assert ":focus-visible" in _css, "no :focus-visible ring defined"


def test_outline_none_removed():
    assert "outline: none" not in _css, "outline:none focus removal still present"


def test_muted_text_meets_aa_contrast():
    # #64748b was 4.18:1 on --bg-primary / 3.96:1 on --bg-secondary.
    # #708198 measures 5.01:1 / 4.75:1 (computed WCAG luminance ratios).
    assert "--text-muted: #708198;" in _css, (
        "muted text color not bumped to AA-compliant #708198"
    )
    # Strip /* */ comments so historical values quoted in documentation
    # comments don't count as live usage.
    live_css = re.sub(r"/\*.*?\*/", "", _css, flags=re.S)
    assert "#64748b" not in live_css, (
        "old low-contrast #64748b still used in live CSS rules"
    )


# ------------------------------------------------------------------ CI gate --

def test_a11y_workflow_defines_axe_gate():
    wf = REPO_ROOT / ".github" / "workflows" / "a11y.yml"
    assert wf.exists(), "missing .github/workflows/a11y.yml"
    text = wf.read_text(encoding="utf-8")
    assert "axe_check.mjs" in text
    assert "continue-on-error: true" in text, "gate should start as allow-failure"
    assert "TODO(AX-1)" in text, "missing TODO flip comment"
    assert "API_INSECURE_DEV" in text and "API_SESSION_SECRET" in text
