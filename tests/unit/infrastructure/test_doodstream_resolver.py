"""Tests for DoodStreamResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.doodstream import (
    DoodStreamResolver,
)


def _make_embed_html(
    *,
    pass_md5: str = "/pass_md5/abc123/def456",
    token: str = "a1b2c3d4e5",
    captcha: bool = False,
    offline: bool = False,
) -> str:
    """Build a minimal DoodStream embed page."""
    parts = ["<html><head><title>DoodStream</title></head><body>"]
    if offline:
        parts.append("<h1> Oops! Sorry </h1>")
    if captcha:
        parts.append('<div data-sitekey="6Lc..."></div>')
    parts.append(
        f"<script>var dsplayer = '/e/xyz'; "
        f"$.get('{pass_md5}', function(data)"
        "{ "
        f"var token = '&token={token}';"
        " });</script>"
    )
    parts.append(
        "minimalUserResponseInMiliseconds"  # prevents empty-embed offline detection
    )
    parts.append("</body></html>")
    return "\n".join(parts)


class TestDoodStreamResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = DoodStreamResolver(http_client=client)
        assert resolver.name == "doodstream"

    @pytest.mark.asyncio
    async def test_successful_extraction(self) -> None:
        html = _make_embed_html()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        pass_resp = MagicMock()
        pass_resp.status_code = 200
        pass_resp.text = "https://cv.dood.re/dl/abc123_video.mp4"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[mock_resp, pass_resp])

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")

        assert result is not None
        assert result.video_url.startswith("https://cv.dood.re/dl/abc123_video.mp4?")
        assert "token=a1b2c3d4e5" in result.video_url
        assert "expiry=" in result.video_url
        assert result.headers == {"Referer": "https://dood.re/e/xyz123"}

    @pytest.mark.asyncio
    async def test_converts_d_url_to_embed(self) -> None:
        html = _make_embed_html()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        pass_resp = MagicMock()
        pass_resp.status_code = 200
        pass_resp.text = "https://cv.dood.re/dl/video.mp4"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[mock_resp, pass_resp])

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/d/xyz123")

        assert result is not None
        # Verify the URL was normalized to /e/ format
        first_call_url = client.get.call_args_list[0][0][0]
        assert "/e/" in first_call_url

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("fail"))

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_offline_sorry(self) -> None:
        html = _make_embed_html(offline=True)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_video_not_found(self) -> None:
        html = "<html><title> Video not found | DoodStream</title></html>"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_captcha_required(self) -> None:
        html = _make_embed_html(captcha=True)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_turnstile_captcha(self) -> None:
        html = '<html><body><div class="cf-turnstile">challenge</div></body></html>'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_pass_md5(self) -> None:
        html = "<html><body><script>var player = 'something';</script></body></html>"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_token(self) -> None:
        html = (
            "<html><body><script>"
            "$.get('/pass_md5/abc/def', function(d){});"
            "</script></body></html>"
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_pass_md5_fails(self) -> None:
        html = _make_embed_html()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        pass_resp = MagicMock()
        pass_resp.status_code = 403

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[mock_resp, pass_resp])

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_pass_md5_network_error(self) -> None:
        html = _make_embed_html()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[mock_resp, httpx.ConnectError("fail")])

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_invalid_video_base(self) -> None:
        html = _make_embed_html()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        pass_resp = MagicMock()
        pass_resp.status_code = 200
        pass_resp.text = "not-a-url"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[mock_resp, pass_resp])

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_embed_iframe(self) -> None:
        html = '<html><body><iframe src="/e/"></iframe></body></html>'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None

    @pytest.mark.asyncio
    async def test_uses_response_domain_for_pass_url(self) -> None:
        """Verify pass_md5 URL uses the redirected domain, not original."""
        html = _make_embed_html()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://d0000d.com/e/xyz123"  # redirected domain

        pass_resp = MagicMock()
        pass_resp.status_code = 200
        pass_resp.text = "https://cv.d0000d.com/dl/video.mp4"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[mock_resp, pass_resp])

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")

        assert result is not None
        # Check that pass_md5 was called with redirected domain
        pass_call_url = client.get.call_args_list[1][0][0]
        assert "d0000d.com" in pass_call_url

    @pytest.mark.asyncio
    async def test_validate_captcha_response(self) -> None:
        html = (
            "<html><body>"
            '<form action="op=validate&gc_response=abc">submit</form>'
            "</body></html>"
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.url = "https://dood.re/e/xyz123"

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=mock_resp)

        resolver = DoodStreamResolver(http_client=client)
        result = await resolver.resolve("https://dood.re/e/xyz123")
        assert result is None
