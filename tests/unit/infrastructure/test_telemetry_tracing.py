"""Tests for on-demand tracing: the stages as OpenTelemetry spans."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
from collections.abc import Sequence

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

import scavengarr.infrastructure.telemetry.tracing as tracing_module
from scavengarr.infrastructure.telemetry import Telemetry, create_telemetry
from scavengarr.infrastructure.telemetry.tracing import Tracing


class _HangingExporter(SpanExporter):
    """An endpoint that takes every batch and never answers (up to 3 s)."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.release.wait(3.0)
        return SpanExportResult.SUCCESS


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def telemetry(exporter: InMemorySpanExporter) -> Telemetry:
    return Telemetry(tracing=Tracing(SimpleSpanProcessor(exporter)))


def _spans(exporter: InMemorySpanExporter) -> dict[str, ReadableSpan]:
    return {span.name: span for span in exporter.get_finished_spans()}


class TestTracingOff:
    def test_no_tracing_without_an_endpoint(self) -> None:
        assert create_telemetry(None).tracing is None

    def test_tracing_is_not_loaded(self) -> None:
        """FastAPI imports the OpenTelemetry API and redis-py the SDK's metric
        types anyway; the trace SDK, the exporter, protobuf and the export
        thread stay out."""
        code = (
            "import sys, threading\n"
            "import scavengarr.interfaces.app\n"
            "from scavengarr.infrastructure.telemetry import create_telemetry\n"
            "create_telemetry(None)\n"
            "heavy = ('opentelemetry.sdk.trace', 'opentelemetry.exporter',"
            " 'google.protobuf')\n"
            "print(sorted(m for m in sys.modules if m.startswith(heavy)))\n"
            "print(threading.active_count())\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout

        assert out.split() == ["[]", "1"]

    def test_an_endpoint_turns_it_on(self) -> None:
        telemetry = create_telemetry("http://127.0.0.1:4318")
        try:
            assert telemetry.tracing is not None
        finally:
            telemetry.close()


class TestSpans:
    def test_a_stage_is_a_span_named_after_its_identity(
        self, telemetry: Telemetry, exporter: InMemorySpanExporter
    ) -> None:
        with telemetry.stage("plugin_search", plugin="kinoger") as stage:
            stage.annotate(results=3)
            stage.outcome = "hits"

        span = _spans(exporter)["plugin_search kinoger"]
        assert span.attributes == {"plugin": "kinoger", "results": 3, "outcome": "hits"}

    def test_metrics_are_recorded_too(self, telemetry: Telemetry) -> None:
        with telemetry.stage("plugin_search", plugin="kinoger") as stage:
            stage.outcome = "hits"

        value = telemetry.registry.get_sample_value(
            "scavengarr_plugin_search_total", {"plugin": "kinoger", "outcome": "hits"}
        )
        assert value == 1

    async def test_stages_and_tasks_of_a_request_form_one_trace(
        self, telemetry: Telemetry, exporter: InMemorySpanExporter
    ) -> None:
        async def _search() -> None:
            with telemetry.stage("stremio_phase", phase="search"):
                with telemetry.stage("plugin_search", plugin="kinoger") as stage:
                    stage.outcome = "empty"

        with telemetry.stage("stremio_request", source="none") as request:
            request.label(source="search")
            # The shared search runs as its own task
            await asyncio.create_task(_search())
            request.outcome = "streams"

        spans = _spans(exporter)
        root = spans["stremio_request"]
        search = spans["stremio_phase search"]
        plugin = spans["plugin_search kinoger"]
        assert root.parent is None
        assert search.parent is not None and root.context is not None
        assert search.parent.span_id == root.context.span_id
        assert plugin.parent is not None and search.context is not None
        assert plugin.parent.span_id == search.context.span_id
        trace_ids = {s.context.trace_id for s in spans.values() if s.context}
        assert len(trace_ids) == 1
        assert root.attributes is not None
        assert root.attributes["source"] == "search"

    def test_the_root_span_carries_the_request_id(
        self, telemetry: Telemetry, exporter: InMemorySpanExporter
    ) -> None:
        tokens = structlog.contextvars.bind_contextvars(request_id="a1b2c3d4e5f6")
        try:
            with telemetry.stage("stremio_request", source="none"):
                with telemetry.stage("stremio_phase", phase="metadata"):
                    pass
        finally:
            structlog.contextvars.reset_contextvars(**tokens)

        spans = _spans(exporter)
        assert spans["stremio_request"].attributes is not None
        assert spans["stremio_request"].attributes["request_id"] == "a1b2c3d4e5f6"
        child = spans["stremio_phase metadata"].attributes
        assert child is not None and "request_id" not in child

    def test_an_error_sets_the_status_without_the_message(
        self, telemetry: Telemetry, exporter: InMemorySpanExporter
    ) -> None:
        secret = "https://cdn.example/v.m3u8?token=abc"
        with (
            pytest.raises(ValueError),
            telemetry.stage("hoster_resolve", resolver="voe"),
        ):
            raise ValueError(f"failed for {secret}")

        span = _spans(exporter)["hoster_resolve voe"]
        assert span.status.status_code is StatusCode.ERROR
        assert span.status.description == "ValueError"
        assert not span.events
        assert secret not in str(span.to_json())

    async def test_a_cut_stage_ends_its_span(
        self, telemetry: Telemetry, exporter: InMemorySpanExporter
    ) -> None:
        async def _slow() -> None:
            with telemetry.stage("plugin_search", plugin="slow"):
                await asyncio.sleep(10)

        task = asyncio.create_task(_slow())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        span = _spans(exporter)["plugin_search slow"]
        assert span.attributes is not None
        assert span.attributes["outcome"] == "cut"

    def test_a_stage_in_a_route_starts_its_own_trace(
        self, telemetry: Telemetry, exporter: InMemorySpanExporter
    ) -> None:
        """FastAPI's own telemetry (off without OTEL_* settings) leaves only a
        span that does not record in the context; the request's stage is the
        root of its trace."""
        app = FastAPI()

        @app.get("/stage")
        async def stage() -> dict[str, str]:
            with telemetry.stage("stremio_request", source="none") as request:
                request.outcome = "empty"
            return {}

        TestClient(app).get("/stage")

        assert _spans(exporter)["stremio_request"].parent is None

    def test_the_hls_proxy_is_not_traced(
        self, telemetry: Telemetry, exporter: InMemorySpanExporter
    ) -> None:
        with telemetry.stage("hls_proxy", kind="segment") as stage:
            stage.outcome = "200"

        assert exporter.get_finished_spans() == ()

    def test_close_waits_for_a_hanging_endpoint_only_briefly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SDK's flush ignores its timeout: each queued batch could take
        the exporter's 5 s against an endpoint that does not answer."""
        monkeypatch.setattr(tracing_module, "_CLOSE_S", 0.2, raising=False)
        exporter = _HangingExporter()
        telemetry = Telemetry(tracing=Tracing(BatchSpanProcessor(exporter)))
        with telemetry.stage("stremio_phase", phase="search"):
            pass
        started = time.monotonic()

        try:
            telemetry.close()
            elapsed = time.monotonic() - started
        finally:
            exporter.release.set()

        assert elapsed < 1.0

    def test_close_flushes_the_batch(self, exporter: InMemorySpanExporter) -> None:
        telemetry = Telemetry(tracing=Tracing(BatchSpanProcessor(exporter)))
        with telemetry.stage("stremio_phase", phase="search"):
            pass

        telemetry.close()

        assert [s.name for s in exporter.get_finished_spans()] == [
            "stremio_phase search"
        ]
