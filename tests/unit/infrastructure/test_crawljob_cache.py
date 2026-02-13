"""Tests for CacheCrawlJobRepository."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from scavengarr.domain.entities.crawljob import CrawlJob
from scavengarr.infrastructure.persistence.crawljob_cache import (
    CacheCrawlJobRepository,
    _serialize_crawljob,
)


class TestCacheCrawlJobRepository:
    async def test_save_stores_json_job(
        self, mock_cache: AsyncMock, crawljob: CrawlJob
    ) -> None:
        repo = CacheCrawlJobRepository(cache=mock_cache)
        await repo.save(crawljob)

        mock_cache.set.assert_awaited_once()
        call_args = mock_cache.set.call_args
        key = call_args[0][0]
        value = call_args[0][1]
        assert key == f"crawljob:{crawljob.job_id}"
        # Should be JSON-encoded
        restored = json.loads(value)
        assert restored["job_id"] == crawljob.job_id

    async def test_get_returns_crawljob(
        self, mock_cache: AsyncMock, crawljob: CrawlJob
    ) -> None:
        serialized = _serialize_crawljob(crawljob)
        mock_cache.get = AsyncMock(return_value=serialized)
        repo = CacheCrawlJobRepository(cache=mock_cache)
        result = await repo.get(crawljob.job_id)
        assert result is not None
        assert result.job_id == crawljob.job_id

    async def test_get_returns_none_for_missing_key(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        repo = CacheCrawlJobRepository(cache=mock_cache)
        result = await repo.get("nonexistent-id")
        assert result is None

    async def test_get_handles_corrupt_data(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value="not-valid-json{{{")
        repo = CacheCrawlJobRepository(cache=mock_cache)
        result = await repo.get("some-id")
        assert result is None

    async def test_custom_ttl(self, mock_cache: AsyncMock, crawljob: CrawlJob) -> None:
        repo = CacheCrawlJobRepository(cache=mock_cache, ttl_seconds=7200)
        await repo.save(crawljob)
        call_kwargs = mock_cache.set.call_args[1]
        assert call_kwargs["ttl"] == 7200

    async def test_roundtrip_preserves_all_fields(
        self, mock_cache: AsyncMock, crawljob: CrawlJob
    ) -> None:
        """Serialize and deserialize preserves all CrawlJob fields."""
        serialized = _serialize_crawljob(crawljob)
        mock_cache.get = AsyncMock(return_value=serialized)
        repo = CacheCrawlJobRepository(cache=mock_cache)
        result = await repo.get(crawljob.job_id)
        assert result is not None
        assert result.job_id == crawljob.job_id
        assert result.text == crawljob.text
        assert result.package_name == crawljob.package_name
        assert result.priority == crawljob.priority
        assert result.auto_start == crawljob.auto_start
        assert result.validated_urls == crawljob.validated_urls
