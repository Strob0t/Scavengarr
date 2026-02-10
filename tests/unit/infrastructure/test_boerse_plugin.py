"""Tests for the boerse.sx Python plugin."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, patch

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "boerse.py"


def _load_boerse_module() -> ModuleType:
    """Load boerse.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("boerse_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once at module level for parser tests
_boerse = _load_boerse_module()
_BoersePlugin = _boerse.BoersePlugin
_LinkParser = _boerse._LinkParser
_ThreadLinkParser = _boerse._ThreadLinkParser
_hoster_from_url = _boerse._hoster_from_url


_TEST_CREDENTIALS = {
    "SCAVENGARR_BOERSE_USERNAME": "testuser",
    "SCAVENGARR_BOERSE_PASSWORD": "testpass",
}


def _make_plugin() -> object:
    """Create BoersePlugin instance."""
    return _BoersePlugin()


def _mock_response(
    status_code: int = 200,
    text: str = "",
) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("GET", "https://example.com"),
    )


class TestLogin:
    async def test_login_success(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)

        jar = httpx.Cookies()
        jar.set("bb_userid", "12345")
        mock_client.cookies = jar
        mock_client.post = AsyncMock(return_value=_mock_response(200))
        mock_client.aclose = AsyncMock()

        with (
            patch.dict(os.environ, _TEST_CREDENTIALS),
            patch.object(_boerse.httpx, "AsyncClient", return_value=mock_client),
        ):
            await plugin._ensure_session()

        assert plugin._logged_in is True
        mock_client.post.assert_awaited_once()

    async def test_login_domain_fallback(self) -> None:
        plugin = _make_plugin()

        call_count = 0

        async def _post_side_effect(url: str, **kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if "boerse.am" in url:
                raise httpx.ConnectError("unreachable")
            return _mock_response(200)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        jar = httpx.Cookies()
        jar.set("bb_userid", "12345")
        mock_client.cookies = jar
        mock_client.post = AsyncMock(side_effect=_post_side_effect)
        mock_client.aclose = AsyncMock()

        with (
            patch.dict(os.environ, _TEST_CREDENTIALS),
            patch.object(_boerse.httpx, "AsyncClient", return_value=mock_client),
        ):
            await plugin._ensure_session()

        assert plugin._logged_in is True
        assert plugin.base_url == "https://boerse.sx"
        assert call_count == 2

    async def test_login_all_domains_fail(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("unreachable"))
        mock_client.cookies = httpx.Cookies()
        mock_client.aclose = AsyncMock()

        with (
            patch.dict(os.environ, _TEST_CREDENTIALS),
            patch.object(_boerse.httpx, "AsyncClient", return_value=mock_client),
        ):
            with pytest.raises(RuntimeError, match="All boerse domains failed"):
                await plugin._ensure_session()

    async def test_missing_credentials_raises(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SCAVENGARR_BOERSE_USERNAME", None)
            os.environ.pop("SCAVENGARR_BOERSE_PASSWORD", None)
            plugin = _BoersePlugin()

        with pytest.raises(RuntimeError, match="Missing credentials"):
            await plugin._ensure_session()

    async def test_session_reuse(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client
        plugin._logged_in = True

        await plugin._ensure_session()
        mock_client.post.assert_not_awaited()


class TestSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()

        search_html = """
        <html><body>
        <a href="https://boerse.am/showthread.php?t=123">Thread 1</a>
        <a href="https://boerse.am/showthread.php?t=456">Thread 2</a>
        </body></html>
        """

        thread_html = """
        <html><head><title>SpongeBob S01 - boerse.am</title></head><body>
        <a href="https://boerse.am/abc123" target="_blank">https://veev.to/dl/spongebob</a>
        <a href="https://boerse.am/def456" target="_blank">https://dood.to/dl/spongebob</a>
        </body></html>
        """

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=_mock_response(200, text=search_html))
        mock_client.get = AsyncMock(return_value=_mock_response(200, text=thread_html))

        plugin._client = mock_client
        plugin._logged_in = True
        plugin.base_url = "https://boerse.am"

        results = await plugin.search("SpongeBob")

        assert len(results) == 2
        assert results[0].title == "SpongeBob S01"
        assert results[0].download_link == "https://veev.to/dl/spongebob"
        assert len(results[0].download_links) == 2

    async def test_search_no_threads(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(
            return_value=_mock_response(
                200, text="<html><body>No results</body></html>"
            )
        )

        plugin._client = mock_client
        plugin._logged_in = True
        plugin.base_url = "https://boerse.am"

        results = await plugin.search("nonexistent")
        assert results == []


class TestDownloadLinkExtraction:
    def test_anonymized_links_extracted(self) -> None:
        html = """
        <a href="https://boerse.am/abc123" target="_blank">https://veev.to/actual-file</a>
        <a href="https://boerse.am/def456" target="_blank">https://dood.to/another-file</a>
        <a href="/some/internal/link">Click here</a>
        """

        parser = _LinkParser()
        parser.feed(html)

        assert len(parser.links) == 2
        assert parser.links[0] == "https://veev.to/actual-file"
        assert parser.links[1] == "https://dood.to/another-file"

    def test_non_http_text_ignored(self) -> None:
        html = """
        <a href="https://boerse.am/abc">Click for download</a>
        <a href="https://boerse.am/def">ftp://something</a>
        """

        parser = _LinkParser()
        parser.feed(html)
        assert len(parser.links) == 0

    def test_thread_link_parser(self) -> None:
        html = """
        <a href="showthread.php?t=123">Thread 1</a>
        <a href="/threads/456-some-thread">Thread 2</a>
        <a href="/other/page">Not a thread</a>
        """

        parser = _ThreadLinkParser("https://boerse.am")
        parser.feed(html)

        assert len(parser.thread_urls) == 2
        assert parser.thread_urls[0] == "https://boerse.am/showthread.php?t=123"
        assert parser.thread_urls[1] == "https://boerse.am/threads/456-some-thread"

    def test_hoster_from_url(self) -> None:
        assert _hoster_from_url("https://veev.to/dl/file") == "veev"
        assert _hoster_from_url("https://www.dood.to/dl/file") == "dood"
        assert _hoster_from_url("https://voe.sx/embed/abc") == "voe"
