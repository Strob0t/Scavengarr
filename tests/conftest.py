"""Shared test fixtures for Scavengarr test suite."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from scavengarr.application.factories import CrawlJobFactory
from scavengarr.domain.entities import TorznabItem, TorznabQuery
from scavengarr.domain.entities.crawljob import CrawlJob
from scavengarr.domain.plugins import SearchResult

# ---------------------------------------------------------------------------
# Domain entity fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def search_result() -> SearchResult:
    """Minimal valid SearchResult."""
    return SearchResult(
        title="Iron.Man.2008.1080p.BluRay",
        download_link="https://example.com/download/1",
        seeders=10,
        leechers=2,
        size="4.5 GB",
        release_name="Iron.Man.2008.1080p.BluRay.x264",
        description="Iron Man movie",
        source_url="https://example.com/movie/1",
        metadata={},
    )


@pytest.fixture()
def torznab_query() -> TorznabQuery:
    """Minimal valid TorznabQuery for search."""
    return TorznabQuery(
        action="search",
        plugin_name="filmpalast",
        query="iron man",
    )


@pytest.fixture()
def torznab_item() -> TorznabItem:
    """Minimal valid TorznabItem."""
    return TorznabItem(
        title="Iron.Man.2008.1080p.BluRay",
        download_url="https://example.com/download/1",
        job_id="test-job-id-123",
    )


@pytest.fixture()
def crawljob() -> CrawlJob:
    """CrawlJob with known timestamps for testing."""
    now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return CrawlJob(
        job_id="test-job-id-123",
        text="https://example.com/download/1",
        package_name="Iron.Man.2008",
        validated_urls=["https://example.com/download/1"],
        source_url="https://example.com/movie/1",
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )


# ---------------------------------------------------------------------------
# Application fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def crawljob_factory() -> CrawlJobFactory:
    """CrawlJobFactory with default settings."""
    return CrawlJobFactory(default_ttl_hours=1)


# ---------------------------------------------------------------------------
# Mock port fixtures
# ---------------------------------------------------------------------------


@dataclass
class FakePlugin:
    """Minimal fake plugin satisfying PluginRegistryPort.get() return."""

    name: str = "filmpalast"
    version: str = "1.0.0"
    scraping: Any = None

    def __post_init__(self) -> None:
        if self.scraping is None:
            self.scraping = _FakeScrapingConfig()


@dataclass
class _FakeScrapingConfig:
    mode: str = "scrapy"


@pytest.fixture()
def fake_plugin() -> FakePlugin:
    return FakePlugin()


@pytest.fixture()
def mock_plugin_registry(fake_plugin: FakePlugin) -> MagicMock:
    """Mock PluginRegistryPort (synchronous methods)."""
    registry = MagicMock()
    registry.discover.return_value = None
    registry.list_names.return_value = ["filmpalast"]
    registry.get.return_value = fake_plugin
    return registry


@pytest.fixture()
def mock_search_engine() -> AsyncMock:
    """Mock SearchEnginePort."""
    engine = AsyncMock()
    engine.search = AsyncMock(return_value=[])
    engine.validate_results = AsyncMock(return_value=[])
    return engine


@pytest.fixture()
def mock_crawljob_repo() -> AsyncMock:
    """Mock CrawlJobRepository."""
    repo = AsyncMock()
    repo.save = AsyncMock()
    repo.get = AsyncMock(return_value=None)
    return repo


class FakePythonPlugin:
    """Minimal fake Python plugin (has search, no scraping attribute)."""

    def __init__(self, name: str = "boerse", base_url: str = "https://boerse.am") -> None:
        self.name = name
        self.base_url = base_url
        self._results: list[Any] = []

    async def search(
        self, query: str, category: int | None = None,
    ) -> list[Any]:
        return self._results


@pytest.fixture()
def fake_python_plugin() -> FakePythonPlugin:
    return FakePythonPlugin()


@pytest.fixture()
def mock_cache() -> AsyncMock:
    """Mock CachePort."""
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    cache.delete = AsyncMock(return_value=True)
    cache.exists = AsyncMock(return_value=False)
    cache.clear = AsyncMock()
    cache.aclose = AsyncMock()
    return cache
