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


class TestWhileTheCacheFails:
    """diskcache raised on every write (locked, disk full), and every
    answer came back empty; Redis lost the writes, and every /play and HLS
    proxy request answered 404 (code review, 2026-10-06). The links of the
    latest answers stay in memory and play while the cache fails."""

    async def test_a_failed_save_raises_nothing_and_the_link_plays(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.set = AsyncMock(side_effect=OSError("database is locked"))
        mock_cache.get = AsyncMock(side_effect=OSError("database is locked"))
        repo = CacheStreamLinkRepository(cache=mock_cache)

        await repo.save(_make_link())

        assert await repo.get("abc123") == _make_link()

    async def test_a_lost_write_still_plays(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        repo = CacheStreamLinkRepository(cache=mock_cache)

        await repo.save(_make_link())

        assert await repo.get("abc123") == _make_link()

    async def test_a_newer_save_replaces_the_memory_copy(
        self, mock_cache: AsyncMock
    ) -> None:
        repo = CacheStreamLinkRepository(cache=mock_cache)
        await repo.save(_make_link(title="old"))

        await repo.save(_make_link(title="new"))

        stored = await repo.get("abc123")
        assert stored is not None and stored.title == "new"

    async def test_memory_keeps_the_latest_links(self, mock_cache: AsyncMock) -> None:
        mock_cache.get = AsyncMock(return_value=None)
        repo = CacheStreamLinkRepository(cache=mock_cache, recent=2)

        for stream_id in ("a", "b", "c"):
            await repo.save(_make_link(stream_id=stream_id))

        assert await repo.get("a") is None
        assert await repo.get("c") is not None

    async def test_a_failed_load_of_another_link_gives_none(
        self, mock_cache: AsyncMock
    ) -> None:
        mock_cache.get = AsyncMock(side_effect=OSError("down"))
        repo = CacheStreamLinkRepository(cache=mock_cache)

        assert await repo.get("unknown") is None


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

    async def test_default_ttl_is_a_week(self, mock_cache: AsyncMock) -> None:
        """Links resolve again when stale, so Continue Watching days later
        still plays."""
        link = _make_link()
        repo = CacheStreamLinkRepository(cache=mock_cache)
        await repo.save(link)
        call_kwargs = mock_cache.set.call_args[1]
        assert call_kwargs["ttl"] == 7 * 24 * 3600

    async def test_the_address_binding_round_trips(self, mock_cache: AsyncMock) -> None:
        link = CachedStreamLink(
            stream_id="file1",
            hoster_url="https://mixdrop.ag/e/abc",
            hoster="mixdrop",
            video_url="https://cdn.mixdrop.example/v.mp4",
            address_bound=True,
        )
        mock_cache.get = AsyncMock(return_value=_serialize_link(link))
        repo = CacheStreamLinkRepository(cache=mock_cache)

        result = await repo.get("file1")

        assert result is not None
        assert result.address_bound is True

    async def test_a_record_from_before_the_binding_reads_unbound(
        self, mock_cache: AsyncMock
    ) -> None:
        """A stored link from before the change keeps /play."""
        old_data = json.dumps(
            {
                "stream_id": "old2",
                "hoster_url": "https://mixdrop.ag/e/abc",
                "hoster": "mixdrop",
                "video_url": "https://cdn.mixdrop.example/v.mp4",
                "is_hls": False,
                "resolved_at": 1791200000.5,
            }
        )
        mock_cache.get = AsyncMock(return_value=old_data)
        repo = CacheStreamLinkRepository(cache=mock_cache)

        result = await repo.get("old2")

        assert result is not None
        assert result.address_bound is False

    async def test_save_includes_hls_proxy_fields(self, mock_cache: AsyncMock) -> None:
        link = CachedStreamLink(
            stream_id="hls1",
            hoster_url="https://dropload.io/e/abc123def456",
            title="Movie",
            hoster="dropload",
            video_url="https://cdn.dropcdn.io/hls2/master.m3u8",
            video_headers='{"Referer": "https://dropload.io/e/abc123def456"}',
            is_hls=True,
        )
        repo = CacheStreamLinkRepository(cache=mock_cache)
        await repo.save(link)

        value = mock_cache.set.call_args[0][1]
        restored = json.loads(value)
        assert restored["video_url"] == "https://cdn.dropcdn.io/hls2/master.m3u8"
        assert restored["is_hls"] is True
        assert "Referer" in restored["video_headers"]

    async def test_get_round_trips_hls_proxy_fields(
        self, mock_cache: AsyncMock
    ) -> None:
        link = CachedStreamLink(
            stream_id="hls2",
            hoster_url="https://dropload.io/e/abc123def456",
            title="Movie",
            hoster="dropload",
            video_url="https://cdn.dropcdn.io/hls2/master.m3u8",
            video_headers='{"Referer": "https://dropload.io/"}',
            is_hls=True,
            resolved_at=1791200000.5,
        )
        serialized = _serialize_link(link)
        mock_cache.get = AsyncMock(return_value=serialized)
        repo = CacheStreamLinkRepository(cache=mock_cache)
        result = await repo.get("hls2")

        assert result is not None
        assert result.video_url == "https://cdn.dropcdn.io/hls2/master.m3u8"
        assert result.video_headers == '{"Referer": "https://dropload.io/"}'
        assert result.is_hls is True
        assert result.resolved_at == 1791200000.5

    async def test_backward_compat_missing_hls_fields(
        self, mock_cache: AsyncMock
    ) -> None:
        """Old cache entries without HLS fields still deserialize correctly."""
        old_data = json.dumps(
            {
                "stream_id": "old1",
                "hoster_url": "https://voe.sx/e/abc",
                "title": "Old Movie",
                "hoster": "voe",
            }
        )
        mock_cache.get = AsyncMock(return_value=old_data)
        repo = CacheStreamLinkRepository(cache=mock_cache)
        result = await repo.get("old1")

        assert result is not None
        assert result.video_url == ""
        assert result.video_headers == ""
        assert result.is_hls is False
        assert result.resolved_at == 0.0
