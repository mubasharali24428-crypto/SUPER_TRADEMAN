"""Minimal OpenTelemetry tracing facade for SUPER_TRADEMAN (OT-1).

Truthfulness-first design, mirroring the rest of ``trading.observability``:

* ``opentelemetry-*`` packages are OPTIONAL. They live in the ``otel`` extras
  group (see ``pyproject.toml`` ``[project.optional-dependencies]``). Importing
  this module must NEVER fail — the SDK is imported lazily inside
  :func:`setup_tracing` and any import/runtime failure downgrades to no-op.
* When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set **and** the SDK + OTLP exporter
  are importable, :func:`setup_tracing` installs a real ``TracerProvider`` with
  a batched OTLP exporter against that endpoint.
* In every other case a tiny in-repo no-op tracer is used: spans are real
  context managers that carry no data and cost nothing, and
  :func:`current_trace_ids` reports ``(None, None)`` so structured JSON logs
  fall back to their correlation-id ``trace_id`` field (never a fake trace id).
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional, Tuple

__all__ = [
    "setup_tracing",
    "tracing_active",
    "get_request_tracer",
    "current_trace_ids",
]

_lock = threading.Lock()
_active = False
_real_tracer = None  # opentelemetry.trace.Tracer once tracing is active


# --------------------------------------------------------------------------
# No-op fallbacks (used whenever the SDK is unavailable / not configured)
# --------------------------------------------------------------------------
class _NoopSpan:
    """Context-manager span that records nothing."""

    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False  # never swallow exceptions

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def record_exception(self, exception: BaseException, **_: Any) -> None:
        pass


class _NoopTracer:
    """Drop-in stand-in for ``opentelemetry.trace.Tracer``."""

    def start_as_current_span(
        self, name: str, attributes: Optional[Dict[str, Any]] = None, **_: Any
    ) -> _NoopSpan:
        return _NoopSpan()


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def setup_tracing(service_name: str) -> bool:
    """Install real OTel tracing when configured; return whether it is active.

    Active requires BOTH: ``OTEL_EXPORTER_OTLP_ENDPOINT`` set in the
    environment and the ``opentelemetry`` SDK + OTLP HTTP exporter importable
    (install with ``pip install -e '.[otel]'``). Otherwise this is a safe
    no-op and the caller keeps using the no-op tracer.
    """
    global _active, _real_tracer
    with _lock:
        if _active:
            return True
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if not endpoint:
            return False
        try:  # lazy import — never at module load time
            from opentelemetry import trace as otel_trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import \
                OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except Exception:  # pragma: no cover - depends on optional extras
            return False

        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )
        otel_trace.set_tracer_provider(provider)
        _real_tracer = otel_trace.get_tracer("trading.observability")
        _active = True
        return True


def tracing_active() -> bool:
    """True only when a real OTel TracerProvider was installed."""
    return _active


def get_request_tracer():
    """Return the tracer for per-request spans (no-op when tracing is off)."""
    return _real_tracer if (_active and _real_tracer is not None) else _NoopTracer()


def current_trace_ids() -> Tuple[Optional[str], Optional[str]]:
    """Current valid ``(trace_id_hex32, span_id_hex16)`` or ``(None, None)``."""
    if not (_active and _real_tracer is not None):
        return None, None
    try:
        from opentelemetry import trace as otel_trace

        ctx = otel_trace.get_current_span().get_span_context()
        if not getattr(ctx, "is_valid", False):
            return None, None
        return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
    except Exception:  # pragma: no cover - defensive
        return None, None
