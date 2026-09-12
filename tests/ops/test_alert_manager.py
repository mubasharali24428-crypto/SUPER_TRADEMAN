"""Tests for Alert Manager: evaluation, dedup, escalation, and REAL webhook dispatch.

Covers audit findings F-0006/G-001 (dispatch was logger.info-only), F-0156/G-003
(cooldown state resets on restart) and F-0376/G-111 (EMERGENCY suppressed by
cooldown). Dispatch is asserted against a real localhost HTTP server spun up
in-test: JSON body contents, retry counts, and cooldown dedup are verified from
the requests the server actually received.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from trading.ops.alert_manager import (AlertManager, AlertManagerConfig,
                                       AlertSeverity)
from trading.ops.deployment_metrics import DeploymentMetricsStore

WEBHOOK_PATH = "/hooks/super_trademan"


class _CaptureServer(HTTPServer):
    """HTTPServer carrying per-test capture state."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.requests: List[Dict] = []
        self.fail_next: int = 0


class _CaptureHandler(BaseHTTPRequestHandler):
    """Records POST bodies and can be told to fail the next N requests."""

    server: _CaptureServer

    def do_POST(self):  # noqa: N802 — http.server API
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            parsed = {"_raw": raw.decode("utf-8", errors="replace")}
        self.server.requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": parsed}
        )

        if self.server.fail_next > 0:
            self.server.fail_next -= 1
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"synthetic server error"}')
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - http.server API
        pass  # silence per-request logging


