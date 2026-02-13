"""Tests for CacheStreamLinkRepository."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from scavengarr.domain.entities.stremio import CachedStreamLink
from scavengarr.infrastructure.persistence.stream_link_cache import (
    CacheStreamLinkRepository,
    _serialize_link,
)


def _make_link(
    *,
    stream_id: str = "abc123",
    hoster_url: str = "https://voe.sx/e/abc",
    title: str = "Iron Man",
    hoster: str = "voe",
) -> CachedStreamLink:
    return CachedStreamLink(
        stream_id=stream_id,
        hoster_url=hoster_url,
        title=title,
        hoster=hoster,
    )


class TestCacheStreamLinkRepository:
    async def test_save_stores_json_link(self, mock_cache: AsyncMock) -> None:
        link = _make_link()
        repo = CacheStreamLinkRepository(cache=mock_cache)
        await repo.save(link)

        mock_cache.set.assert_awaited_once()
        call_args = mock_cache.set.call_args
        key = call_args[0][0]
        value = call_args[0][1]
        assert key == "streamlink:abc123"
        restored = json.loads(value)
        assert restored["stream_id"] == "abc123"
        assert restored["hoster_url"] == "https://voe.sx/e/abc"

    async def test_save_uses_configured_ttl(self, mock_cache: AsyncMock) -> None:
        link = _make_link()
        repo = CacheStreamLinkRepository(cache=mock_cache, ttl_seconds=3600)
        await repo.save(link)
        call_kwargs = mock_cache.set.call_args[1]
        assert call_kwargs["ttl"] == 3600

    async def test_get_returns_cached_link(self, mock_cache: AsyncMock) -> None:
        link = _make_link()
        serialized = _serialize_link(link)
        mock_cache.get = AsyncMock(return_value=serialized)
        repo = CacheStreamLinkRepository(cache=mock_cache)
        result = await repo.get("abc123")
        assert result is not None
        assert result.stream_id == "abc123"
        assert result.hoster_url == "https://voe.sx/e/abc"
        assert result.title == "Iron Man"
        assert result.hoster == "voe"

    async def test_get_returns_none_for_missing(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        repo = CacheStreamLinkRepository(cache=mock_cache)
        result = await repo.get("nonexistent")
        assert result is None

    async def test_get_handles_corrupt_data(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value="not-valid-json{{{")
        repo = CacheStreamLinkRepository(cache=mock_cache)
        result = await repo.get("corrupt")
        assert result is None

    async def test_default_ttl_is_7200(self, mock_cache: AsyncMock) -> None:
        link = _make_link()
        repo = CacheStreamLinkRepository(cache=mock_cache)
        await repo.save(link)
        call_kwargs = mock_cache.set.call_args[1]
        assert call_kwargs["ttl"] == 7200
