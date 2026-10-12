"""Tests for HosterResolverRegistry."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.browser.page_gate import PageBusy, PageGate
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.hoster_resolvers import extract_domain
from scavengarr.infrastructure.hoster_resolvers import registry as registry_module
from scavengarr.infrastructure.hoster_resolvers.registry import (
    HosterResolverRegistry,
)
from scavengarr.infrastructure.telemetry import Telemetry


class TestExtractDomain:
    def test_standard_domain(self) -> None:
        assert extract_domain("https://voe.sx/e/abc") == "voe"

    def test_two_part_domain(self) -> None:
        assert extract_domain("https://streamtape.com/v/abc") == "streamtape"

    def test_subdomain(self) -> None:
        assert extract_domain("https://cdn.filemoon.sx/e/abc") == "filemoon"

    def test_empty_url(self) -> None:
        assert extract_domain("") == ""

    def test_invalid_url(self) -> None:
        assert extract_domain("not-a-url") == ""


class TestHosterResolverRegistry:
    def test_register_and_list(self) -> None:
        resolver = MagicMock()
        resolver.name = "voe"
        registry = HosterResolverRegistry(resolvers=[resolver])

        assert "voe" in registry.supported_hosters

    def test_domain_claimed_twice_keeps_the_first_and_warns(self) -> None:
        # Specific resolvers are registered before the generic XFS/DDL ones
        first = SimpleNamespace(name="vidhide", supported_domains=frozenset({"dup"}))
        second = SimpleNamespace(name="vidguard", supported_domains=frozenset({"dup"}))

        with structlog.testing.capture_logs() as logs:
            registry = HosterResolverRegistry(resolvers=[first, second])

        assert registry._domain_map["dup"] is first
        assert any(
            e["event"] == "hoster_domain_conflict"
            and e["domain"] == "dup"
            and e["kept"] == "vidhide"
            and e["ignored"] == "vidguard"
            for e in logs
        )

    def test_supported_domains_include_aliases(self) -> None:
        vidhide = SimpleNamespace(
            name="vidhide", supported_domains=frozenset({"vidhide", "filelions"})
        )
        voe = SimpleNamespace(name="voe")
        registry = HosterResolverRegistry(resolvers=[vidhide, voe])

        assert registry.supported_domains == frozenset({"vidhide", "filelions", "voe"})

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("vidhide", "vidhide"),
            ("filelions", "vidhide"),
            ("voe", "voe"),
            ("unknown-hoster", None),
            ("", None),
        ],
    )
    def test_canonical_hoster(self, name: str, expected: str | None) -> None:
        vidhide = SimpleNamespace(
            name="vidhide", supported_domains=frozenset({"vidhide", "filelions"})
        )
        voe = SimpleNamespace(name="voe")
        registry = HosterResolverRegistry(resolvers=[vidhide, voe])

        assert registry.canonical_hoster(name) == expected

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
    async def test_an_address_bound_resolver_marks_its_stream(self) -> None:
        resolver = MagicMock()
        resolver.name = "doodstream"
        resolver.address_bound = True
        resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.example.com/v.mp4")
        )
        registry = HosterResolverRegistry(resolvers=[resolver])

        result = await registry.resolve("https://dood.to/e/abc", hoster="doodstream")

        assert result is not None
        assert result.address_bound is True

    @pytest.mark.asyncio
    async def test_a_resolver_without_the_attribute_gives_an_unbound_stream(
        self,
    ) -> None:
        resolver = MagicMock()
        resolver.name = "voe"
        del resolver.address_bound
        resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.example.com/v.mp4")
        )
        registry = HosterResolverRegistry(resolvers=[resolver])

        result = await registry.resolve("https://voe.sx/e/abc123", hoster="voe")

        assert result is not None
        assert result.address_bound is False

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
    @respx.mock
    async def test_probe_detects_direct_video(self) -> None:
        respx.head("https://cdn.example.com/video.mp4").respond(
            200, headers={"content-type": "video/mp4"}
        )
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(http_client=client)

            result = await registry.resolve("https://cdn.example.com/video.mp4")

        assert result is not None
        assert result.video_url == "https://cdn.example.com/video.mp4"
        assert result.is_hls is False

    @pytest.mark.asyncio
    @respx.mock
    async def test_probe_detects_hls(self) -> None:
        respx.head("https://cdn.example.com/master.m3u8").respond(
            200,
            headers={"content-type": "application/vnd.apple.mpegurl; charset=utf-8"},
        )
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(http_client=client)

            result = await registry.resolve("https://cdn.example.com/master.m3u8")

        assert result is not None
        assert result.is_hls is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_media_url_is_probed_not_given_to_the_domain_resolver(
        self,
    ) -> None:
        # moflix hands out its own HLS playlists on moflix-stream.day; the
        # domain's resolver (VidHide) expects an embed page and failed on
        # every one of them (60 of 60 in the live test)
        vidhide = MagicMock()
        vidhide.name = "vidhide"
        vidhide.supported_domains = frozenset({"vidhide", "moflix-stream"})
        vidhide.resolve = AsyncMock(return_value=None)
        url = "https://gandalf.moflix-stream.day/movies/Dune.2021/master.m3u8?md5=x"
        respx.head(url).respond(
            200, headers={"content-type": "application/vnd.apple.mpegurl"}
        )
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(resolvers=[vidhide], http_client=client)

            result = await registry.resolve(url, "moflix-stream")

        assert result is not None
        assert result.video_url == url
        assert result.is_hls is True
        vidhide.resolve.assert_not_awaited()

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
    @respx.mock
    async def test_probe_ignores_a_refusal_with_a_media_type(self) -> None:
        """A CDN that types its error page by the path made a refusal a
        stream, cached for an hour (code review, 2026-10-06)."""
        respx.head("https://cdn.example.com/master.m3u8").respond(
            403, headers={"content-type": "application/vnd.apple.mpegurl"}
        )
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(http_client=client)

            result = await registry.resolve("https://cdn.example.com/master.m3u8")

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

    def test_canonical_hoster_of_a_claimed_host(self) -> None:
        vidara = SimpleNamespace(
            name="strmup",
            supported_domains=frozenset({"strmup"}),
            supported_hosts=frozenset({"kinoger.pw"}),
        )
        registry = HosterResolverRegistry(resolvers=[vidara])

        assert registry.canonical_hoster("kinoger.pw") == "strmup"
        assert registry.canonical_hoster("kinoger") is None

    @pytest.mark.asyncio
    async def test_host_claim_leaves_other_hosts_of_the_name_alone(self) -> None:
        """kinoger.pw is a Vidara player; kinoger.ru links redirect to VOE
        mirrors. Claiming the name ``kinoger`` would send both to Vidara."""
        vidara = MagicMock()
        vidara.name = "strmup"
        vidara.supported_domains = frozenset({"strmup"})
        vidara.supported_hosts = frozenset({"kinoger.pw"})
        vidara.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.example/v.m3u8")
        )
        voe = MagicMock()
        voe.name = "voe"
        voe.supported_domains = frozenset({"voe", "jeremyparticipantanything"})
        voe.supported_hosts = frozenset()
        voe.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.voe.sx/v.m3u8")
        )
        redirect = MagicMock()
        redirect.url = "https://jeremyparticipantanything.com/e/bs3h8ltwwh2b"
        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.head = AsyncMock(return_value=redirect)
        registry = HosterResolverRegistry(
            resolvers=[vidara, voe], http_client=http_client
        )

        pw = await registry.resolve("https://kinoger.pw/e/5wCjBALU9QDHF", "kinoger")
        ru = await registry.resolve("https://kinoger.ru/e/bs3h8ltwwh2b", "kinoger")

        assert pw is not None and ru is not None
        vidara.resolve.assert_awaited_once_with("https://kinoger.pw/e/5wCjBALU9QDHF")
        voe.resolve.assert_awaited_once_with(
            "https://jeremyparticipantanything.com/e/bs3h8ltwwh2b"
        )

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
    @respx.mock
    async def test_redirect_to_unknown_hoster_falls_through_to_probe(self) -> None:
        """Redirect to unknown domain falls through to content-type probing."""
        respx.head("https://redirect.example/out/123").respond(
            302, headers={"location": "https://unknown-hoster.com/v/abc"}
        )
        respx.head("https://unknown-hoster.com/v/abc").respond(
            200, headers={"content-type": "video/mp4"}
        )
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(http_client=client)

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
    async def test_a_cached_stream_keeps_the_time_of_its_resolution(self) -> None:
        """Links stamped the time an answer was built treated video URLs up
        to two hours old as fresh (code review, 2026-10-06)."""
        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.example.com/v.mp4")
        )
        registry = HosterResolverRegistry(resolvers=[resolver])

        before = time.time()
        first = await registry.resolve("https://voe.sx/e/abc123")
        await asyncio.sleep(0.02)
        cached = await registry.resolve("https://voe.sx/e/abc123")

        assert first is not None and cached is not None
        assert before <= first.resolved_at <= time.time()
        assert cached.resolved_at == first.resolved_at

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

    async def test_cached_tells_alive_dead_and_unknown_apart(self) -> None:
        """A cached search answers from the cache without resolving."""
        # Already stamped, so the registry keeps it as it is
        stream = ResolvedStream(
            video_url="https://cdn.example.com/video.mp4", resolved_at=1.0
        )
        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(
            side_effect=lambda url: None if url.endswith("dead") else stream
        )
        registry = HosterResolverRegistry(resolvers=[resolver])
        await registry.resolve("https://voe.sx/e/alive")
        await registry.resolve("https://voe.sx/e/dead")

        assert registry.cached("https://voe.sx/e/alive\n") == (True, stream)
        assert registry.cached("https://voe.sx/e/dead") == (True, None)
        assert registry.cached("https://voe.sx/e/new") == (False, None)
        assert resolver.resolve.await_count == 2

    async def test_refresh_resolves_past_the_cache(self) -> None:
        """A CDN refused the cached stream: the next resolution is new."""
        old = ResolvedStream(
            video_url="https://cdn.example.com/old.mp4", resolved_at=1.0
        )
        new = ResolvedStream(
            video_url="https://cdn.example.com/new.mp4", resolved_at=2.0
        )
        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(side_effect=[old, new])
        registry = HosterResolverRegistry(resolvers=[resolver])
        await registry.resolve("https://voe.sx/e/abc")

        assert await registry.resolve("https://voe.sx/e/abc", refresh=True) == new
        assert await registry.resolve("https://voe.sx/e/abc") == new
        assert resolver.resolve.await_count == 2

    async def test_an_expired_resolution_is_not_cached(self) -> None:
        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(return_value=None)
        registry = HosterResolverRegistry(resolvers=[resolver])
        await registry.resolve("https://voe.sx/e/dead")

        registry._result_cache["https://voe.sx/e/dead"].expires_at = 0.0

        assert registry.cached("https://voe.sx/e/dead") == (False, None)

    @pytest.mark.asyncio
    async def test_resolver_is_bounded_by_resolve_timeout(self) -> None:
        """A hanging resolver is cut off (/play used to hang for a minute)."""

        async def _hang(url: str) -> ResolvedStream | None:
            await asyncio.sleep(10)
            return None

        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(side_effect=_hang)
        registry = HosterResolverRegistry(resolvers=[resolver], resolve_timeout=0.05)

        started = time.monotonic()
        assert await registry.resolve("https://voe.sx/e/slow") is None
        assert time.monotonic() - started < 1

    @pytest.mark.asyncio
    async def test_resolve_timeout_is_not_cached_as_dead(self) -> None:
        calls = 0

        async def _slow_then_fast(url: str) -> ResolvedStream | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                await asyncio.sleep(10)
            return ResolvedStream(video_url="https://cdn.example.com/v.mp4")

        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(side_effect=_slow_then_fast)
        registry = HosterResolverRegistry(resolvers=[resolver], resolve_timeout=0.05)

        assert await registry.resolve("https://voe.sx/e/abc") is None
        assert await registry.resolve("https://voe.sx/e/abc") is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error", [httpx.ReadTimeout("slow"), httpx.ConnectError("down")]
    )
    async def test_network_failures_are_not_cached_as_dead(
        self, error: Exception
    ) -> None:
        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(
            side_effect=[error, ResolvedStream(video_url="https://cdn.example.com/v")]
        )
        registry = HosterResolverRegistry(resolvers=[resolver])

        assert await registry.resolve("https://voe.sx/e/abc") is None
        assert await registry.resolve("https://voe.sx/e/abc") is not None

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

    @pytest.mark.asyncio
    async def test_domain_alias_dispatch(self) -> None:
        """Resolver with supported_domains is found via domain alias."""
        expected = ResolvedStream(video_url="https://cdn.example.com/video.mp4")
        resolver = MagicMock()
        resolver.name = "vidhide"
        resolver.supported_domains = frozenset({"vidhide", "filelions", "streamhide"})
        resolver.resolve = AsyncMock(return_value=expected)

        registry = HosterResolverRegistry(resolvers=[resolver])

        # "filelions" is not the resolver name but is in supported_domains
        result = await registry.resolve("https://filelions.live/v/abc123")
        assert result is not None
        assert result.video_url == "https://cdn.example.com/video.mp4"
        resolver.resolve.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_domain_alias_dispatch_after_redirect(self) -> None:
        """Redirect to a domain alias resolves via domain map."""
        expected = ResolvedStream(video_url="https://cdn.example.com/video.mp4")
        resolver = MagicMock()
        resolver.name = "vidhide"
        resolver.supported_domains = frozenset({"vidhide", "filelions"})
        resolver.resolve = AsyncMock(return_value=expected)

        mock_response = MagicMock()
        mock_response.url = "https://filelions.live/v/abc123"

        http_client = AsyncMock(spec=httpx.AsyncClient)
        http_client.head = AsyncMock(return_value=mock_response)

        registry = HosterResolverRegistry(resolvers=[resolver], http_client=http_client)
        result = await registry.resolve("https://random-redirect.com/out/123")

        assert result is not None
        resolver.resolve.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_result_cache_respects_max_size(self) -> None:
        """Result cache evicts oldest entries when exceeding max size."""
        from scavengarr.infrastructure.hoster_resolvers import registry as reg_mod

        resolver = MagicMock()
        resolver.name = "voe"
        resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.example.com/v.mp4")
        )

        registry = HosterResolverRegistry(resolvers=[resolver])

        original_max = reg_mod._MAX_CACHE_SIZE
        try:
            reg_mod._MAX_CACHE_SIZE = 5
            for i in range(8):
                # Reset resolve mock to always return a result
                resolver.resolve = AsyncMock(
                    return_value=ResolvedStream(
                        video_url=f"https://cdn.example.com/v{i}.mp4"
                    )
                )
                await registry.resolve(f"https://voe.sx/e/{i:012d}")

            # Cache should be capped at 5
            assert len(registry._result_cache) == 5
            # Oldest entries (0, 1, 2) should have been evicted
            assert f"https://voe.sx/e/{0:012d}" not in registry._result_cache
            # Newest entries should remain
            assert f"https://voe.sx/e/{7:012d}" in registry._result_cache
        finally:
            reg_mod._MAX_CACHE_SIZE = original_max

    @pytest.mark.asyncio
    async def test_name_match_takes_priority_over_domain_alias(self) -> None:
        """Resolver registered by name takes priority over domain alias map."""
        name_resolver = MagicMock()
        name_resolver.name = "filelions"
        name_resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://direct.filelions/v.mp4")
        )

        alias_resolver = MagicMock()
        alias_resolver.name = "vidhide"
        alias_resolver.supported_domains = frozenset({"vidhide", "filelions"})
        alias_resolver.resolve = AsyncMock(
            return_value=ResolvedStream(video_url="https://cdn.vidhide/v.mp4")
        )

        registry = HosterResolverRegistry(resolvers=[name_resolver, alias_resolver])
        result = await registry.resolve("https://filelions.live/v/abc123")

        assert result is not None
        assert result.video_url == "https://direct.filelions/v.mp4"
        name_resolver.resolve.assert_awaited_once()
        alias_resolver.resolve.assert_not_awaited()


def _mirror_cases() -> list[tuple[str, str]]:
    """(resolver name, mirror URL) pairs seen on the plugins' sites."""
    return [
        ("doodstream", "https://d0000d.com/e/38g63393lou4"),
        ("doodstream", "https://dood.to/e/w71gg51eat6x"),
        ("doodstream", "https://myvidplay.com/d/p5y6rtn9ib30"),
        ("doodstream", "https://playmogo.com/e/0dqejb4q9dt5"),
        ("streamtape", "https://streamta.pe/v/jjJpOzJkZdhmBm/x.mp4"),
        ("streamtape", "https://strtape.site/e/mkZxemvao2hb18G/x"),
        ("streamtape", "https://shavetape.cash/e/ZaoGYpO2o6tP8B/x"),
        ("streamtape", "https://tapeblocker.com/v/kWWLMpJLDzSOVb8/x"),
        ("streamtape", "https://streamtapeadblockuser.xyz/v/LQwLWXdWWkHR2Ly/x"),
        ("vidguard", "https://vgembed.com/e/k3gG5qoLzDE1N2b"),
        ("vidguard", "https://vembed.net/e/l4vexv027oO8B9k"),
        ("strmup", "https://vidara.so/e/VGkJYYFohaboI"),
        ("strmup", "https://vidaraa.cc/e/7Ba8NtMpSl0Wv"),
        ("filemoon", "https://bysezejataos.com/d/pz97syzbv14q"),
        ("filemoon", "https://filemooon.link/e/abc123def456"),
        ("filemoon", "https://byse.sx/e/abc123def456"),
        ("voe", "https://goofy-banana.com/e/ewoeevihnzun"),
        ("voe", "https://jeremyparticipantanything.com/e/ewoeevihnzun"),
        ("voe", "https://housecardsummerbutton.com/e/abc123def456"),
        ("ddownload", "https://ddl.to/abcdefghijkl"),
        ("serienstream", "https://serien.sx/redirect/123"),
    ]


