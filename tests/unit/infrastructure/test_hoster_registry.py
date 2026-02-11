"""Tests for HosterResolverRegistry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.domain.entities.stremio import ResolvedStream
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
    _extract_hoster_from_url,
)


class TestExtractHosterFromUrl:
    def test_standard_domain(self) -> None:
        assert _extract_hoster_from_url("https://voe.sx/e/abc") == "voe"

    def test_two_part_domain(self) -> None:
        assert _extract_hoster_from_url("https://streamtape.com/v/abc") == "streamtape"

    def test_subdomain(self) -> None:
        assert _extract_hoster_from_url("https://cdn.filemoon.sx/e/abc") == "filemoon"

    def test_empty_url(self) -> None:
        assert _extract_hoster_from_url("") == ""

    def test_invalid_url(self) -> None:
        assert _extract_hoster_from_url("not-a-url") == ""


class TestHosterResolverRegistry:
    def test_register_and_list(self) -> None:
        resolver = MagicMock()
        resolver.name = "voe"
        registry = HosterResolverRegistry(resolvers=[resolver])

        assert "voe" in registry.supported_hosters

    @pytest.mark.asyncio
    async def test_dispatches_to_registered_resolver(self) -> None:
        expected = ResolvedStream(video_url="https://cdn.example.com/video.mp4")
        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(return_value=expected)

        registry = HosterResolverRegistry(resolvers=[resolver])
        result = await registry.resolve("https://voe.sx/e/abc123", hoster="voe")

        assert result is not None
        assert result.video_url == "https://cdn.example.com/video.mp4"
        resolver.resolve.assert_awaited_once_with("https://voe.sx/e/abc123")

    @pytest.mark.asyncio
    async def test_extracts_hoster_from_url_when_not_provided(self) -> None:
        expected = ResolvedStream(video_url="https://cdn.example.com/video.mp4")
        resolver = MagicMock()
        resolver.name = "streamtape"
        resolver.resolve = AsyncMock(return_value=expected)

        registry = HosterResolverRegistry(resolvers=[resolver])
        result = await registry.resolve("https://streamtape.com/v/abc")

        assert result is not None
        resolver.resolve.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_resolver_fails(self) -> None:
        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(return_value=None)

        registry = HosterResolverRegistry(resolvers=[resolver])
        result = await registry.resolve("https://voe.sx/e/abc", hoster="voe")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_resolver_raises(self) -> None:
        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(side_effect=RuntimeError("extraction failed"))

        registry = HosterResolverRegistry(resolvers=[resolver])
        result = await registry.resolve("https://voe.sx/e/abc", hoster="voe")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_hoster_without_client(self) -> None:
        registry = HosterResolverRegistry()
        result = await registry.resolve("https://unknown.com/e/abc")

        assert result is None

    @pytest.mark.asyncio
    async def test_probe_detects_direct_video(self) -> None:
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "video/mp4"}
        mock_response.url = "https://cdn.example.com/video.mp4"

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.head = AsyncMock(return_value=mock_response)

        registry = HosterResolverRegistry(http_client=http_client)
        result = await registry.resolve("https://cdn.example.com/video.mp4")

        assert result is not None
        assert result.video_url == "https://cdn.example.com/video.mp4"
        assert result.is_hls is False

    @pytest.mark.asyncio
    async def test_probe_detects_hls(self) -> None:
        mock_response = MagicMock()
        mock_response.headers = {
            "content-type": "application/vnd.apple.mpegurl; charset=utf-8"
        }
        mock_response.url = "https://cdn.example.com/master.m3u8"

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.head = AsyncMock(return_value=mock_response)

        registry = HosterResolverRegistry(http_client=http_client)
        result = await registry.resolve("https://cdn.example.com/master.m3u8")

        assert result is not None
        assert result.is_hls is True

    @pytest.mark.asyncio
    async def test_probe_returns_none_for_html(self) -> None:
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}
        mock_response.url = "https://example.com/embed"

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.head = AsyncMock(return_value=mock_response)

        registry = HosterResolverRegistry(http_client=http_client)
        result = await registry.resolve("https://example.com/embed")

        assert result is None

    @pytest.mark.asyncio
    async def test_probe_returns_none_on_network_error(self) -> None:
        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.head = AsyncMock(
            side_effect=httpx.ConnectError("connection failed")
        )

        registry = HosterResolverRegistry(http_client=http_client)
        result = await registry.resolve("https://down.example.com/video.mp4")

        assert result is None

    @pytest.mark.asyncio
    async def test_explicit_hoster_overrides_url_extraction(self) -> None:
        resolver = MagicMock()
        resolver.name = "custom"
        resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.example.com/v.mp4")
        )

        registry = HosterResolverRegistry(resolvers=[resolver])
        result = await registry.resolve("https://voe.sx/e/abc", hoster="custom")

        assert result is not None
        resolver.resolve.assert_awaited_once()
