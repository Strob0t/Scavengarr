"""Tests for wiring the CrawlJob repository and factory in composition."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from scavengarr.domain.plugins import SearchResult
from scavengarr.interfaces.composition import build_crawljob_store


def test_crawljob_ttl_reaches_repository_and_jobs() -> None:
    """The cache entry and the job's expires_at must use the same TTL."""
    config = SimpleNamespace(cache=SimpleNamespace(crawljob_ttl_seconds=7200))

    repo, factory = build_crawljob_store(config, AsyncMock())

    assert repo.ttl == 7200
    job = factory.create_from_search_result(
        SearchResult(title="Movie", download_link="https://example.com/dl")
    )
    assert job.expires_at - job.created_at == timedelta(seconds=7200)