class TestMirrorDomainDispatch:
    """Mirror domains reach their resolver without a plugin hint."""

    @pytest.fixture
    def registry(self) -> HosterResolverRegistry:
        from scavengarr.infrastructure.hoster_resolvers.ddownload import (
            DDownloadResolver,
        )
        from scavengarr.infrastructure.hoster_resolvers.doodstream import (
            DoodStreamResolver,
        )
        from scavengarr.infrastructure.hoster_resolvers.filemoon import (
            FilemoonResolver,
        )
        from scavengarr.infrastructure.hoster_resolvers.serienstream import (
            SerienstreamResolver,
        )
        from scavengarr.infrastructure.hoster_resolvers.streamtape import (
            StreamtapeResolver,
        )
        from scavengarr.infrastructure.hoster_resolvers.strmup import StrmupResolver
        from scavengarr.infrastructure.hoster_resolvers.vidguard import (
            VidguardResolver,
        )
        from scavengarr.infrastructure.hoster_resolvers.voe import VoeResolver

        client = MagicMock(spec=httpx.AsyncClient)
        resolvers = [
            cls(http_client=client)
            for cls in (
                DDownloadResolver,
                DoodStreamResolver,
                FilemoonResolver,
                SerienstreamResolver,
                StreamtapeResolver,
                StrmupResolver,
                VidguardResolver,
                VoeResolver,
            )
        ]
        for resolver in resolvers:
            resolver.resolve = AsyncMock(  # type: ignore[method-assign]
                return_value=ResolvedStream(video_url=f"https://{resolver.name}/v")
            )
        return HosterResolverRegistry(resolvers=resolvers)

    @pytest.mark.parametrize(("name", "url"), _mirror_cases())
    async def test_mirror_dispatches_to_resolver(
        self, registry: HosterResolverRegistry, name: str, url: str
    ) -> None:
        assert extract_domain(url) in registry.supported_domains

        result = await registry.resolve(url)

        assert result is not None
        assert result.video_url == f"https://{name}/v"

    async def test_url_whitespace_is_stripped(
        self, registry: HosterResolverRegistry
    ) -> None:
        """Scraped links sometimes carry a trailing newline."""
        await registry.resolve("https://streamtape.com/v/8vwOLp7aApUodrP\n")

        resolver = registry._resolvers["streamtape"]
        resolver.resolve.assert_awaited_once_with(  # type: ignore[attr-defined]
            "https://streamtape.com/v/8vwOLp7aApUodrP"
        )


