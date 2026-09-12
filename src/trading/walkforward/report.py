"""Tearsheet rendering for walk-forward validation cycles."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from trading.walkforward.validator import FoldResult


def folds_to_json(folds: list[FoldResult]) -> list[dict[str, Any]]:
    return [asdict(f) for f in folds]


def render_tearsheet(
    folds: list[FoldResult], meta: dict[str, Any] | None = None
) -> str:
    meta = meta or {}
    counts: dict[str, int] = {}
    for f in folds:
        counts[f.verdict] = counts.get(f.verdict, 0) + 1

    lines: list[str] = []
    lines.append("# Walk-Forward Tearsheet")
    if meta:
        for k, v in meta.items():
            lines.append(f"- **{k}:** {v}")
    lines.append("")
    lines.append("## Verdict summary")
    lines.append("")
    lines.append("| Verdict | Folds |")
    lines.append("|---|---|")
    for verdict in ("PASS", "FAIL", "SKIP"):
        if counts.get(verdict):
            lines.append(f"| {verdict} | {counts[verdict]} |")
    passed = counts.get("PASS", 0)
    decided = counts.get("PASS", 0) + counts.get("FAIL", 0)
    ratio = f"{passed}/{decided}" if decided else "n/a"
    lines.append(f"| **Pass rate** | **{ratio}** |")

    lines.append("")
    lines.append("## Per-fold detail")
    lines.append("")
    lines.append(
        "| Fold | Train | Validate | Sharpe | DSR | PBO | Trades | Verdict | Reason |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for f in folds:
        sharpe = f"{f.sharpe:.3f}" if f.sharpe is not None else "—"
        dsr = f"{f.dsr:.3f}" if f.dsr is not None else "—"
        pbo = f"{f.pbo:.3f}" if f.pbo is not None else "—"
        reason = f.reason or "—"
        lines.append(
            f"| {f.fold_idx} | {f.train_range[0]}–{f.train_range[1]}d "
            f"| {f.valid_range[0]}–{f.valid_range[1]}d "
            f"| {sharpe} | {dsr} | {pbo} | {f.num_trades} | {f.verdict} | {reason} |"
        )

    lines.append("")
    lines.append(
        "*Verdicts are CSCV/DSR-corrected (see stats stack). FAIL means the edge "
        "did not survive out-of-sample validation — treat as suggestive-only.*"
    )
    return "\n".join(lines)


def to_json_report(folds: list[FoldResult], meta: dict[str, Any] | None = None) -> str:
    """JSON persistence format for evidence artifacts."""
    payload = {
        "meta": meta or {},
        "folds": folds_to_json(folds),
        "verdict_counts": _verdict_counts(folds),
    }
    return json.dumps(payload, indent=2, default=str)


def _verdict_counts(folds: list[FoldResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in folds:
        counts[f.verdict] = counts.get(f.verdict, 0) + 1
    return counts
