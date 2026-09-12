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
    canvases = re.findall(r"<canvas\b[^>]*>", _html)
    assert len(canvases) >= 3, "expected the three chart canvases"
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
    # VC-021: gate flipped from advisory to blocking after the green baseline.
    assert "continue-on-error" not in text, "a11y gate must be blocking (VC-021 flip)"
    assert "TODO(AX-1)" not in text, "TODO flip comment should be gone after VC-021"
    assert "API_INSECURE_DEV" in text and "API_SESSION_SECRET" in text
