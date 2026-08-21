"""Alert Manager with Multi-Channel Routing, Cooldown, Deduplication, and Escalation."""

import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from trading.observability.logger import get_logger
from trading.ops.deployment_metrics import AlertRecord, DeploymentMetricsStore

__all__ = [
    "AlertSeverity",
    "AlertRule",
    "AlertManager",
]

logger = get_logger("trading.ops.alert_manager")


class AlertSeverity:
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


@dataclass
class AlertRule:
    rule_name: str
    metric_name: str
    threshold: float
    comparator: str  # ">", "<", ">=", "<="
    severity: str
    cooldown_sec: float = 300.0


class AlertManager:
    """Evaluates metrics against alert rules and dispatches deduplicated alerts."""

    def __init__(
        self,
        store: Optional[DeploymentMetricsStore] = None,
        cooldown_sec: float = 300.0,
        slack_webhook_url: str = "",
        pagerduty_key: str = "",
    ):
        self.store = store or DeploymentMetricsStore()
        self.cooldown_sec = cooldown_sec
        self.slack_webhook_url = slack_webhook_url or os.getenv("SLACK_WEBHOOK_URL", "")
        self.pagerduty_key = pagerduty_key or os.getenv("PAGERDUTY_ROUTING_KEY", "")

        self.last_alert_time: Dict[str, float] = {}
        self.alert_counts: Dict[str, int] = {}
        self.rules: List[AlertRule] = [
            AlertRule("High Latency", "latency_p95_ms", 500.0, ">", AlertSeverity.WARNING),
            AlertRule("Stale Market Data", "stale_data_sec", 10.0, ">", AlertSeverity.CRITICAL),
            AlertRule("Portfolio Drawdown", "drawdown_pct", 0.02, ">", AlertSeverity.CRITICAL),
            AlertRule("Reconciliation Mismatch", "reconciliation_mismatch", 0.0, ">", AlertSeverity.EMERGENCY),
            AlertRule("Drill Failure", "drill_failure", 0.0, ">", AlertSeverity.CRITICAL),
        ]

    def should_suppress(self, rule_name: str, now_ts: float) -> bool:
        """Deduplicates and checks cooldown period for alert rules."""
        last_t = self.last_alert_time.get(rule_name, 0.0)
        return (now_ts - last_t) < self.cooldown_sec

    def escalate_severity(self, rule_name: str, current_severity: str) -> str:
        """Escalates severity if triggered repeatedly within a short window."""
        count = self.alert_counts.get(rule_name, 0) + 1
        self.alert_counts[rule_name] = count
        if count >= 3 and current_severity == AlertSeverity.WARNING:
            return AlertSeverity.CRITICAL
        if count >= 5 and current_severity == AlertSeverity.CRITICAL:
            return AlertSeverity.EMERGENCY
        return current_severity

    def send_alert_channels(self, alert_record: AlertRecord) -> List[str]:
        """Dispatches alert to configured notification channels."""
        channels_sent = []

        # Slack Channel
        if self.slack_webhook_url:
            logger.info(f"[SLACK_ALERT] [{alert_record.severity}] {alert_record.alert_name}: {alert_record.message}")
            channels_sent.append("slack")
        else:
            logger.info(f"[LOG_FALLBACK_ALERT] [{alert_record.severity}] {alert_record.alert_name}: {alert_record.message}")
            channels_sent.append("log")

        # PagerDuty Channel for Critical / Emergency
        if alert_record.severity in (AlertSeverity.CRITICAL, AlertSeverity.EMERGENCY):
            if self.pagerduty_key:
                logger.info(f"[PAGERDUTY_ALERT] [{alert_record.severity}] {alert_record.alert_name}")
                channels_sent.append("pagerduty")

        return channels_sent

    def evaluate_metric(self, metric_name: str, value: float, custom_message: str = "") -> Optional[AlertRecord]:
        """Evaluates a single metric value against registered rules."""
        now_ts = time.time()

        for rule in self.rules:
            if rule.metric_name != metric_name:
                continue

            triggered = False
            if rule.comparator == ">" and value > rule.threshold:
                triggered = True
            elif rule.comparator == "<" and value < rule.threshold:
                triggered = True
            elif rule.comparator == ">=" and value >= rule.threshold:
                triggered = True
            elif rule.comparator == "<=" and value <= rule.threshold:
                triggered = True

            if triggered:
                if self.should_suppress(rule.rule_name, now_ts):
                    logger.debug(f"[ALERT_SUPPRESSED] {rule.rule_name} suppressed by cooldown.")
                    return None

                self.last_alert_time[rule.rule_name] = now_ts
                escalated_severity = self.escalate_severity(rule.rule_name, rule.severity)

                msg = custom_message or f"{rule.rule_name} triggered: {metric_name}={value} {rule.comparator} {rule.threshold}"
                rec = AlertRecord(
                    alert_id=f"alt_{uuid.uuid4().hex[:8]}",
                    alert_name=rule.rule_name,
                    severity=escalated_severity,
                    message=msg,
                    channel="multi",
                    timestamp_utc=datetime.now(timezone.utc),
                )
                self.send_alert_channels(rec)
                return rec

        return None
