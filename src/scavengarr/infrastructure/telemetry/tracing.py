"""On-demand tracing: the core's stages as OpenTelemetry spans.

Imported only when ``telemetry.tracing_endpoint`` is set
(``create_telemetry``): without it no OpenTelemetry module is loaded. The
provider is not registered globally; the spans nest through the active span
in the context, which tasks copy when they start, so a shared search and its
resolutions are children of the request that started them.

Spans carry the stage's labels, annotations and outcome, never URLs (stream
URLs carry tokens): an error sets the status to the exception's type, not
its message.
"""

from __future__ import annotations

from collections.abc import Mapping

import structlog
from opentelemetry import context, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from scavengarr.domain.ports.telemetry import AttributeValue
from scavengarr.infrastructure.version import APP_VERSION

# Spans still queued at shutdown get this long to go out
_FLUSH_MILLIS = 3000
# One export (a batch, every 5 s) may take this long
_EXPORT_TIMEOUT_S = 5.0


class TracedStage:
    """The span of one running stage, current in the context until it ends."""

    __slots__ = ("_span", "_token")

    def __init__(self, span: Span) -> None:
        self._span = span
        self._token = context.attach(trace.set_span_in_context(span))

    def set_attributes(self, attributes: Mapping[str, AttributeValue]) -> None:
        self._span.set_attributes(attributes)

    def end(self, outcome: str, error: BaseException | None) -> None:
        self._span.set_attribute("outcome", outcome)
        if outcome == "error":
            name = type(error).__name__ if error is not None else None
            self._span.set_status(Status(StatusCode.ERROR, name))
        context.detach(self._token)
        self._span.end()


class Tracing:
    """Starts the stages' spans and hands them to *processor*."""

    def __init__(self, processor: SpanProcessor) -> None:
        resource = Resource.create(
            {"service.name": "scavengarr", "service.version": APP_VERSION}
        )
        self._provider = TracerProvider(resource=resource)
        self._provider.add_span_processor(processor)
        self._tracer = self._provider.get_tracer("scavengarr")

    def start(self, name: str, attributes: Mapping[str, AttributeValue]) -> TracedStage:
        """Start a span; a root span (a request) gets the log's ``request_id``."""
        attrs = dict(attributes)
        if not trace.get_current_span().get_span_context().is_valid:
            request_id = structlog.contextvars.get_contextvars().get("request_id")
            if request_id:
                attrs["request_id"] = request_id
        return TracedStage(self._tracer.start_span(name, attributes=attrs))

    def close(self) -> None:
        """Send the spans still queued and stop (blocks up to a few seconds)."""
        self._provider.force_flush(_FLUSH_MILLIS)
        self._provider.shutdown()


def otlp_tracing(endpoint: str) -> Tracing:
    """Tracing that sends batches over OTLP/HTTP to *endpoint* (base URL)."""
    exporter = OTLPSpanExporter(
        endpoint=f"{endpoint.rstrip('/')}/v1/traces", timeout=_EXPORT_TIMEOUT_S
    )
    return Tracing(BatchSpanProcessor(exporter))