async def _hang(url: str) -> ResolvedStream | None:
    await asyncio.sleep(10)
    return None


async def _cut(resolving: Awaitable[ResolvedStream | None], *, after: float) -> None:
    """Cancel a resolution after *after* seconds, as the Stremio deadline does."""
    task = asyncio.ensure_future(resolving)
    await asyncio.sleep(after)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class TestCircuitBreaker:
    """A hoster that never delivers from here cost every request its browser
    capture: in production (VPN IP, 2026-10-04) DoodStream's Turnstile and
    Dropload's captcha player gave no stream from 50 captures in an hour,
    about 10 s of Chromium CPU per stream request."""

    _MP4 = "https://cdn.example.com/v.mp4"

    @staticmethod
    def _registry(
        resolve: Callable[[str], Awaitable[ResolvedStream | None]],
        *,
        resolve_timeout: float = 0.05,
        failure_threshold: int = 1,
        cooldown_seconds: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> tuple[HosterResolverRegistry, MagicMock]:
        resolver = MagicMock()
        resolver.name = "doodstream"
        resolver.resolve = AsyncMock(side_effect=resolve)
        registry = HosterResolverRegistry(
            resolvers=[resolver],
            http_client=http_client,
            resolve_timeout=resolve_timeout,
            verify_playback=http_client is not None,
            circuit_breaker=PluginCircuitBreaker(
                failure_threshold=failure_threshold, cooldown_seconds=cooldown_seconds
            ),
        )
        return registry, resolver

    @classmethod
    def _answers(
        cls, *answers: str
    ) -> Callable[[str], Awaitable[ResolvedStream | None]]:
        """Resolver answering "hang", "stream", "slow" (a stream after 0.1 s)
        or "dead", one per call."""
        queue = list(answers)

        async def _resolve(url: str) -> ResolvedStream | None:
            answer = queue.pop(0)
            if answer == "hang":
                await asyncio.sleep(10)
            if answer == "slow":
                await asyncio.sleep(0.1)
            return (
                ResolvedStream(video_url=cls._MP4)
                if answer in ("stream", "slow")
                else None
            )

        return _resolve

    async def test_a_timeout_counts(self) -> None:
        registry, resolver = self._registry(_hang)

        assert await registry.resolve("https://doodstream.com/e/a") is None
        with structlog.testing.capture_logs() as logs:
            assert await registry.resolve("https://doodstream.com/e/b") is None

        assert resolver.resolve.await_count == 1
        assert any(
            e["event"] == "hoster_resolve_circuit_open" and e["hoster"] == "doodstream"
            for e in logs
        )

    async def test_a_late_cut_does_not_count(self) -> None:
        """A cut says nothing about the hoster: most come from the answer
        going out once enough other hosters have a video, and the timed
        window includes the wait for a browser page. Five such cuts opened
        the breakers of healthy hosters (code review, 2026-10-06)."""
        registry, resolver = self._registry(
            self._answers("hang", "stream"), resolve_timeout=0.2
        )

        await _cut(registry.resolve("https://doodstream.com/e/a"), after=0.15)

        assert await registry.resolve("https://doodstream.com/e/b") is not None
        assert resolver.resolve.await_count == 2

    async def test_an_early_cut_does_not_count(self) -> None:
        """Cut by the resolve grace or early stop: other hosters were faster."""
        registry, _ = self._registry(self._answers("hang", "stream"), resolve_timeout=1)

        await _cut(registry.resolve("https://doodstream.com/e/a"), after=0.02)

        assert await registry.resolve("https://doodstream.com/e/b") is not None

    async def test_a_stream_resets_it(self) -> None:
        registry, resolver = self._registry(
            self._answers("hang", "stream", "hang", "stream"), failure_threshold=2
        )

        for link in "abcd":
            await registry.resolve(f"https://doodstream.com/e/{link}")

        assert resolver.resolve.await_count == 4

    async def test_a_dead_link_neither_counts_nor_resets_it(self) -> None:
        """A file the hoster deleted says nothing about the hoster."""
        registry, resolver = self._registry(
            self._answers("hang", "dead", "hang", "stream"), failure_threshold=2
        )

        for link in "abcd":
            await registry.resolve(f"https://doodstream.com/e/{link}")

        assert resolver.resolve.await_count == 3

    @respx.mock
    async def test_an_unplayable_stream_counts(self) -> None:
        respx.get(self._MP4).respond(502)
        async with httpx.AsyncClient() as client:
            registry, resolver = self._registry(
                self._answers("stream", "stream"), http_client=client
            )

            assert await registry.resolve("https://doodstream.com/e/a") is None
            assert await registry.resolve("https://doodstream.com/e/b") is None

        assert resolver.resolve.await_count == 1

    async def test_a_skipped_link_is_not_cached_as_dead(self) -> None:
        registry, _ = self._registry(self._answers("hang", "stream"))
        await registry.resolve("https://doodstream.com/e/a")
        assert await registry.resolve("https://doodstream.com/e/b") is None

        registry._circuit_breaker.reset("doodstream")  # type: ignore[union-attr]

        assert await registry.resolve("https://doodstream.com/e/b") is not None

    @staticmethod
    async def _half_open(registry: HosterResolverRegistry) -> PluginCircuitBreaker:
        """Open the breaker and let its cooldown run out: the next call probes."""
        breaker = registry._circuit_breaker
        assert breaker is not None
        breaker.record_failure("doodstream")
        await asyncio.sleep(0.03)
        return breaker

    async def test_a_cut_probe_runs_on_and_closes_the_breaker(self) -> None:
        """Filemoon's half-open probe was cut by the resolve grace before it
        could report, so the breaker probed again after every cooldown,
        which never doubled (production, 2026-10-05)."""
        registry, resolver = self._registry(
            self._answers("slow"), resolve_timeout=1, cooldown_seconds=0.02
        )
        breaker = await self._half_open(registry)

        await _cut(registry.resolve("https://doodstream.com/e/a"), after=0.02)
        await asyncio.sleep(0.15)

        assert breaker.is_closed("doodstream")
        # The probe's stream is cached for the next request
        assert await registry.resolve("https://doodstream.com/e/a") is not None
        assert resolver.resolve.await_count == 1

    @pytest.mark.parametrize("verdictless", ["dead", "network_error", "http_error"])
    async def test_a_probe_without_a_verdict_lets_the_next_link_probe(
        self, verdictless: str
    ) -> None:
        """A deleted file or a failed request says nothing about the hoster;
        the probe kept its slot, and the alive links after it were refused
        for a whole cooldown, up to an hour (code review, 2026-10-06)."""
        queue = [verdictless, "stream"]

        async def _resolve(url: str) -> ResolvedStream | None:
            answer = queue.pop(0)
            if answer == "network_error":
                raise httpx.ConnectError("reset")
            if answer == "http_error":
                raise httpx.DecodingError("garbled")
            return ResolvedStream(video_url=self._MP4) if answer == "stream" else None

        registry, resolver = self._registry(
            _resolve, resolve_timeout=1, cooldown_seconds=0.05
        )
        breaker = registry._circuit_breaker
        assert breaker is not None
        breaker.record_failure("doodstream")
        await asyncio.sleep(0.06)

        assert await registry.resolve("https://doodstream.com/e/a") is None
        assert await registry.resolve("https://doodstream.com/e/b") is not None
        assert resolver.resolve.await_count == 2
        assert breaker.is_closed("doodstream")

    async def test_a_cut_probe_that_fails_doubles_the_cooldown(self) -> None:
        registry, _ = self._registry(_hang, resolve_timeout=0.1, cooldown_seconds=0.02)
        breaker = await self._half_open(registry)

        await _cut(registry.resolve("https://doodstream.com/e/a"), after=0.02)
        await asyncio.sleep(0.15)

        assert breaker.state("doodstream") == "open"
        assert breaker._cooldowns["doodstream"] == pytest.approx(0.04)

    async def test_aclose_ends_a_running_probe(self) -> None:
        cancelled: list[str] = []

        async def _hang_until_cancelled(url: str) -> ResolvedStream | None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(url)
                raise
            return None

        registry, _ = self._registry(
            _hang_until_cancelled, resolve_timeout=5, cooldown_seconds=0.02
        )
        await self._half_open(registry)
        await _cut(registry.resolve("https://doodstream.com/e/a"), after=0.02)
        assert not cancelled

        await registry.aclose()

        assert cancelled == ["https://doodstream.com/e/a"]

    async def test_a_resolution_with_the_breaker_closed_ends_with_its_request(
        self,
    ) -> None:
        cancelled: list[str] = []

        async def _hang_until_cancelled(url: str) -> ResolvedStream | None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(url)
                raise
            return None

        registry, _ = self._registry(_hang_until_cancelled, resolve_timeout=5)

        await _cut(registry.resolve("https://doodstream.com/e/a"), after=0.02)

        assert cancelled == ["https://doodstream.com/e/a"]


class TestMoflixStreamHosts:
    """moflix hands out two players under one second-level name:
    moflix-stream.click is VidHide (EarnVids), moflix-stream.link runs
    Filemoon's Byse player ("Byse Frontend"), which VidHide cannot read."""

    @pytest.mark.parametrize(
        ("url", "name"),
        [
            ("https://moflix-stream.link/e/olh8wkp6ejd0", "filemoon"),
            ("https://moflix-stream.click/embed/kulz2q4qc0fl", "vidhide"),
        ],
    )
    async def test_each_host_goes_to_its_player(self, url: str, name: str) -> None:
        from scavengarr.infrastructure.hoster_resolvers.filemoon import (
            FilemoonResolver,
        )
        from scavengarr.infrastructure.hoster_resolvers.xfs import (
            create_all_xfs_resolvers,
        )

        client = MagicMock(spec=httpx.AsyncClient)
        resolvers = [
            FilemoonResolver(http_client=client),
            *create_all_xfs_resolvers(http_client=client),
        ]
        for resolver in resolvers:
            resolver.resolve = AsyncMock(  # type: ignore[method-assign]
                return_value=ResolvedStream(video_url=f"https://{resolver.name}/v")
            )
        registry = HosterResolverRegistry(resolvers=resolvers)

        result = await registry.resolve(url)

        assert result is not None
        assert result.video_url == f"https://{name}/v"


class TestTelemetry:
    """Every resolution is recorded in the registry, labeled by resolver."""

    _URL = "https://doodstream.com/e/a"
    _MP4 = "https://cdn.example.com/v.mp4"

    @staticmethod
    def _registry(
        resolve: Callable[[str], Awaitable[ResolvedStream | None]],
        *,
        http_client: httpx.AsyncClient | None = None,
        failure_threshold: int = 5,
    ) -> tuple[HosterResolverRegistry, Telemetry]:
        resolver = MagicMock()
        resolver.name = "doodstream"
        resolver.resolve = AsyncMock(side_effect=resolve)
        telemetry = Telemetry()
        registry = HosterResolverRegistry(
            resolvers=[resolver],
            http_client=http_client,
            resolve_timeout=0.05,
            verify_playback=http_client is not None,
            circuit_breaker=PluginCircuitBreaker(failure_threshold=failure_threshold),
            telemetry=telemetry,
        )
        return registry, telemetry

    @staticmethod
    def _outcome(
        t: Telemetry, outcome: str, resolver: str = "doodstream"
    ) -> float | None:
        return t.registry.get_sample_value(
            "scavengarr_hoster_resolve_total",
            {"resolver": resolver, "outcome": outcome},
        )

    @staticmethod
    def _timed(t: Telemetry, resolver: str = "doodstream") -> float | None:
        return t.registry.get_sample_value(
            "scavengarr_hoster_resolve_seconds_count", {"resolver": resolver}
        )

    @staticmethod
    def _raising(error: Exception) -> Callable[[str], Awaitable[ResolvedStream | None]]:
        async def _resolve(url: str) -> ResolvedStream | None:
            raise error

        return _resolve

    async def test_stream(self) -> None:
        async def _stream(url: str) -> ResolvedStream | None:
            return ResolvedStream(video_url=self._MP4)

        registry, t = self._registry(_stream)

        await registry.resolve(self._URL)

        assert self._outcome(t, "stream") == 1
        assert self._timed(t) == 1

    async def test_dead_link(self) -> None:
        async def _dead(url: str) -> ResolvedStream | None:
            return None

        registry, t = self._registry(_dead)

        await registry.resolve(self._URL)

        assert self._outcome(t, "dead") == 1

    @respx.mock
    async def test_unplayable_stream(self) -> None:
        respx.get(self._MP4).respond(502)

        async def _stream(url: str) -> ResolvedStream | None:
            return ResolvedStream(video_url=self._MP4)

        async with httpx.AsyncClient() as client:
            registry, t = self._registry(_stream, http_client=client)
            await registry.resolve(self._URL)

        assert self._outcome(t, "unplayable") == 1

    async def test_timeout(self) -> None:
        registry, t = self._registry(_hang)

        await registry.resolve(self._URL)

        assert self._outcome(t, "timeout") == 1
        assert self._timed(t) == 1

    @pytest.mark.parametrize(
        ("error", "outcome"),
        [
            (httpx.ConnectError("refused"), "network_error"),
            (
                httpx.HTTPStatusError(
                    "500",
                    request=httpx.Request("GET", "https://doodstream.com/e/a"),
                    response=httpx.Response(500),
                ),
                "http_error",
            ),
            (RuntimeError("parser broke"), "error"),
        ],
    )
    async def test_errors(self, error: Exception, outcome: str) -> None:
        registry, t = self._registry(self._raising(error))

        assert await registry.resolve(self._URL) is None

        assert self._outcome(t, outcome) == 1

    async def test_cut(self) -> None:
        registry, t = self._registry(_hang)
        registry._resolve_timeout = 10.0

        await _cut(registry.resolve(self._URL), after=0.02)

        assert self._outcome(t, "cut") == 1
        assert self._timed(t) == 1

    async def test_cached_resolution_counts_for_its_resolver(self) -> None:
        async def _stream(url: str) -> ResolvedStream | None:
            return ResolvedStream(video_url=self._MP4)

        registry, t = self._registry(_stream)

        await registry.resolve(self._URL)
        await registry.resolve(self._URL)

        assert self._outcome(t, "cached") == 1
        assert self._timed(t) == 1

    async def test_open_breaker(self) -> None:
        registry, t = self._registry(_hang, failure_threshold=1)

        await registry.resolve(self._URL)
        await registry.resolve("https://doodstream.com/e/b")

        assert self._outcome(t, "breaker_open") == 1

    @pytest.mark.parametrize(
        ("content_type", "outcome"),
        [("application/vnd.apple.mpegurl", "stream"), ("text/html", "dead")],
    )
    @respx.mock
    async def test_a_url_without_resolver_is_probed_as_direct(
        self, content_type: str, outcome: str
    ) -> None:
        respx.head("https://cdn.example.com/master.m3u8").respond(
            200, headers={"content-type": content_type}
        )
        telemetry = Telemetry()
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(http_client=client, telemetry=telemetry)

            await registry.resolve("https://cdn.example.com/master.m3u8")
            await registry.resolve("https://cdn.example.com/master.m3u8")

        assert self._outcome(telemetry, outcome, resolver="direct") == 1
        assert self._outcome(telemetry, "cached", resolver="direct") == 1


class _PlayerBound:
    """A hoster whose CDN binds video URLs to the player's headers (VEEV)."""

    name = "veev"
    supported_domains = frozenset({"veev"})
    bound_headers = ("user-agent", "accept-language")

    def __init__(self) -> None:
        self.plain_calls = 0
        self.player_calls: list[tuple[str, dict[str, str]]] = []

    async def resolve(self, url: str) -> ResolvedStream | None:
        self.plain_calls += 1
        return ResolvedStream(video_url="https://cdn.example/shared.mp4")

    async def resolve_for_client(
        self, url: str, headers: dict[str, str]
    ) -> ResolvedStream | None:
        self.player_calls.append((url, dict(headers)))
        return ResolvedStream(video_url="https://cdn.example/player.mp4")


class TestClientBoundResolution:
    """veevcdn binds the video URL to the player's User-Agent and
    Accept-Language (production, 2026-10-06)."""

    _URL = "https://veev.to/e/abcdefghijkl"
    _PLAYER = {"user-agent": "Firefox", "accept-language": "de"}

    @staticmethod
    def _voe() -> SimpleNamespace:
        return SimpleNamespace(
            name="voe",
            supported_domains=frozenset({"voe"}),
            resolve=AsyncMock(return_value=ResolvedStream(video_url="https://c/v.mp4")),
        )

    def test_names_the_headers_a_hosters_cdn_binds(self) -> None:
        registry = HosterResolverRegistry(resolvers=[_PlayerBound()])

        assert registry.bound_headers(self._URL) == ("user-agent", "accept-language")

    def test_other_hosters_bind_no_headers(self) -> None:
        registry = HosterResolverRegistry(resolvers=[self._voe()])

        assert registry.bound_headers("https://voe.sx/e/abc") == ()
        assert registry.bound_headers("https://unknown.example/e/abc") == ()

    async def test_resolves_for_the_player_past_the_shared_cache(self) -> None:
        resolver = _PlayerBound()
        registry = HosterResolverRegistry(resolvers=[resolver])
        await registry.resolve(self._URL)

        stream = await registry.resolve_for_client(self._URL, "veev", self._PLAYER)
        shared = await registry.resolve(self._URL)

        assert stream is not None
        assert stream.video_url == "https://cdn.example/player.mp4"
        assert resolver.player_calls == [(self._URL, self._PLAYER)]
        assert shared is not None
        assert shared.video_url == "https://cdn.example/shared.mp4"
        assert resolver.plain_calls == 1

    async def test_a_hoster_binding_no_headers_gives_none(self) -> None:
        voe = self._voe()
        registry = HosterResolverRegistry(resolvers=[voe])

        stream = await registry.resolve_for_client(
            "https://voe.sx/e/abc", "voe", self._PLAYER
        )

        assert stream is None
        voe.resolve.assert_not_awaited()

    async def test_an_open_breaker_skips_the_player_resolution(self) -> None:
        resolver = _PlayerBound()
        breaker = PluginCircuitBreaker(failure_threshold=1, cooldown_seconds=60)
        breaker.record_failure("veev")
        registry = HosterResolverRegistry(resolvers=[resolver], circuit_breaker=breaker)

        assert (
            await registry.resolve_for_client(self._URL, "veev", self._PLAYER) is None
        )
        assert resolver.player_calls == []


class TestBusyBrowser:
    """The stealth browser's pages are shared: a capture that waits for one
    says nothing about its hoster."""

    _MP4 = "https://cdn.example.com/v.mp4"

    async def test_busy_is_neither_cached_nor_counted(self) -> None:
        calls = 0

        async def _busy_once(url: str) -> ResolvedStream | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PageBusy
            return ResolvedStream(video_url=self._MP4)

        resolver = MagicMock()
        resolver.name = "filemoon"
        resolver.resolve = AsyncMock(side_effect=_busy_once)
        telemetry = Telemetry()
        registry = HosterResolverRegistry(
            resolvers=[resolver],
            circuit_breaker=PluginCircuitBreaker(failure_threshold=1),
            telemetry=telemetry,
        )

        assert await registry.resolve("https://filemoon.sx/e/a") is None
        result = await registry.resolve("https://filemoon.sx/e/a")

        assert result is not None
        assert result.video_url == self._MP4
        assert (
            telemetry.registry.get_sample_value(
                "scavengarr_hoster_resolve_total",
                {"resolver": "filemoon", "outcome": "busy"},
            )
            == 1
        )

    async def test_the_wait_for_a_page_is_not_part_of_the_resolve_timeout(
        self,
    ) -> None:
        gate = PageGate(limit=1)
        release = asyncio.Event()

        async def _capture(url: str) -> ResolvedStream | None:
            async with gate.page("capture", timeout=15):
                return ResolvedStream(video_url=self._MP4)

        async def _hold() -> None:
            async with gate.page("plugin", timeout=30):
                await release.wait()

        resolver = MagicMock()
        resolver.name = "filemoon"
        resolver.resolve = AsyncMock(side_effect=_capture)
        registry = HosterResolverRegistry(resolvers=[resolver], resolve_timeout=0.05)
        holder = asyncio.create_task(_hold())
        await asyncio.sleep(0)
        resolving = asyncio.create_task(registry.resolve("https://filemoon.sx/e/a"))

        await asyncio.sleep(0.2)
        release.set()

        result = await resolving
        assert result is not None
        assert result.video_url == self._MP4
        await holder


class TestUnresolvedHosters:
    """Links no resolver claims are counted by hoster: which resolver to
    build next, and which plugin hands out such links."""

    @respx.mock
    async def test_probes_are_counted_and_logged_by_hoster(self) -> None:
        respx.head(url__regex=r"https://(byse|other)\.").respond(
            200, headers={"content-type": "text/html"}
        )
        respx.head("https://cdn.example.com/master.m3u8").respond(
            200, headers={"content-type": "application/vnd.apple.mpegurl"}
        )
        async with httpx.AsyncClient() as client:
            registry = HosterResolverRegistry(http_client=client)
            with structlog.testing.capture_logs() as logs:
                await registry.resolve("https://byse.sx/e/1")
                await registry.resolve("https://other.net/e/2")
                await registry.resolve("https://byse.sx/e/3")
                await registry.resolve("https://byse.sx/e/3")  # cached
                # A playlist needs no resolver (moflix hands out its own)
                await registry.resolve("https://cdn.example.com/master.m3u8")

        assert registry.unresolved_hosts() == {"byse": 2, "other": 1}
        events = [e for e in logs if e["event"] == "hoster_without_resolver"]
        assert events == [
            {"event": "hoster_without_resolver", "log_level": "info", "hoster": h}
            for h in ("byse", "other", "byse")
        ]

    async def test_most_frequent_first_and_at_most_twenty(self) -> None:
        registry = HosterResolverRegistry()
        for i in range(25):
            for link in range(i + 1):
                await registry.resolve(f"https://hoster{i}.net/e/{link}")

        top = registry.unresolved_hosts()

        assert list(top) == [f"hoster{i}" for i in range(24, 4, -1)]
        assert top["hoster24"] == 25

    async def test_new_hosters_stop_counting_at_the_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry_module, "_MAX_UNRESOLVED", 2)
        registry = HosterResolverRegistry()
        for url in ("https://a.net/1", "https://b.net/1", "https://c.net/1"):
            await registry.resolve(url)
        await registry.resolve("https://a.net/2")

        assert registry.unresolved_hosts() == {"a": 2, "b": 1}


