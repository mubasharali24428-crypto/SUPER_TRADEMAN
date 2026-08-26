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


# ---------------------------------------------------------------------- W8 --
# Wave-8 additions: trading-terminal layout (Workstream A) + Apple-style
# scroll animations (Workstream B). Same grep-level static guarantees as the
# AX-1/W7 blocks so the gate runs without node.

_app_container = re.search(r'\.app-container\s*\{[^}]*\}', _css)


def test_w8_terminal_shell_layout_present():
    # sidebar rail + sticky header classes exist and are wired into a grid shell
    assert ".terminal-header" in _css and ".terminal-sidebar" in _css
    assert _app_container, ".app-container grid shell missing"
    assert "display: grid" in _app_container.group(0)
    m = re.search(r'\.terminal-header\s*\{[^}]*\}', _css)
    assert m and "position: sticky" in m.group(0), "header is not sticky"
    assert "z-index" in m.group(0), "sticky header needs a stacking tier"


def test_w8_status_pills_in_header():
    assert 'class="status-pill pill-mode"' in _html, "mode pill missing"
    assert 'class="status-pill pill-connection up"' in _html, (
        "connection pill missing"
    )
    for label in ("Engine mode: simulated",
                  "Connection status: local simulation feed online"):
        assert label in _html, f"status pill lacks accessible name: {label}"


def test_w8_sidebar_nav_structure_and_names():
    m = re.search(r'<nav[^>]*id="terminalSidebar"[^>]*>', _html)
    assert m, "#terminalSidebar nav landmark missing"
    assert 'aria-label=' in m.group(0), "sidebar nav lacks accessible name"
    items = re.findall(r'<a[^>]*class="side-nav-item[^"]*"[^>]*>', _html)
    assert len(items) == 5, f"expected 5 section nav items, got {len(items)}"
    labels = re.findall(r'class="nav-label">([^<]+)<', _html)
    for expected in ("Dashboard", "Charts", "Trades / Ledger", "Strategy",
                     "About"):
        assert expected in labels, f"nav item '{expected}' missing"
    # icons are decorative; names come from the text labels
    for icon in re.findall(r'<span class="nav-icon"[^>]*>', _html):
        assert 'aria-hidden="true"' in icon


def test_w8_hamburger_drawer_contract():
    """Escape closes the drawer + focus returns to the burger (documented in
    index.html above #btnNavToggle and implemented in app.js setDrawer)."""
    m = re.search(r'<button[^>]*id="btnNavToggle"[^>]*>', _html)
    assert m, "hamburger button #btnNavToggle missing"
    tag = m.group(0)
    assert 'aria-expanded="false"' in tag, "burger lacks initial aria-expanded"
    assert 'aria-controls="terminalSidebar"' in tag, "burger lacks aria-controls"
    assert 'aria-label=' in tag, "icon-only burger needs an aria-label"
    # behavior contract in app.js
    assert "classList.toggle('nav-drawer-open', open)" in _js, (
        "drawer open state not toggled on <body>"
    )
    assert "setAttribute('aria-expanded', String(open))" in _js, (
        "aria-expanded not kept in sync with drawer state"
    )
    assert "e.key === 'Escape' && drawerOpen()" in _js, (
        "Escape does not close the drawer"
    )
    assert "DOM.btnNavToggle.focus()" in _js, (
        "focus does not return to the burger button on close"
    )


def test_w8_workspace_split_chart_wide_ledger_right():
    m = re.search(r'\.workspace-grid\s*\{[^}]*\}', _css)
    assert m, ".workspace-grid rule vanished"
    assert "minmax(320px, 420px)" in m.group(0), (
        "right ledger column missing from workspace split"
    )
    # ledger moved INSIDE <main> so it stacks under the wide column <1200px
    main_m = re.search(r'<main\b.*?</main>', _html, flags=re.S)
    assert main_m and 'data-testid="ledger"' in main_m.group(0), (
        "ledger panel must live inside <main>"
    )


