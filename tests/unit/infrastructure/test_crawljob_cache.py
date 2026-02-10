"""Tests for CacheCrawlJobRepository."""

from __future__ import annotations

import pickle
from unittest.mock import AsyncMock

from scavengarr.domain.entities.crawljob import CrawlJob
from scavengarr.infrastructure.persistence.crawljob_cache import (
    CacheCrawlJobRepository,
)


class TestCacheCrawlJobRepository:
    async def test_save_stores_pickled_job(
        self, mock_cache: AsyncMock, crawljob: CrawlJob
    ) -> None:
        repo = CacheCrawlJobRepository(cache=mock_cache)
        await repo.save(crawljob)

        mock_cache.set.assert_awaited_once()
        call_args = mock_cache.set.call_args
        key = call_args[0][0]
        value = call_args[0][1]
        assert key == f"crawljob:{crawljob.job_id}"
        # Should be pickle-encoded
        restored = pickle.loads(value)
        assert restored.job_id == crawljob.job_id

    async def test_get_returns_crawljob(
        self, mock_cache: AsyncMock, crawljob: CrawlJob
    ) -> None:
        pickled = pickle.dumps(crawljob)
        mock_cache.get = AsyncMock(return_value=pickled)
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

    async def test_get_handles_corrupt_data(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(return_value=b"not-valid-pickle")
        repo = CacheCrawlJobRepository(cache=mock_cache)
        result = await repo.get("some-id")
        assert result is None

    async def test_custom_ttl(
        self, mock_cache: AsyncMock, crawljob: CrawlJob
    ) -> None:
        repo = CacheCrawlJobRepository(cache=mock_cache, ttl_seconds=7200)
        await repo.save(crawljob)
        call_kwargs = mock_cache.set.call_args[1]
        assert call_kwargs["ttl"] == 7200