def _stub_resolver(name: str, result: ResolvedStream | None) -> MagicMock:
    resolver = MagicMock()
    resolver.name = name
    resolver.resolve = AsyncMock(return_value=result)
    return resolver


def _left(registry: HosterResolverRegistry, url: str) -> float:
    """Seconds until *url*'s cached resolution expires."""
    return registry._result_cache[url].expires_at - time.monotonic()


class TestStateExport:
    """Resolutions and redirects outlive a restart (HosterStateStore):
    exported with their lifetime left, restored with it shortened by the
    downtime."""

    _STREAM = ResolvedStream(
        video_url="https://cdn.example.com/hls/master.m3u8",
        headers={"Referer": "https://voe.sx/"},
        is_hls=True,
        quality=StreamQuality.HD_1080P,
        size_bytes=1_500_000_000,
    )

    async def test_alive_and_dead_entries_round_trip(self) -> None:
        old = HosterResolverRegistry(
            resolvers=[
                _stub_resolver("voe", self._STREAM),
                _stub_resolver("streamtape", None),
            ]
        )
        await old.resolve("https://voe.sx/e/alive")
        await old.resolve("https://streamtape.com/e/dead")

        new = HosterResolverRegistry()
        assert new.import_state(old.export_state(), age_s=600.0) == (2, 0)

        alive = new.cached("https://voe.sx/e/alive")
        assert alive == old.cached("https://voe.sx/e/alive")
        assert alive[1] is not None
        assert alive[1].quality is StreamQuality.HD_1080P
        assert new.cached("https://streamtape.com/e/dead") == (True, None)
        assert 2990 < _left(new, "https://voe.sx/e/alive") <= 3000
        assert 290 < _left(new, "https://streamtape.com/e/dead") <= 300
        assert new._result_cache["https://voe.sx/e/alive"].resolver == "voe"

    async def test_the_snapshot_holds_builtins_only(self) -> None:
        """No class in the pickle: moving one cannot make it unreadable."""
        registry = HosterResolverRegistry(
            resolvers=[_stub_resolver("voe", self._STREAM)]
        )
        await registry.resolve("https://voe.sx/e/alive")

        stream = registry.export_state()["results"][0]["stream"]

        assert type(stream["quality"]) is int
        assert {type(v) for v in stream.values()} <= {str, bool, int, float, dict}

    async def test_redirects_round_trip(self) -> None:
        response = MagicMock()
        response.url = "https://voe.sx/e/abc"
        old_client = AsyncMock(spec=httpx.AsyncClient)
        old_client.head = AsyncMock(return_value=response)
        old = HosterResolverRegistry(
            resolvers=[_stub_resolver("voe", self._STREAM)], http_client=old_client
        )
        await old.resolve("https://out.example.net/go/1")

        new_client = AsyncMock(spec=httpx.AsyncClient)
        new = HosterResolverRegistry(
            resolvers=[_stub_resolver("voe", self._STREAM)], http_client=new_client
        )
        assert new.import_state(old.export_state(), age_s=0.0) == (1, 1)
        await new.resolve("https://out.example.net/go/1", refresh=True)

        new_client.head.assert_not_awaited()

    def test_entries_that_ran_out_while_down_stay_out(self) -> None:
        state = {
            "results": [
                {
                    "url": "https://voe.sx/e/dead",
                    "stream": None,
                    "remaining": 120.0,
                    "resolver": "voe",
                }
            ],
            "redirects": [
                {
                    "url": "https://out.example.net/go/1",
                    "target": "https://voe.sx/e/abc",
                    "remaining": 30.0,
                }
            ],
        }
        registry = HosterResolverRegistry()

        assert registry.import_state(state, age_s=600.0) == (0, 0)
        assert registry.cached("https://voe.sx/e/dead") == (False, None)

    async def test_expired_entries_are_not_exported(self) -> None:
        registry = HosterResolverRegistry(resolvers=[_stub_resolver("voe", None)])
        await registry.resolve("https://voe.sx/e/dead")
        registry._result_cache["https://voe.sx/e/dead"].expires_at = time.monotonic()

        assert registry.export_state() == {"results": [], "redirects": []}

    def test_other_stream_fields_of_another_version(self) -> None:
        """A field the code no longer has is dropped, a new one takes its
        default."""
        state = {
            "results": [
                {
                    "url": "https://voe.sx/e/abc",
                    "stream": {
                        "video_url": "https://cdn.example.com/v.mp4",
                        "codec": 1,
                    },
                    "remaining": 60.0,
                    "resolver": "voe",
                }
            ],
            "redirects": [],
        }
        registry = HosterResolverRegistry()
        registry.import_state(state, age_s=0.0)

        assert registry.cached("https://voe.sx/e/abc") == (
            True,
            ResolvedStream(video_url="https://cdn.example.com/v.mp4"),
        )

    def test_a_malformed_entry_changes_nothing(self) -> None:
        good = {"url": "https://voe.sx/e/a", "stream": None, "remaining": 60.0}
        state = {"results": [{**good, "resolver": "voe"}, good], "redirects": []}
        registry = HosterResolverRegistry()

        with pytest.raises(KeyError):
            registry.import_state(state, age_s=0.0)
        assert registry._result_cache == {}

    def test_the_cap_holds_after_an_import(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(registry_module, "_MAX_CACHE_SIZE", 3)
        state = {
            "results": [
                {
                    "url": f"https://voe.sx/e/{i}",
                    "stream": None,
                    "remaining": 600.0,
                    "resolver": "voe",
                }
                for i in range(5)
            ],
            "redirects": [],
        }
        registry = HosterResolverRegistry()
        registry.import_state(state, age_s=0.0)

        assert list(registry._result_cache) == [
            f"https://voe.sx/e/{i}" for i in (2, 3, 4)
        ]

    async def test_caching_counts_as_a_change(self) -> None:
        registry = HosterResolverRegistry(resolvers=[_stub_resolver("voe", None)])
        assert registry.changes == 0

        await registry.resolve("https://voe.sx/e/dead")
        assert registry.changes == 1
        await registry.resolve("https://voe.sx/e/dead")  # a cache hit
        assert registry.changes == 1
