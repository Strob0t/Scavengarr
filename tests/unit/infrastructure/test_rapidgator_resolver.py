"""Tests for RapidgatorResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.infrastructure.hoster_resolvers.rapidgator import (
    RapidgatorResolver,
    _extract_file_id,
    _extract_filename,
    _extract_filesize,
)

# ---------------------------------------------------------------------------
# File ID extraction
# ---------------------------------------------------------------------------


class TestExtractFileId:
    def test_hex_hash_id(self) -> None:
        url = "https://rapidgator.net/file/abc123def456abc123def456abc123de"
        assert _extract_file_id(url) == "abc123def456abc123def456abc123de"

    def test_numeric_id(self) -> None:
        assert _extract_file_id("https://rapidgator.net/file/12345678") == "12345678"

    def test_with_filename_suffix(self) -> None:
        url = "https://rapidgator.net/file/abc123def456abc123def456abc123de/Movie.mkv.html"
        assert _extract_file_id(url) == "abc123def456abc123def456abc123de"

    def test_rapidgator_asia(self) -> None:
        assert (
            _extract_file_id("https://rapidgator.asia/file/12345678") == "12345678"
        )

    def test_rg_to(self) -> None:
        assert _extract_file_id("https://rg.to/file/12345678") == "12345678"

    def test_www_prefix(self) -> None:
        assert (
            _extract_file_id("https://www.rapidgator.net/file/12345678") == "12345678"
        )

    def test_http_scheme(self) -> None:
        assert (
            _extract_file_id("http://rapidgator.net/file/12345678") == "12345678"
        )

    def test_non_rapidgator_domain(self) -> None:
        assert _extract_file_id("https://example.com/file/12345678") is None

    def test_no_path_match(self) -> None:
        assert _extract_file_id("https://rapidgator.net/folder/12345678") is None

    def test_empty_url(self) -> None:
        assert _extract_file_id("") is None

    def test_invalid_url(self) -> None:
        assert _extract_file_id("not-a-url") is None


# ---------------------------------------------------------------------------
# Filename / filesize extraction
# ---------------------------------------------------------------------------

_SAMPLE_HTML = """
<html>
<head><title>Download file Movie.2025.German.DL.1080p.BluRay.x264.mkv</title></head>
<body>
<div>
    Downloading: </strong> <a href="">Movie.2025.German.DL.1080p.BluRay.x264.mkv</a>
</div>
<div>
    File size: <strong>4.00 GB</strong>
</div>
</body>
</html>
"""


class TestExtractFilename:
    def test_from_downloading_label(self) -> None:
        assert (
            _extract_filename(_SAMPLE_HTML)
            == "Movie.2025.German.DL.1080p.BluRay.x264.mkv"
        )

    def test_fallback_to_title(self) -> None:
        html = "<title>Download file SomeFile.rar</title>"
        assert _extract_filename(html) == "SomeFile.rar"

    def test_empty_when_no_match(self) -> None:
        assert _extract_filename("<html><body>no match</body></html>") == ""


class TestExtractFilesize:
    def test_extracts_size(self) -> None:
        assert _extract_filesize(_SAMPLE_HTML) == "4.00 GB"

    def test_empty_when_no_match(self) -> None:
        assert _extract_filesize("<html><body>no match</body></html>") == ""


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------

_FILE_ID = "abc123def456abc123def456abc123de"
_FILE_URL = f"https://rapidgator.net/file/{_FILE_ID}"


def _make_response(
    *,
    status_code: int = 200,
    html: str = _SAMPLE_HTML,
    final_url: str = _FILE_URL,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = html
    resp.url = final_url
    return resp


class TestRapidgatorResolver:
    def test_name(self) -> None:
        client = MagicMock(spec=httpx.AsyncClient)
        resolver = RapidgatorResolver(http_client=client)
        assert resolver.name == "rapidgator"

    @pytest.mark.asyncio
    async def test_resolves_valid_file(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_make_response())

        resolver = RapidgatorResolver(http_client=client)
        result = await resolver.resolve(_FILE_URL)

        assert result is not None
        assert result.video_url == _FILE_URL

    @pytest.mark.asyncio
    async def test_calls_canonical_url(self) -> None:
        """Resolver always fetches the canonical rapidgator.net URL."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_make_response())

        resolver = RapidgatorResolver(http_client=client)
        # Pass rg.to URL — should still fetch canonical rapidgator.net URL.
        await resolver.resolve(f"https://rg.to/file/{_FILE_ID}")

        client.get.assert_called_once()
        call_url = client.get.call_args[0][0]
        assert call_url == _FILE_URL

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_make_response(status_code=404))

        resolver = RapidgatorResolver(http_client=client)
        result = await resolver.resolve(_FILE_URL)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_make_response(status_code=500))

        resolver = RapidgatorResolver(http_client=client)
        result = await resolver.resolve(_FILE_URL)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=httpx.ConnectError("failed"))

        resolver = RapidgatorResolver(http_client=client)
        result = await resolver.resolve(_FILE_URL)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_offline_html(self) -> None:
        """File page contains '404 File not found' despite HTTP 200."""
        html = "<html><body>> 404 File not found</body></html>"
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_make_response(html=html))

        resolver = RapidgatorResolver(http_client=client)
        result = await resolver.resolve(_FILE_URL)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_redirect_away(self) -> None:
        """Redirect to homepage means file is offline."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(
            return_value=_make_response(final_url="https://rapidgator.net/")
        )

        resolver = RapidgatorResolver(http_client=client)
        result = await resolver.resolve(_FILE_URL)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_url(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)

        resolver = RapidgatorResolver(http_client=client)
        result = await resolver.resolve("https://example.com/file/12345678")
        assert result is None
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_numeric_id(self) -> None:
        numeric_url = "https://rapidgator.net/file/99887766"
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(
            return_value=_make_response(final_url=numeric_url)
        )

        resolver = RapidgatorResolver(http_client=client)
        result = await resolver.resolve(numeric_url)

        assert result is not None
        assert result.video_url == numeric_url

    @pytest.mark.asyncio
    async def test_rapidgator_asia_url(self) -> None:
        """URLs from rapidgator.asia are normalised to rapidgator.net."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_make_response())

        resolver = RapidgatorResolver(http_client=client)
        result = await resolver.resolve(
            f"https://rapidgator.asia/file/{_FILE_ID}"
        )

        assert result is not None
        assert result.video_url == _FILE_URL
