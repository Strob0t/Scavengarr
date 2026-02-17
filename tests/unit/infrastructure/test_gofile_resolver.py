"""Tests for GoFileResolver."""

from __future__ import annotations

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
            with patch.object(gofile, "_cached_token_ts", 0.0):
                await resolver.resolve(url)

        assert token_route.call_count == 2

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
