"""Tests for StreamtapeResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.streamtape import (
    StreamtapeResolver,
    _is_streamtape_domain,
)


class TestIsStreamtapeDomain:
    def test_main_domain(self) -> None:
        assert _is_streamtape_domain("https://streamtape.com/v/abc") is True

    def test_alt_domain(self) -> None:
        assert _is_streamtape_domain("https://strtape.tech/e/abc") is True

    def test_non_streamtape(self) -> None:
        assert _is_streamtape_domain("https://voe.sx/e/abc") is False

    def test_tapecontent(self) -> None:
        assert _is_streamtape_domain("https://tapecontent.net/v/abc") is True


class TestStreamtapeResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = StreamtapeResolver(http_client=client)
        assert resolver.name == "streamtape"

    @pytest.mark.asyncio
    async def test_extracts_video_url(self) -> None:
        html = """
        <html>
        <script>
        var params = 'id=abc123&expires=1700000000&ip=1.2.3.4&token=AAAA-BBBB'</script>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://streamtape.com/v/abc123"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = StreamtapeResolver(http_client=client)
        result = await resolver.resolve("https://streamtape.com/v/abc123")

        assert result is not None
        assert "get_video" in result.video_url
        assert "id=abc123" in result.video_url
        assert "stream=1" in result.video_url

    @pytest.mark.asyncio
    async def test_corrects_token(self) -> None:
        html = """
        <html>
        <script>
        var x = 'id=abc&expires=1700000000&ip=1.2.3.4&token=WRONG-TOKEN'
        document.getElementById('something').innerHTML = '&token=CORRECT-TOKEN-123';
        </script>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://streamtape.com/v/abc"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = StreamtapeResolver(http_client=client)
        result = await resolver.resolve("https://streamtape.com/v/abc")

        assert result is not None
        assert "CORRECT-TOKEN-123" in result.video_url
        assert "WRONG-TOKEN" not in result.video_url

    @pytest.mark.asyncio
    async def test_returns_none_on_not_found(self) -> None:
        html = "<html><body><h1>Video not found</h1></body></html>"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://streamtape.com/v/abc"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = StreamtapeResolver(http_client=client)
        result = await resolver.resolve("https://streamtape.com/v/abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = StreamtapeResolver(http_client=client)
        result = await resolver.resolve("https://streamtape.com/v/abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = StreamtapeResolver(http_client=client)
        result = await resolver.resolve("https://streamtape.com/v/abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_params(self) -> None:
        html = "<html><body><p>No video params here</p></body></html>"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://streamtape.com/v/abc"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = StreamtapeResolver(http_client=client)
        result = await resolver.resolve("https://streamtape.com/v/abc")
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_response_domain(self) -> None:
        """Video URL should use the domain from the response, not hardcoded."""
        html = """
        <script>
        var x = 'id=abc&expires=1700000000&ip=1.2.3.4&token=TOK'</script>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://strtape.tech/v/abc"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = StreamtapeResolver(http_client=client)
        result = await resolver.resolve("https://strtape.tech/v/abc")

        assert result is not None
        assert "strtape.tech" in result.video_url
