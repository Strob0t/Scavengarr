"""Cost of the telemetry: recording a stage and rendering a scrape.

The metrics must cost far less than the work they measure (a first stream
request: 9.2 s of CPU on the Raspberry Pi 4). Limits from
``openspec/changes/add-observability``: under 50 µs per stage and under 20 ms
per scrape of the expected series on the development machine; the Pi is
about 3-4 times slower.

Run manually:  ``poetry run pytest tests/benchmark/test_telemetry_overhead.py -s -v``
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable

import pytest
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from scavengarr.infrastructure.telemetry import Telemetry
from scavengarr.infrastructure.telemetry.tracing import Tracing

pytestmark = pytest.mark.benchmark

_STAGE_LIMIT_US = 50.0
_SCRAPE_LIMIT_MS = 20.0


def _busy(telemetry: Telemetry) -> Telemetry:
    """Telemetry with the series of a busy day: 20 stream plugins, 12
    resolvers, every source, phase and proxy kind."""
    for i in range(20):
        for outcome in ("hits", "empty", "cut", "error"):
            with telemetry.stage("plugin_search", plugin=f"plugin{i}") as stage:
                stage.outcome = outcome
        telemetry.count("plugin_search", "breaker_open", plugin=f"plugin{i}")
        telemetry.record("plugin_results", 5, plugin=f"plugin{i}")
    for i in range(12):
        for outcome in ("stream", "dead", "timeout", "cut"):
            with telemetry.stage("hoster_resolve", resolver=f"resolver{i}") as stage:
                stage.outcome = outcome
        telemetry.count("hoster_resolve", "cached", resolver=f"resolver{i}")
    for source in ("cache", "stale", "search", "joined"):
        with telemetry.stage("stremio_request", source=source) as stage:
            stage.outcome = "streams"
    for phase, outcome in (
        ("metadata", "found"),
        ("search", "ok"),
        ("resolve", "target"),
        ("resolve", "done"),
        ("background_resolve", "done"),
    ):
        with telemetry.stage("stremio_phase", phase=phase) as stage:
            stage.outcome = outcome
    for kind in ("master", "playlist", "segment"):
        with telemetry.stage("hls_proxy", kind=kind) as stage:
            stage.outcome = "200"
        telemetry.record("hls_proxy_bytes", 1_500_000, kind=kind)
    telemetry.record("stremio_streams", 5)
    telemetry.record_loop_lag(3.0)
    return telemetry


def _median_us(run: Callable[[], None], *, rounds: int = 7, n: int = 5000) -> float:
    """Median over *rounds* of the mean time of *n* calls, in µs."""
    means = []
    for _ in range(rounds):
        started = time.perf_counter()
        for _ in range(n):
            run()
        means.append((time.perf_counter() - started) / n * 1e6)
    return statistics.median(means)


def _stage(telemetry: Telemetry) -> Callable[[], None]:
    def run() -> None:
        with telemetry.stage("plugin_search", plugin="plugin3") as stage:
            stage.outcome = "hits"

    return run


def test_recording_and_scraping_stay_cheap() -> None:
    telemetry = _busy(Telemetry())
    text = telemetry.render().decode()
    series = sum(1 for line in text.splitlines() if not line.startswith("#"))

    stage_us = _median_us(_stage(telemetry))
    count_us = _median_us(
        lambda: telemetry.count("plugin_search", "skipped", plugin="plugin3")
    )
    scrape_ms = _median_us(telemetry.render, n=20) / 1000

    exporter = InMemorySpanExporter()
    traced = _busy(Telemetry(tracing=Tracing(BatchSpanProcessor(exporter))))
    traced_us = _median_us(_stage(traced), n=2000)
    traced.close()

    print(
        f"\nseries {series}, bytes {len(text)}\n"
        f"stage {stage_us:.1f} us, count {count_us:.1f} us, "
        f"stage with tracing {traced_us:.1f} us\n"
        f"scrape {scrape_ms:.2f} ms"
    )
    assert stage_us < _STAGE_LIMIT_US
    assert scrape_ms < _SCRAPE_LIMIT_MS
