"""Shared benchmark infrastructure: fake plugins, registries, timing utilities.

All benchmarks use synthetic ``asyncio.sleep()`` latency to simulate I/O,
providing deterministic, fast measurements of scheduling behaviour without
real network variance.
"""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from scavengarr.domain.plugins.base import SearchResult

# ---------------------------------------------------------------------------
# Fake plugin that simulates I/O with asyncio.sleep
# ---------------------------------------------------------------------------


@dataclass
class LatencyPlugin:
    """Fake plugin with configurable latency and result count.

    Simulates a real plugin's ``search()`` method by sleeping for
    ``latency_seconds`` then returning ``result_count`` synthetic
    SearchResult objects.
    """

    name: str
    latency_seconds: float = 0.4
    result_count: int = 5
    mode: str = "httpx"  # "httpx" | "playwright"
    provides: str = "stream"
    languages: list[str] = field(default_factory=lambda: ["de"])

    async def search(
        self,
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        await asyncio.sleep(self.latency_seconds)
        return [
            SearchResult(
                title=f"{self.name}_result_{i}",
                download_link=f"https://example.com/{self.name}/{i}",
            )
            for i in range(self.result_count)
        ]


# ---------------------------------------------------------------------------
# Fake plugin registry (synchronous, MagicMock-compatible)
# ---------------------------------------------------------------------------


class FakePluginRegistry:
    """Minimal PluginRegistryPort implementation for benchmarks.

    Stores LatencyPlugin instances and satisfies the synchronous
    protocol used by StremioStreamUseCase._search_plugins.
    """

    def __init__(self, plugins: list[LatencyPlugin]) -> None:
        self._plugins = {p.name: p for p in plugins}

    def discover(self) -> None:
        pass

    def list_names(self) -> list[str]:
        return list(self._plugins.keys())

    def get(self, name: str) -> LatencyPlugin:
        return self._plugins[name]

    def get_by_provides(self, provides: str) -> list[str]:
        return [n for n, p in self._plugins.items() if p.provides == provides]

    def get_languages(self, name: str) -> list[str]:
        return self._plugins[name].languages

    def get_mode(self, name: str) -> str:
        return self._plugins[name].mode


# ---------------------------------------------------------------------------
# Benchmark result dataclass with derived metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkResult:
    """Captures timing data from a single benchmark scenario run."""

    scenario: str
    httpx_slots: int
    pw_slots: int
    plugin_count: int
    concurrent_requests: int
    total_wall_ns: int
    per_request_latencies_ns: list[int]
    completed: int
    failed: int

    @property
    def wall_ms(self) -> float:
        return self.total_wall_ns / 1_000_000

    @property
    def throughput(self) -> float:
        """Requests per second."""
        wall_s = self.total_wall_ns / 1_000_000_000
        return self.completed / wall_s if wall_s > 0 else 0.0

    @property
    def p50_ms(self) -> float:
        if not self.per_request_latencies_ns:
            return 0.0
        sorted_ns = sorted(self.per_request_latencies_ns)
        idx = len(sorted_ns) // 2
        return sorted_ns[idx] / 1_000_000

    @property
    def p95_ms(self) -> float:
        if not self.per_request_latencies_ns:
            return 0.0
        sorted_ns = sorted(self.per_request_latencies_ns)
        idx = int(len(sorted_ns) * 0.95)
        return sorted_ns[min(idx, len(sorted_ns) - 1)] / 1_000_000

    @property
    def p99_ms(self) -> float:
        if not self.per_request_latencies_ns:
            return 0.0
        sorted_ns = sorted(self.per_request_latencies_ns)
        idx = int(len(sorted_ns) * 0.99)
        return sorted_ns[min(idx, len(sorted_ns) - 1)] / 1_000_000

    @property
    def mean_ms(self) -> float:
        if not self.per_request_latencies_ns:
            return 0.0
        return statistics.mean(self.per_request_latencies_ns) / 1_000_000


# ---------------------------------------------------------------------------
# Fake TMDB client, search engine, and config
# ---------------------------------------------------------------------------


def make_fake_tmdb() -> AsyncMock:
    """Create a fake TmdbClientPort that returns a fixed title."""
    from scavengarr.domain.entities.stremio import TitleMatchInfo

    tmdb = AsyncMock()
    tmdb.get_title_and_year.return_value = TitleMatchInfo(
        title="Benchmark Movie",
        year=2024,
        content_type="movie",
    )
    return tmdb


def make_fake_search_engine() -> AsyncMock:
    """Create a fake SearchEnginePort that passes results through."""
    engine = AsyncMock()
    engine.validate_results.side_effect = lambda results: results
    return engine


@dataclass
class FakeStremioConfig:
    """Minimal config satisfying _StremioConfig protocol."""

    max_concurrent_plugins: int = 10
    max_concurrent_playwright: int = 3
    plugin_timeout_seconds: float = 30.0
    title_match_threshold: float = 0.5
    title_year_bonus: float = 0.1
    title_year_penalty: float = 0.15
    title_sequel_penalty: float = 0.2
    title_year_tolerance_movie: int = 1
    title_year_tolerance_series: int = 0
    max_results_per_plugin: int = 100
    probe_at_stream_time: bool = False
    max_probe_count: int = 50
    resolve_target_count: int = 5
    probe_concurrency: int = 10
    scoring_enabled: bool = False
    max_plugins_scored: int = 15
    exploration_probability: float = 0.15


# ---------------------------------------------------------------------------
# Plugin factory helpers
# ---------------------------------------------------------------------------


def make_mixed_plugins(
    count: int,
    *,
    fast_ratio: float = 0.7,
    fast_latency: float = 0.3,
    slow_latency: float = 1.5,
    results_per_plugin: int = 5,
) -> list[LatencyPlugin]:
    """Create a mixed list of fast (httpx) and slow (playwright) plugins.

    Args:
        count: Total number of plugins to create.
        fast_ratio: Fraction of plugins that are httpx (fast).
        fast_latency: Simulated I/O latency for httpx plugins.
        slow_latency: Simulated I/O latency for Playwright plugins.
        results_per_plugin: Results returned per plugin.
    """
    n_fast = int(count * fast_ratio)
    plugins: list[LatencyPlugin] = []
    for i in range(count):
        is_fast = i < n_fast
        plugins.append(
            LatencyPlugin(
                name=f"plugin_{i:03d}",
                latency_seconds=fast_latency if is_fast else slow_latency,
                result_count=results_per_plugin,
                mode="httpx" if is_fast else "playwright",
            )
        )
    return plugins


# ---------------------------------------------------------------------------
# Table formatter for benchmark output
# ---------------------------------------------------------------------------


def format_table(
    headers: list[str],
    rows: list[list[Any]],
    *,
    title: str = "",
) -> str:
    """Format benchmark results as a plain-text table."""
    col_widths = [len(h) for h in headers]
    str_rows = []
    for row in rows:
        str_row = [str(v) for v in row]
        str_rows.append(str_row)
        for i, v in enumerate(str_row):
            col_widths[i] = max(col_widths[i], len(v))

    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    lines: list[str] = []
    if title:
        lines.append(f"\n{'=' * 60}")
        lines.append(f"  {title}")
        lines.append(f"{'=' * 60}")
    lines.append(sep)
    lines.append(
        "|" + "|".join(f" {h:>{col_widths[i]}} " for i, h in enumerate(headers)) + "|"
    )
    lines.append(sep)
    for row in str_rows:
        lines.append(
            "|" + "|".join(f" {v:>{col_widths[i]}} " for i, v in enumerate(row)) + "|"
        )
    lines.append(sep)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pytest configuration
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Mark all tests in this package with @pytest.mark.benchmark."""
    for item in items:
        item.add_marker(pytest.mark.benchmark)
