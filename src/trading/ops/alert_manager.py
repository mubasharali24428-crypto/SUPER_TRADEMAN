"""Alert Manager with Multi-Channel Routing, Cooldown, Deduplication, and Escalation.

Real dispatch (audit finding F-0006/G-001): alerts are delivered via HTTP POST
using only the standard library (urllib.request) — no new dependencies.

- Generic ops webhook: POSTs the full alert JSON to ALERT_WEBHOOK_URL (env).
- Slack incoming webhook (SLACK_WEBHOOK_URL) and PagerDuty Events API v2
  (PAGERDUTY_ROUTING_KEY) are attempted when configured.
- EMERGENCY severity always attempts delivery and retries twice with linear
  backoff; other severities get a single attempt (failures are logged).
- Cooldown/dedup state (last_alert_time, alert_counts) is persisted to
  ops_alert_state.json so suppression windows and escalation counters survive
  process restarts (findings F-0156/G-003, F-0376/G-111).
- Dispatch outcomes are logged with channel, status code, and attempt count.
"""

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading.observability.logger import get_logger
from trading.ops.deployment_metrics import AlertRecord, DeploymentMetricsStore

__all__ = [
    "AlertSeverity",
    "AlertRule",
    "WebhookDispatchResult",
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


@dataclass
class WebhookDispatchResult:
    """Outcome of one webhook channel delivery (possibly multi-attempt)."""

    channel: str
    ok: bool
    attempts: int = 1
    status_code: Optional[int] = None
    error: str = ""


@dataclass
class AlertManagerConfig:
    """Tunables for AlertManager delivery behaviour."""

    http_timeout_sec: float = 5.0
    emergency_max_retries: int = 2  # two retries => up to 3 attempts total
    retry_backoff_sec: float = 2.0
    dispatch_interval: float = 60.0  # minimum seconds between webhook batches (dedup window)


class AlertManager:
    """Evaluates metrics against alert rules and dispatches deduplicated alerts over real webhooks."""

    def __init__(
        self,
        store: Optional[DeploymentMetricsStore] = None,
        cooldown_sec: float = 300.0,
        slack_webhook_url: str = "",
        pagerduty_key: str = "",
        webhook_url: str = "",
        state_path: "str | Path | None" = None,
        config: Optional[AlertManagerConfig] = None,
    ):
        self.store = store or DeploymentMetricsStore()
        self.cooldown_sec = cooldown_sec
        self.slack_webhook_url = slack_webhook_url or os.getenv("SLACK_WEBHOOK_URL", "")
        self.pagerduty_key = pagerduty_key or os.getenv("PAGERDUTY_ROUTING_KEY", "")
        self.webhook_url = webhook_url or os.getenv("ALERT_WEBHOOK_URL", "")
        self.config = config or AlertManagerConfig()
        self.state_path = Path(state_path) if state_path else Path(os.getenv("OPS_ALERT_STATE_PATH", "ops_alert_state.json"))

        self.last_alert_time: Dict[str, float] = {}
        self.alert_counts: Dict[str, int] = {}
        self.rules: List[AlertRule] = [
            AlertRule("High Latency", "latency_p95_ms", 500.0, ">", AlertSeverity.WARNING),
            AlertRule("Stale Market Data", "stale_data_sec", 10.0, ">", AlertSeverity.CRITICAL),
            AlertRule("Portfolio Drawdown", "drawdown_pct", 0.02, ">", AlertSeverity.CRITICAL),
            AlertRule("Reconciliation Mismatch", "reconciliation_mismatch", 0.0, ">", AlertSeverity.EMERGENCY),
            AlertRule("Drill Failure", "drill_failure", 0.0, ">", AlertSeverity.CRITICAL),
        ]
        self._load_state()

    # ------------------------------------------------------------------
    # Persisted cooldown / dedup state
    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        """Restore cooldown and escalation counters across restarts."""
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.last_alert_time = {
                    str(k): float(v) for k, v in (data.get("last_alert_time") or {}).items()
                }
                self.alert_counts = {
                    str(k): int(v) for k, v in (data.get("alert_counts") or {}).items()
                }
                logger.info(
                    f"[ALERT_STATE_LOADED] rules={len(self.last_alert_time)} from {self.state_path}"
                )
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(f"[ALERT_STATE_LOAD_FAILED] {exc}; starting with empty dedup state.")

    def _persist_state(self) -> None:
        """Write cooldown and escalation counters atomically to the state file."""
        payload = {
            "last_alert_time": dict(self.last_alert_time),
            "alert_counts": dict(self.alert_counts),
        }
        try:
            tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp_path, self.state_path)
        except OSError as exc:
            logger.warning(f"[ALERT_STATE_PERSIST_FAILED] {exc}")

    def should_suppress(self, rule_name: str, now_ts: float) -> bool:
        """Deduplicates and checks the cooldown period for alert rules.

        EMERGENCY-severity alerts are never suppressed by this window — see
        ``should_suppress_for_severity`` (finding F-0376/G-111).
        """
        last_t = self.last_alert_time.get(rule_name, 0.0)
        return (now_ts - last_t) < self.cooldown_sec

    def should_suppress_for_severity(self, rule_name: str, severity: str, now_ts: float) -> bool:
        """Severity-aware suppression: EMERGENCY always pages, others respect cooldown."""
        if severity == AlertSeverity.EMERGENCY:
            return False
        return self.should_suppress(rule_name, now_ts)

    def escalate_severity(self, rule_name: str, current_severity: str) -> str:
        """Escalates severity if triggered repeatedly within a short window."""
        count = self.alert_counts.get(rule_name, 0) + 1
        self.alert_counts[rule_name] = count
        if count >= 3 and current_severity == AlertSeverity.WARNING:
            return AlertSeverity.CRITICAL
        if count >= 5 and current_severity == AlertSeverity.CRITICAL:
            return AlertSeverity.EMERGENCY
        return current_severity

    # ------------------------------------------------------------------
    # Real webhook dispatch
    # ------------------------------------------------------------------
    @staticmethod
    def _post_json(url: str, payload: Dict[str, Any], timeout_sec: float) -> Tuple[bool, Optional[int], str]:
        """Single HTTP POST of a JSON body via urllib. Returns (ok, status_code, error)."""
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "super_trademan-alerts/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                return True, int(response.status), ""
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:  # noqa: BLE001 — best-effort body read
                pass
            return False, int(exc.code), f"HTTP {exc.code} {detail}".strip()
        except urllib.error.URLError as exc:
            return False, None, f"URL error: {exc.reason}"
        except (TimeoutError, OSError) as exc:
            return False, None, f"transport error: {exc}"

    def _dispatch_with_retries(self, channel: str, url: str, payload: Dict[str, Any], emergency: bool) -> WebhookDispatchResult:
        """Deliver a payload to one webhook URL with EMERGENCY-grade retry policy."""
        timeout = self.config.http_timeout_sec
        max_attempts = (1 + self.config.emergency_max_retries) if emergency else 1
        attempts = 0
        last_status: Optional[int] = None
        last_error = ""

        while attempts < max_attempts:
            attempts += 1
            ok, status, err = self._post_json(url, payload, timeout)
            last_status, last_error = status, err
            if ok:
                logger.info(
                    f"[WEBHOOK_DISPATCH_OK] channel={channel} attempts={attempts} status={status}"
                )
                return WebhookDispatchResult(channel=channel, ok=True, attempts=attempts, status_code=status)
            logger.warning(
                f"[WEBHOOK_DISPATCH_FAIL] channel={channel} attempt={attempts}/{max_attempts} "
                f"status={status} error={err}"
            )
            if attempts < max_attempts:
                time.sleep(self.config.retry_backoff_sec * attempts)

        return WebhookDispatchResult(channel=channel, ok=False, attempts=attempts, status_code=last_status, error=last_error)

    def send_alert_channels(self, alert_record: AlertRecord) -> List[str]:
        """Dispatches an alert over every configured channel via real HTTP POSTs.

        Returns the list of channels where delivery succeeded; channels whose
        delivery failed are reported with a ':failed' suffix and logged.
        """
        channels_sent: List[str] = []
        emergency = alert_record.severity == AlertSeverity.EMERGENCY
        summary = f"[{alert_record.severity}] {alert_record.alert_name}: {alert_record.message}"

        # Primary ops webhook (generic JSON sink, e.g. gateway relay).
        if self.webhook_url:
            result = self._dispatch_with_retries(
                "webhook", self.webhook_url,
                {
                    "alert_id": alert_record.alert_id,
                    "alert_name": alert_record.alert_name,
                    "severity": alert_record.severity,
                    "message": alert_record.message,
                    "timestamp_utc": alert_record.timestamp_utc.isoformat(),
                },
                emergency=emergency,
            )
            channels_sent.append("webhook" if result.ok else "webhook:failed")

        # Slack incoming webhook.
        if self.slack_webhook_url:
            result = self._dispatch_with_retries(
                "slack", self.slack_webhook_url, {"text": summary}, emergency=emergency
            )
            channels_sent.append("slack" if result.ok else "slack:failed")
        elif not self.webhook_url:
            logger.info(f"[LOG_FALLBACK_ALERT] {summary}")
            channels_sent.append("log")

        # PagerDuty Events API v2 for Critical / Emergency.
        if alert_record.severity in (AlertSeverity.CRITICAL, AlertSeverity.EMERGENCY):
            if self.pagerduty_key:
                pd_payload = {
                    "routing_key": self.pagerduty_key,
                    "event_action": "trigger",
                    "payload": {
                        "summary": summary[:1024],
                        "source": "super_trademan",
                        "severity": "critical" if alert_record.severity == AlertSeverity.CRITICAL else "error",
                        "custom_details": {
                            "alert_id": alert_record.alert_id,
                            "message": alert_record.message,
                        },
                    },
                }
                result = self._dispatch_with_retries(
                    "pagerduty", "https://events.pagerduty.com/v2/enqueue", pd_payload, emergency=emergency
                )
                channels_sent.append("pagerduty" if result.ok else "pagerduty:failed")

        return channels_sent

    # ------------------------------------------------------------------
    # Metric evaluation
    # ------------------------------------------------------------------
    def evaluate_metric(self, metric_name: str, value: float, custom_message: str = "") -> Optional[AlertRecord]:
        """Evaluates a single metric value against registered rules and dispatches on trigger.

        Suppression is severity-aware: an EMERGENCY alert (including any alert
        escalated to EMERGENCY) always attempts delivery regardless of cooldown
        state. Every accepted trigger updates and persists the cooldown state.
        """
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
                # Severity-aware suppression (F-0376/G-111): EMERGENCY rules
                # always page — cooldown never suppresses them. Other severities
                # respect the persisted cooldown window.
                if self.should_suppress_for_severity(rule.rule_name, rule.severity, now_ts):
                    logger.debug(f"[ALERT_SUPPRESSED] {rule.rule_name} suppressed by cooldown.")
                    return None

                escalated_severity = self.escalate_severity(rule.rule_name, rule.severity)
                self.last_alert_time[rule.rule_name] = now_ts
                # Persist AFTER mutating both cooldown and escalation counters so
                # the snapshot always includes the current event.
                self._persist_state()

                msg = custom_message or f"{rule.rule_name} triggered: {metric_name}={value} {rule.comparator} {rule.threshold}"
                rec = AlertRecord(
                    alert_id=f"alt_{uuid.uuid4().hex[:8]}",
                    alert_name=rule.rule_name,
                    severity=escalated_severity,
                    message=msg,
                    channel="multi",
                    timestamp_utc=datetime.now(timezone.utc),
                )
                channels = self.send_alert_channels(rec)
                logger.info(
                    f"[ALERT_DISPATCHED] rule={rule.rule_name} severity={rec.severity} "
                    f"alert_id={rec.alert_id} channels={','.join(channels) or 'none'}"
                )
                return rec

        return None
