"""Tests for GoFileResolver."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import httpx
import pytest
import respx

from scavengarr.infrastructure.hoster_resolvers import gofile
from scavengarr.infrastructure.hoster_resolvers.gofile import (
    GoFileResolver,
    _extract_content_id,
)

_TOKEN_URL = "https://api.gofile.io/accounts"
_CONTENT_URL = "https://api.gofile.io/contents/abc123"


@pytest.fixture(autouse=True)
def _reset_token_cache() -> None:
    """Reset module-level token cache before each test."""
    gofile._cached_token = None
    gofile._cached_token_ts = 0.0


class TestExtractContentId:
    def test_valid_url(self) -> None:
        assert _extract_content_id("https://gofile.io/d/abc123") == "abc123"

    def test_www_prefix(self) -> None:
        assert _extract_content_id("https://www.gofile.io/d/abc123") == "abc123"

    def test_http_scheme(self) -> None:
        assert _extract_content_id("http://gofile.io/d/abc123") == "abc123"

    def test_non_matching_domain(self) -> None:
        assert _extract_content_id("https://example.com/d/abc123") is None

    def test_empty_url(self) -> None:
        assert _extract_content_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_content_id("not-a-url") is None

    def test_no_content_id(self) -> None:
        assert _extract_content_id("https://gofile.io/") is None

    def test_wrong_path_prefix(self) -> None:
        assert _extract_content_id("https://gofile.io/f/abc123") is None


class TestGoFileResolver:
    def test_name(self) -> None:
        resolver = GoFileResolver(http_client=httpx.AsyncClient())
        assert resolver.name == "gofile"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_resolves_valid_content(self) -> None:
        url = "https://gofile.io/d/abc123"
        respx.post(_TOKEN_URL).respond(
            200,
            json={"status": "ok", "data": {"token": "testtoken123"}},
        )
        respx.get(_CONTENT_URL).respond(
            200,
            json={
                "status": "ok",
                "data": {
                    "type": "folder",
                    "children": {"file1": {"name": "movie.mkv"}},
                },
            },
        )

        async with httpx.AsyncClient() as client:
            result = await GoFileResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_not_found(self) -> None:
        url = "https://gofile.io/d/abc123"
        respx.post(_TOKEN_URL).respond(
            200,
            json={"status": "ok", "data": {"token": "testtoken123"}},
        )
        respx.get(_CONTENT_URL).respond(404)

        async with httpx.AsyncClient() as client:
            result = await GoFileResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_api_error(self) -> None:
        url = "https://gofile.io/d/abc123"
        respx.post(_TOKEN_URL).respond(
            200,
            json={"status": "ok", "data": {"token": "testtoken123"}},
        )
        respx.get(_CONTENT_URL).respond(
            200,
            json={"status": "error-notFound", "data": {}},
        )

        async with httpx.AsyncClient() as client:
            result = await GoFileResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_when_token_fails(self) -> None:
        url = "https://gofile.io/d/abc123"
        respx.post(_TOKEN_URL).respond(500)

        async with httpx.AsyncClient() as client:
            result = await GoFileResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_network_error(self) -> None:
        url = "https://gofile.io/d/abc123"
        respx.post(_TOKEN_URL).respond(
            200,
            json={"status": "ok", "data": {"token": "testtoken123"}},
        )
        respx.get(_CONTENT_URL).mock(side_effect=httpx.ConnectError("failed"))

        async with httpx.AsyncClient() as client:
            result = await GoFileResolver(http_client=client).resolve(url)
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_for_invalid_url(self) -> None:
        async with httpx.AsyncClient() as client:
            result = await GoFileResolver(http_client=client).resolve(
                "https://example.com/d/abc123",
            )
        assert result is None

    @respx.mock
    @pytest.mark.asyncio()
    async def test_reuses_cached_token(self) -> None:
        url = "https://gofile.io/d/abc123"
        token_route = respx.post(_TOKEN_URL).respond(
            200,
            json={"status": "ok", "data": {"token": "testtoken123"}},
        )
        respx.get(_CONTENT_URL).respond(
            200,
            json={"status": "ok", "data": {"type": "folder", "children": {}}},
        )

        async with httpx.AsyncClient() as client:
            resolver = GoFileResolver(http_client=client)
            await resolver.resolve(url)
            await resolver.resolve(url)

        # Token endpoint should only be called once
        assert token_route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio()
    async def test_refreshes_expired_token(self) -> None:
        url = "https://gofile.io/d/abc123"
        token_route = respx.post(_TOKEN_URL).respond(
            200,
            json={"status": "ok", "data": {"token": "testtoken123"}},
        )
        respx.get(_CONTENT_URL).respond(
            200,
            json={"status": "ok", "data": {"type": "folder", "children": {}}},
        )

        async with httpx.AsyncClient() as client:
            resolver = GoFileResolver(http_client=client)
            await resolver.resolve(url)

            # Expire the token
            # Relative to the monotonic clock: 0.0 is "fresh" on a host that
            # booted less than _TOKEN_TTL ago.
            expired_ts = time.monotonic() - gofile._TOKEN_TTL - 1
            with patch.object(gofile, "_cached_token_ts", expired_ts):
                await resolver.resolve(url)

        assert token_route.call_count == 2

    @respx.mock
    @pytest.mark.asyncio()
    async def test_renews_a_rejected_token_once(self) -> None:
        # GoFile can drop a guest token before our TTL ends:
        # 401 {"status": "error-wrongToken"} (JD2 GofileIo)
        url = "https://gofile.io/d/abc123"
        gofile._cached_token = "stale"
        gofile._cached_token_ts = time.monotonic()
        token_route = respx.post(_TOKEN_URL).respond(
            200,
            json={"status": "ok", "data": {"token": "fresh"}},
        )
        content_route = respx.get(_CONTENT_URL).mock(
            side_effect=lambda request: (
                httpx.Response(200, json={"status": "ok", "data": {}})
                if request.headers["Authorization"] == "Bearer fresh"
                else httpx.Response(
                    401, json={"status": "error-wrongToken", "data": {}}
                )
            )
        )

        async with httpx.AsyncClient() as client:
            result = await GoFileResolver(http_client=client).resolve(url)

        assert result is not None
        assert token_route.call_count == 1
        assert content_route.call_count == 2
        assert gofile._cached_token == "fresh"

    @respx.mock
    @pytest.mark.asyncio()
    async def test_gives_up_when_the_new_token_is_rejected_too(self) -> None:
        url = "https://gofile.io/d/abc123"
        token_route = respx.post(_TOKEN_URL).respond(
            200, json={"status": "ok", "data": {"token": "t"}}
        )
        content_route = respx.get(_CONTENT_URL).respond(
            401, json={"status": "error-wrongToken", "data": {}}
        )

        async with httpx.AsyncClient() as client:
            assert await GoFileResolver(http_client=client).resolve(url) is None

        assert token_route.call_count == 2
        assert content_route.call_count == 2

    @respx.mock
    @pytest.mark.asyncio()
    async def test_guest_access_refused_is_not_retried(self) -> None:
        # GoFile answers guest lookups with error-notPremium (2026-09); a
        # retry would only create another guest account, which GoFile
        # throttles with 429
        url = "https://gofile.io/d/abc123"
        token_route = respx.post(_TOKEN_URL).respond(
            200, json={"status": "ok", "data": {"token": "t"}}
        )
        content_route = respx.get(_CONTENT_URL).respond(
            401, json={"status": "error-notPremium", "data": {}}
        )

        async with httpx.AsyncClient() as client:
            assert await GoFileResolver(http_client=client).resolve(url) is None

        assert token_route.call_count == 1
        assert content_route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio()
    async def test_concurrent_resolves_share_one_guest_account(self) -> None:
        # GoFile throttles guest account creation (429 after two accounts)
        url = "https://gofile.io/d/abc123"

        async def _slow_token(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.01)  # let the other resolves run meanwhile
            return httpx.Response(200, json={"status": "ok", "data": {"token": "t"}})

        token_route = respx.post(_TOKEN_URL).mock(side_effect=_slow_token)
        respx.get(_CONTENT_URL).respond(200, json={"status": "ok", "data": {}})

        async with httpx.AsyncClient() as client:
            resolver = GoFileResolver(http_client=client)
            results = await asyncio.gather(*(resolver.resolve(url) for _ in range(5)))

        assert all(r is not None for r in results)
        assert token_route.call_count == 1

    @respx.mock
    @pytest.mark.asyncio()
    async def test_returns_none_on_token_api_error(self) -> None:
        url = "https://gofile.io/d/abc123"
        respx.post(_TOKEN_URL).respond(
            200,
            json={"status": "error", "data": {}},
        )

        async with httpx.AsyncClient() as client:
            result = await GoFileResolver(http_client=client).resolve(url)
        assert result is None
