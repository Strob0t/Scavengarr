"""Tests for SerienstreamResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.serienstream import (
    SerienstreamResolver,
    _extract_file_id,
)


class TestExtractFileId:
    def test_s_to(self) -> None:
        assert (
            _extract_file_id("https://s.to/serie/stream/breaking-bad")
            == "stream/breaking-bad"
        )

    def test_serienstream_to(self) -> None:
        url = "https://serienstream.to/serie/stream/breaking-bad"
        assert _extract_file_id(url) == "stream/breaking-bad"

    def test_serien_sx(self) -> None:
        url = "https://serien.sx/serie/stream/breaking-bad"
        assert _extract_file_id(url) == "stream/breaking-bad"

    def test_www_s_to(self) -> None:
        assert (
            _extract_file_id("https://www.s.to/serie/stream/breaking-bad")
            == "stream/breaking-bad"
        )

    def test_http_scheme(self) -> None:
        assert (
            _extract_file_id("http://s.to/serie/stream/breaking-bad")
            == "stream/breaking-bad"
        )

    def test_non_matching_domain(self) -> None:
        assert _extract_file_id("https://example.com/serie/stream/breaking-bad") is None

    def test_wrong_path(self) -> None:
        assert _extract_file_id("https://s.to/movie/breaking-bad") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


_VALID_PAGE = "<html><body><h1>Breaking Bad</h1><div>Streams</div></body></html>"
_OFFLINE_PAGE = "<html><body>Seite nicht gefunden</body></html>"


class TestSerienstreamResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        assert SerienstreamResolver(http_client=client).name == "serienstream"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _VALID_PAGE
        mock_resp.url = "https://s.to/serie/stream/breaking-bad"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        url = "https://s.to/serie/stream/breaking-bad"
        result = await SerienstreamResolver(http_client=client).resolve(url)
        assert result is not None
        assert result.video_url == url

    @pytest.mark.asyncio
    async def test_returns_none_for_offline(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _OFFLINE_PAGE
        mock_resp.url = "https://s.to/serie/stream/breaking-bad"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        result = await SerienstreamResolver(http_client=client).resolve(
            "https://s.to/serie/stream/breaking-bad"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        result = await SerienstreamResolver(http_client=client).resolve(
            "https://s.to/serie/stream/breaking-bad"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))
        result = await SerienstreamResolver(http_client=client).resolve(
            "https://s.to/serie/stream/breaking-bad"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        result = await SerienstreamResolver(http_client=client).resolve(
            "https://example.com/serie/stream/breaking-bad"
        )
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_none_on_error_redirect(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Error</body></html>"
        mock_resp.url = "https://s.to/404"
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)
        result = await SerienstreamResolver(http_client=client).resolve(
            "https://s.to/serie/stream/breaking-bad"
        )
        assert result is None