def test_w8_breakpoint_tiers_defined():
    # tier 1: full 3-zone at >=1200 (default rules); tier 2 collapse; tier 3 drawer
    assert "@media (max-width: 1199px)" in _css, "768-1199px tier missing"
    assert _css.count("@media (max-width: 768px)") >= 1
    assert "@media (max-width: 1200px)" in _css, "stacked-panels tier missing"
    assert "transform: translateX(-105%)" in _css, (
        "off-canvas drawer transform missing"
    )
    assert "body.nav-drawer-open .terminal-sidebar" in _css, (
        "drawer open state has no CSS hook"
    )


# --- W8 Workstream B: scroll animations -------------------------------------

_w8b_css_block = _css.split("W8-B: SCROLL REVEAL", 1)[-1]
_w8b_css_block = _w8b_css_block.split("VC-004", 1)[0]


def test_w8_scroll_reveal_css_present():
    assert "[data-reveal]" in _w8b_css_block, "reveal base styles missing"
    assert "transform: translateY(12px)" in _w8b_css_block, (
        "reveal must start offset by exactly 12px"
    )
    assert ".is-revealed" in _w8b_css_block, "revealed-state class missing"


def test_w8_reveal_animates_opacity_and_transform_only():
    # No layout-shifting properties may appear in the reveal transitions.
    # `transition: none` (the reduced-motion kill switch) is explicitly allowed.
    transitions = re.findall(r"transition:\s*([^;]+);", _w8b_css_block)
    assert transitions, "no reveal transition declared"
    for t in transitions:
        if t.strip().replace("!important", "").strip() == "none":
            continue  # reduced-motion kill switch, not an animated property
        props = [p.strip().split()[0] for p in t.split(",")]
        allowed = {"opacity", "transform"}
        illegal = [p for p in props if p not in allowed]
        assert not illegal, f"reveal animates layout properties: {illegal}"


def test_w8_reveal_timing_matches_spec():
    assert "300ms ease-out" in _w8b_css_block, (
        "reveal transition must be 300ms ease-out"
    )
    # staggered metrics cards: 60ms per-card delay from JS
    assert "--reveal-delay" in _w8b_css_block
    assert "60" in _js and "--reveal-delay" in _js, (
        "metrics card stagger (60ms) not driven from app.js"
    )


def test_w8_intersection_observer_feature_guarded_with_fallback():
    assert "typeof window.IntersectionObserver !== 'function'" in _js, (
        "IntersectionObserver used without a feature check"
    )
    # graceful fallback: without IO everything renders visible immediately
    guard = _js.split("function setupScrollReveals()", 1)[-1]
    guard = guard.split("function setupHeroParallax", 1)[0]
    assert "is-revealed" in guard, "fallback path does not force content visible"
    assert "new IntersectionObserver(" in _js


def test_w8_reduced_motion_disables_all_animation():
    # CSS: reveals render visible immediately, zero transforms
    assert "@media (prefers-reduced-motion: reduce)" in _w8b_css_block, (
        "reduced-motion query missing from the reveal block"
    )
    rm_rule = _w8b_css_block.split("@media (prefers-reduced-motion: reduce)", 1)[-1]
    assert "opacity: 1 !important" in rm_rule
    assert "transform: none !important" in rm_rule
    # JS: matchMedia guard skips the observer + parallax entirely
    assert "(prefers-reduced-motion: reduce)" in _js, (
        "app.js ignores prefers-reduced-motion"
    )
    assert "if (prefersReducedMotion()) return;" in _js, (
        "parallax/reveal setup lacks a reduced-motion early return"
    )


def test_w8_hero_parallax_is_subtle_and_scoped():
    para = _js.split("function setupHeroParallax", 1)[-1]
    para = para.split("function bootW8", 1)[0]
    assert "0.3" in para, "parallax must run at 0.3x scroll speed"
    assert "translateY(" in para, "hero parallax must be transform-only"
    assert "requestAnimationFrame" in para, "scroll handler must be rAF-throttled"
    # scoped to the hero band only, never the whole document
    assert ".terminal-header .brand-group" in para


def test_w8_no_new_external_dependencies():
    external_scripts = re.findall(r'<script[^>]*src="(https?:)?//[^"]*"', _html)
    assert not external_scripts, "W8 introduced CDN scripts"
    assert "innerHTML" not in _js.split("function w8TerminalShell", 1)[-1], (
        "W8 shell code must use DOM APIs only"
    )


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