@pytest.fixture()
def webhook_server():
    server = _CaptureServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _webhook_url(server: _CaptureServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{WEBHOOK_PATH}"


@pytest.fixture(autouse=True)
def _isolated_alert_env(monkeypatch, tmp_path):
    """No ambient webhook env leakage; default state file kept out of the repo cwd."""
    for var in ("ALERT_WEBHOOK_URL", "SLACK_WEBHOOK_URL", "PAGERDUTY_ROUTING_KEY"):
        monkeypatch.delenv(var, raising=False)
    # Managers constructed without an explicit state_path must not share (or
    # litter) a state file across tests — point the env default into tmp_path.
    monkeypatch.setenv("OPS_ALERT_STATE_PATH", str(tmp_path / "ops_alert_state.json"))


def _manager(
    server: Optional[_CaptureServer] = None,
    state_path: Optional[Path] = None,
    config: Optional[AlertManagerConfig] = None,
) -> AlertManager:
    cfg = config or AlertManagerConfig(retry_backoff_sec=0.01)
    return AlertManager(
        store=DeploymentMetricsStore(),
        cooldown_sec=300.0,
        webhook_url=_webhook_url(server) if server else "",
        state_path=state_path,
        config=cfg,
    )


# ---------------------------------------------------------------------------
# Evaluation / escalation semantics (legacy behaviour preserved)
# ---------------------------------------------------------------------------


def test_alert_manager_evaluation_and_deduplication():
    mgr = _manager()  # no webhooks -> pure evaluation path
    rec1 = mgr.evaluate_metric("latency_p95_ms", 650.0)
    assert rec1 is not None
    assert rec1.alert_name == "High Latency"
    assert rec1.severity == AlertSeverity.WARNING

    rec2 = mgr.evaluate_metric("latency_p95_ms", 700.0)
    assert rec2 is None  # suppressed inside cooldown window


def test_alert_manager_escalation():
    mgr = _manager(config=AlertManagerConfig(retry_backoff_sec=0.01))
    mgr.cooldown_sec = 0.0
    mgr.evaluate_metric("latency_p95_ms", 600.0)
    mgr.evaluate_metric("latency_p95_ms", 600.0)
    rec3 = mgr.evaluate_metric("latency_p95_ms", 600.0)
    assert rec3 is not None
    assert rec3.severity == AlertSeverity.CRITICAL


def test_alert_manager_emergency_reconciliation():
    mgr = _manager()
    mgr.cooldown_sec = 0.0
    rec = mgr.evaluate_metric("reconciliation_mismatch", 1.0)
    assert rec is not None
    assert rec.severity == AlertSeverity.EMERGENCY


# ---------------------------------------------------------------------------
# Real dispatch against a live localhost webhook server (F-0006/G-001)
# ---------------------------------------------------------------------------


def test_emergency_dispatch_posts_json_body_to_webhook(webhook_server: _CaptureServer):
    mgr = _manager(webhook_server)
    rec = mgr.evaluate_metric("reconciliation_mismatch", 1.0)

    assert rec is not None and rec.severity == AlertSeverity.EMERGENCY
    assert len(webhook_server.requests) == 1
    sent = webhook_server.requests[0]
    assert sent["path"] == WEBHOOK_PATH
    assert sent["headers"].get("Content-Type") == "application/json"
    body = sent["body"]
    assert body["alert_id"] == rec.alert_id
    assert body["alert_name"] == "Reconciliation Mismatch"
    assert body["severity"] == "EMERGENCY"
    assert "mismatch" in body["message"].lower()
    assert "T" in body["timestamp_utc"]  # ISO timestamp present


def test_cooldown_dedup_suppresses_second_webhook_post(webhook_server: _CaptureServer):
    mgr = _manager(webhook_server)

    first = mgr.evaluate_metric("latency_p95_ms", 650.0)
    second = mgr.evaluate_metric("latency_p95_ms", 700.0)

    assert first is not None
    assert second is None
    assert len(webhook_server.requests) == 1  # exactly ONE outbound POST


def test_emergency_retries_twice_with_backoff_then_succeeds(
    webhook_server: _CaptureServer,
):
    webhook_server.fail_next = 2  # first two attempts return HTTP 500
    mgr = _manager(
        webhook_server,
        config=AlertManagerConfig(emergency_max_retries=2, retry_backoff_sec=0.01),
    )

    rec = mgr.evaluate_metric("reconciliation_mismatch", 2.0)
    assert rec is not None
    assert len(webhook_server.requests) == 3  # initial attempt + exactly two retries


def test_non_emergency_makes_single_attempt_without_retry(
    webhook_server: _CaptureServer,
):
    webhook_server.fail_next = 5  # would exhaust any retry budget if retried
    mgr = _manager(webhook_server)

    rec = mgr.evaluate_metric("latency_p95_ms", 900.0)
    assert rec is not None  # alert record produced even though delivery failed
    assert len(webhook_server.requests) == 1  # WARNING gets no retries


def test_emergency_bypasses_cooldown_window(webhook_server: _CaptureServer):
    """F-0376/G-111: an EMERGENCY rule must page even inside its own cooldown window."""
    mgr = _manager(webhook_server)

    r1 = mgr.evaluate_metric("reconciliation_mismatch", 1.0)
    r2 = mgr.evaluate_metric("reconciliation_mismatch", 1.0)  # immediate recurrence

    assert r1 is not None and r2 is not None
    assert len(webhook_server.requests) == 2  # both delivered, none suppressed


# ---------------------------------------------------------------------------
# Persisted cooldown state (F-0156/G-003)
# ---------------------------------------------------------------------------


def test_cooldown_state_persisted_to_ops_alert_state_json(tmp_path: Path):
    state_file = tmp_path / "ops_alert_state.json"
    mgr = _manager(state_path=state_file)
    mgr.evaluate_metric("latency_p95_ms", 650.0)

    assert state_file.exists()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert "High Latency" in data["last_alert_time"]
    assert data["alert_counts"]["High Latency"] >= 1


def test_cooldown_state_survives_manager_restart(
    webhook_server: _CaptureServer, tmp_path: Path
):
    state_file = tmp_path / "ops_alert_state.json"

    first = _manager(webhook_server, state_path=state_file)
    first.evaluate_metric("latency_p95_ms", 650.0)

    # Fresh instance simulating process restart, same persisted state.
    rebooted = _manager(webhook_server, state_path=state_file)
    suppressed = rebooted.evaluate_metric("latency_p95_ms", 700.0)

    assert suppressed is None  # cooldown honored across restart
    assert len(webhook_server.requests) == 1  # no duplicate post-restart dispatch


def test_escalation_counters_survive_restart(tmp_path: Path):
    state_file = tmp_path / "ops_alert_state.json"
    mgr_a = _manager(state_path=state_file)
    mgr_a.cooldown_sec = 0.0
    mgr_a.evaluate_metric("latency_p95_ms", 600.0)
    mgr_a.evaluate_metric("latency_p95_ms", 600.0)

    mgr_b = _manager(state_path=state_file)  # restart
    mgr_b.cooldown_sec = 0.0
    rec = mgr_b.evaluate_metric("latency_p95_ms", 600.0)
    assert rec is not None
    assert (
        rec.severity == AlertSeverity.CRITICAL
    )  # count carried over: 2 -> 3 escalates
