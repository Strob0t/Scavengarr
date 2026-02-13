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
    async def test_hoster_hint_used_when_url_extraction_fails(self) -> None:
        """When URL domain extraction returns empty, the hoster hint is used."""
        resolver = MagicMock()
        resolver.name = "custom"
        resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.example.com/v.mp4")
        )

        registry = HosterResolverRegistry(resolvers=[resolver])
        # Malformed URL yields empty domain extraction, so "custom" hint kicks in
        result = await registry.resolve("not-a-valid-url", hoster="custom")

        assert result is not None
        resolver.resolve.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_url_domain_takes_priority_over_hoster_hint(self) -> None:
        """URL domain is authoritative — resolver is chosen by domain, not hint."""
        voe_resolver = MagicMock()
        voe_resolver.name = "voe"
        voe_resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.voe.sx/video.mp4")
        )
        custom_resolver = MagicMock()
        custom_resolver.name = "custom"
        custom_resolver.resolve = AsyncMock(return_value=None)

        registry = HosterResolverRegistry(resolvers=[voe_resolver, custom_resolver])
        # URL domain is "voe", even though hoster hint says "custom"
        result = await registry.resolve("https://voe.sx/e/abc", hoster="custom")

        assert result is not None
        voe_resolver.resolve.assert_awaited_once()
        custom_resolver.resolve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_follows_redirect_to_resolve(self) -> None:
        """When URL domain has no resolver, follow redirects and dispatch."""
        voe_resolver = MagicMock()
        voe_resolver.name = "voe"
        voe_resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.voe.sx/video.mp4")
        )

        mock_response = MagicMock()
        mock_response.url = "https://voe.sx/e/abc123"

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.head = AsyncMock(return_value=mock_response)

        registry = HosterResolverRegistry(
            resolvers=[voe_resolver], http_client=http_client
        )
        # cine.to/out/123 redirects to voe.sx/e/abc123
        result = await registry.resolve("https://cine.to/out/123")

        assert result is not None
        assert result.video_url == "https://cdn.voe.sx/video.mp4"
        voe_resolver.resolve.assert_awaited_once_with("https://voe.sx/e/abc123")

    @pytest.mark.asyncio
    async def test_redirect_to_unknown_hoster_falls_through_to_probe(self) -> None:
        """Redirect to unknown domain falls through to content-type probing."""
        mock_redirect_resp = MagicMock()
        mock_redirect_resp.url = "https://unknown-hoster.com/v/abc"

        mock_probe_resp = MagicMock()
        mock_probe_resp.headers = {"content-type": "video/mp4"}
        mock_probe_resp.url = "https://unknown-hoster.com/v/abc"

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.head = AsyncMock(side_effect=[mock_redirect_resp, mock_probe_resp])

        registry = HosterResolverRegistry(http_client=http_client)
        result = await registry.resolve("https://redirect.example/out/123")

        assert result is not None
        assert result.video_url == "https://unknown-hoster.com/v/abc"

    @pytest.mark.asyncio
    async def test_redirect_failure_falls_through_to_probe(self) -> None:
        """When redirect following fails, fall through to probe."""
        mock_probe_resp = MagicMock()
        mock_probe_resp.headers = {"content-type": "text/html"}
        mock_probe_resp.url = "https://broken.example/out/123"

        http_client = AsyncMock(spec=httpx.AsyncClient)
        # First call (redirect) fails, second call (probe) succeeds
        http_client.head = AsyncMock(
            side_effect=[
                httpx.ConnectError("redirect failed"),
                mock_probe_resp,
            ]
        )

        registry = HosterResolverRegistry(http_client=http_client)
        result = await registry.resolve("https://broken.example/out/123")

        # Probe returns None for text/html
        assert result is None

    @pytest.mark.asyncio
    async def test_no_redirect_when_url_stays_same(self) -> None:
        """When redirect returns same URL, skip redirect step."""
        mock_response = MagicMock()
        mock_response.url = "https://noredirect.example/embed"
        mock_response.headers = {"content-type": "text/html"}

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.head = AsyncMock(return_value=mock_response)

        registry = HosterResolverRegistry(http_client=http_client)
        result = await registry.resolve("https://noredirect.example/embed")

        # No redirect, probe returns None for text/html
        assert result is None

    @pytest.mark.asyncio
    async def test_hoster_hint_fallback_for_unknown_domain(self) -> None:
        """VOE redirect domains (e.g., lauradaydo.com) use hoster hint fallback."""
        voe_resolver = MagicMock()
        voe_resolver.name = "voe"
        voe_resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.voe.sx/video.mp4")
        )

        # HEAD returns same URL (no redirect — already final domain)
        mock_response = MagicMock()
        mock_response.url = "https://lauradaydo.com/e/abc123"
        mock_response.headers = {"content-type": "text/html"}

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.head = AsyncMock(return_value=mock_response)

        registry = HosterResolverRegistry(
            resolvers=[voe_resolver], http_client=http_client
        )
        result = await registry.resolve("https://lauradaydo.com/e/abc123", hoster="voe")

        assert result is not None
        assert result.video_url == "https://cdn.voe.sx/video.mp4"
        voe_resolver.resolve.assert_awaited_once_with("https://lauradaydo.com/e/abc123")

    @pytest.mark.asyncio
    async def test_hoster_hint_not_tried_when_same_as_domain(self) -> None:
        """When hint matches URL domain, no double dispatch."""
        mock_response = MagicMock()
        mock_response.url = "https://unknown.example/embed"
        mock_response.headers = {"content-type": "text/html"}

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.head = AsyncMock(return_value=mock_response)

        registry = HosterResolverRegistry(http_client=http_client)
        # hoster hint "example" matches extracted domain "example"
        result = await registry.resolve(
            "https://unknown.example/embed", hoster="example"
        )

        # Falls through to probe which returns None for text/html
        assert result is None

    @pytest.mark.asyncio
    async def test_hoster_hint_tried_after_redirect_fails(self) -> None:
        """Redirect fails, but hoster hint fallback succeeds."""
        voe_resolver = MagicMock()
        voe_resolver.name = "voe"
        voe_resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.voe.sx/v.mp4")
        )

        http_client = AsyncMock(spec=httpx.AsyncClient)
        # Redirect following throws
        http_client.head = AsyncMock(side_effect=httpx.ConnectError("redirect failed"))

        registry = HosterResolverRegistry(
            resolvers=[voe_resolver], http_client=http_client
        )
        result = await registry.resolve("https://randomdomain.com/e/abc", hoster="voe")

        assert result is not None
        voe_resolver.resolve.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_calls_resolver_cleanup(self) -> None:
        """cleanup() calls cleanup on resolvers that have one."""
        resolver = MagicMock()
        resolver.name = "voe"
        resolver.cleanup = AsyncMock()

        registry = HosterResolverRegistry(resolvers=[resolver])
        await registry.cleanup()

        resolver.cleanup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cleanup_skips_resolvers_without_cleanup(self) -> None:
        """cleanup() does not error when resolver has no cleanup method."""
        resolver = MagicMock(spec=["name", "resolve"])
        resolver.name = "basic"

        registry = HosterResolverRegistry(resolvers=[resolver])
        # Should not raise
        await registry.cleanup()

    @pytest.mark.asyncio
    async def test_result_cache_prevents_repeated_resolution(self) -> None:
        """Successful resolution is cached — second call doesn't invoke resolver."""
        expected = ResolvedStream(video_url="https://cdn.example.com/video.mp4")
        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(return_value=expected)

        registry = HosterResolverRegistry(resolvers=[resolver])

        result1 = await registry.resolve("https://voe.sx/e/abc123")
        assert result1 is not None
        assert resolver.resolve.await_count == 1

        result2 = await registry.resolve("https://voe.sx/e/abc123")
        assert result2 is not None
        assert result2.video_url == expected.video_url
        # Resolver should NOT have been called again
        assert resolver.resolve.await_count == 1

    @pytest.mark.asyncio
    async def test_failed_result_cached(self) -> None:
        """Failed resolution (None) is cached too."""
        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(return_value=None)

        registry = HosterResolverRegistry(resolvers=[resolver])

        result1 = await registry.resolve("https://voe.sx/e/dead")
        assert result1 is None

        result2 = await registry.resolve("https://voe.sx/e/dead")
        assert result2 is None
        # Only one actual resolve call
        assert resolver.resolve.await_count == 1

    @pytest.mark.asyncio
    async def test_redirect_cache_prevents_repeated_head(self) -> None:
        """Redirect mapping is cached — second call skips HEAD redirect check."""
        voe_resolver = MagicMock()
        voe_resolver.name = "voe"
        voe_resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.voe.sx/v.mp4")
        )

        mock_response = MagicMock()
        mock_response.url = "https://voe.sx/e/abc123"

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.head = AsyncMock(return_value=mock_response)

        registry = HosterResolverRegistry(
            resolvers=[voe_resolver], http_client=http_client
        )

        # First call: follows redirect via HEAD
        result1 = await registry.resolve("https://cine.to/out/123")
        assert result1 is not None
        head_count_after_first = http_client.head.await_count

        # Second call: should use result cache, no new HEAD
        result2 = await registry.resolve("https://cine.to/out/123")
        assert result2 is not None
        assert http_client.head.await_count == head_count_after_first

    @pytest.mark.asyncio
    async def test_resolve_timeout_parameter(self) -> None:
        """resolve_timeout parameter is accepted."""
        registry = HosterResolverRegistry(resolve_timeout=5.0)
        assert registry._resolve_timeout == 5.0
