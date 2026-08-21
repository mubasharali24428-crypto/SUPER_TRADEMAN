"""Shadow Campaign Orchestrator & Gate 1 Continuous Evaluator."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from trading.observability.logger import get_logger
from trading.ops.deployment_metrics import DeploymentMetricRecord, DeploymentMetricsStore

__all__ = [
    "ShadowCampaignSummary",
    "ShadowCampaign",
]

logger = get_logger("trading.ops.shadow_campaign")


@dataclass
class ShadowCampaignSummary:
    days_evaluated: int
    tracking_error_z_score: float
    avg_slippage_bps: float
    latency_p99_ms: float
    shadow_pnl_pct: float
    campaign_status: str  # "IN_PROGRESS", "GATE_1_PASS", "GATE_1_FAIL"
    consecutive_breaches: int = 0
    failure_reasons: List[str] = field(default_factory=list)


class ShadowCampaign:
    """Orchestrates 30-day Shadow Mode validation campaign and evaluates Gate 1 criteria."""

    def __init__(
        self,
        store: Optional[DeploymentMetricsStore] = None,
        max_z_score_threshold: float = 3.0,
        max_slippage_bps_threshold: float = 25.0,
    ):
        self.store = store or DeploymentMetricsStore()
        self.max_z_score_threshold = max_z_score_threshold
        self.max_slippage_bps_threshold = max_slippage_bps_threshold
        self.daily_records: List[DeploymentMetricRecord] = []
        self.consecutive_breaches = 0

    def record_daily_metrics(self, record: DeploymentMetricRecord) -> None:
        self.daily_records.append(record)
        self.store.record_metrics(record)

    def evaluate_campaign_status(
        self,
        backtest_expected_pnl_pct: float = 0.05,
        backtest_std_dev: float = 0.02,
    ) -> ShadowCampaignSummary:
        """Evaluates campaign progress, tracking error z-score, and hard-stop thresholds."""
        if not self.daily_records:
            return ShadowCampaignSummary(
                days_evaluated=0,
                tracking_error_z_score=0.0,
                avg_slippage_bps=0.0,
                latency_p99_ms=0.0,
                shadow_pnl_pct=0.0,
                campaign_status="IN_PROGRESS",
            )

        days_count = len(self.daily_records)
        cum_pnl = sum(r.shadow_pnl_pct for r in self.daily_records)
        avg_slippage = sum(r.avg_shadow_slippage_bps for r in self.daily_records) / days_count
        p99_lat = max(r.p99_signal_to_fill_latency_ms for r in self.daily_records)

        z_score = (cum_pnl - backtest_expected_pnl_pct) / backtest_std_dev if backtest_std_dev > 0 else 0.0

        # Compute max consecutive threshold breaches across all records
        consecutive_breaches = 0
        max_consecutive = 0
        expected_daily_pnl = backtest_expected_pnl_pct / max(1, days_count)

        for rec in self.daily_records:
            rec_z = (rec.shadow_pnl_pct - expected_daily_pnl) / backtest_std_dev if backtest_std_dev > 0 else 0.0
            is_breach = abs(rec_z) > self.max_z_score_threshold or rec.avg_shadow_slippage_bps > self.max_slippage_bps_threshold
            if is_breach:
                consecutive_breaches += 1
                max_consecutive = max(max_consecutive, consecutive_breaches)
            else:
                consecutive_breaches = 0

        self.consecutive_breaches = max_consecutive

        failures = []
        if self.consecutive_breaches >= 3:
            failures.append(f"HARD_STOP_TRIGGERED: {self.consecutive_breaches} consecutive days exceeding variance thresholds.")

        if abs(z_score) > self.max_z_score_threshold:
            failures.append(f"Z_SCORE_EXCEEDED: Tracking error z-score {z_score:.2f} > {self.max_z_score_threshold}")

        if days_count >= 20 and len(failures) == 0:
            status = "GATE_1_PASS"
        elif len(failures) > 0:
            status = "GATE_1_FAIL"
        else:
            status = "IN_PROGRESS"

        return ShadowCampaignSummary(
            days_evaluated=days_count,
            tracking_error_z_score=round(z_score, 4),
            avg_slippage_bps=round(avg_slippage, 2),
            latency_p99_ms=round(p99_lat, 2),
            shadow_pnl_pct=round(cum_pnl, 4),
            campaign_status=status,
            consecutive_breaches=self.consecutive_breaches,
            failure_reasons=failures,
        )
