"""Tests for the myboerse.bz Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "myboerse.py"


def _load_myboerse_module() -> ModuleType:
    """Load myboerse.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("myboerse_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once at module level for parser tests
_myboerse = _load_myboerse_module()
_MyboersePlugin = _myboerse.MyboersePlugin
_LoginTokenParser = _myboerse._LoginTokenParser
_SearchResultParser = _myboerse._SearchResultParser
_ThreadPostParser = _myboerse._ThreadPostParser
_TORZNAB_TO_NODE_IDS = _myboerse._TORZNAB_TO_NODE_IDS
_NODE_TO_TORZNAB = _myboerse._NODE_TO_TORZNAB
_FORUM_NAME_MAP = _myboerse._FORUM_NAME_MAP
_DOMAINS = _MyboersePlugin._domains
_is_download_link = _myboerse._is_download_link
_hoster_from_text = _myboerse._hoster_from_text
_hoster_from_url = _myboerse._hoster_from_url
_forum_name_to_torznab = _myboerse._forum_name_to_torznab
_node_id_from_url = _myboerse._node_id_from_url

_TEST_CREDENTIALS = {
    "SCAVENGARR_MYBOERSE_USERNAME": "testuser",
    "SCAVENGARR_MYBOERSE_PASSWORD": "testpass",
}


def _make_plugin() -> object:
    """Create MyboersePlugin instance."""
    return _MyboersePlugin()


class TestPluginAttributes:
    def test_name_attribute(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "myboerse"

    def test_version_attribute(self) -> None:
        plugin = _make_plugin()
        assert plugin.version == "1.0.0"

    def test_mode_attribute(self) -> None:
        plugin = _make_plugin()
        assert plugin.mode == "httpx"

    def test_domains_list(self) -> None:
        assert "myboerse.bz" in _DOMAINS
        assert "myboerse.ws" in _DOMAINS
        assert "myboerse.me" in _DOMAINS
        assert _DOMAINS[0] == "myboerse.bz"


class TestDomainVerification:
    async def test_first_domain_reachable(self) -> None:
        plugin = _make_plugin()

        head_resp = MagicMock()
        head_resp.status_code = 200

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=head_resp)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert plugin.base_url == f"https://{_DOMAINS[0]}"
        mock_client.head.assert_awaited_once()

    async def test_fallback_to_second_domain(self) -> None:
        plugin = _make_plugin()

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        fail_exc = httpx.ConnectError("Connection refused")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=[fail_exc, ok_resp])
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert plugin.base_url == f"https://{_DOMAINS[1]}"

    async def test_all_domains_fail_uses_first(self) -> None:
        plugin = _make_plugin()

        fail_exc = httpx.ConnectError("Connection refused")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=fail_exc)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert plugin.base_url == f"https://{_DOMAINS[0]}"

    async def test_skips_if_already_verified(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin._verify_domain()

        mock_client.head.assert_not_awaited()


class TestLoginTokenParser:
    def test_extracts_token(self) -> None:
        html = """
        <html><body>
        <form action="/login/login" method="post">
        <input type="hidden" name="_xfToken" value="abc123,1234567890">
        <input type="text" name="login">
        <input type="password" name="password">
        </form>
        </body></html>
        """
        parser = _LoginTokenParser()
        parser.feed(html)
        assert parser.token == "abc123,1234567890"

    def test_no_token(self) -> None:
        html = "<html><body><form></form></body></html>"
        parser = _LoginTokenParser()
        parser.feed(html)
        assert parser.token == ""

    def test_first_token_wins(self) -> None:
        html = """
        <input type="hidden" name="_xfToken" value="first_token">
        <input type="hidden" name="_xfToken" value="second_token">
        """
        parser = _LoginTokenParser()
        parser.feed(html)
        assert parser.token == "first_token"


class TestSearchResultParser:
    def test_parses_search_results(self) -> None:
        html = """
        <html><body>
        <h3 class="contentRow-title">
          <a href="/threads/batman-forever-uhd.12345/">Batman Forever UHD</a>
        </h3>
        <a href="/forums/hd.62/">HD</a>
        <h3 class="contentRow-title">
          <a href="/threads/inception-4k.67890/">Inception 4K</a>
        </h3>
        <a href="/forums/uhd-4k.75/">UHD/4K</a>
        </body></html>
        """
        parser = _SearchResultParser("https://myboerse.bz")
        parser.feed(html)
        parser.flush_pending()

        assert len(parser.results) == 2
        assert parser.results[0]["title"] == "Batman Forever UHD"
        assert "/threads/batman-forever-uhd.12345/" in parser.results[0]["url"]
        assert parser.results[0]["forum"] == "HD"
        assert parser.results[1]["title"] == "Inception 4K"

    def test_parses_empty_results(self) -> None:
        html = "<html><body><p>No results found.</p></body></html>"
        parser = _SearchResultParser("https://myboerse.bz")
        parser.feed(html)
        parser.flush_pending()
        assert parser.results == []

    def test_detects_next_page(self) -> None:
        html = """
        <h3 class="contentRow-title">
          <a href="/threads/test.123/">Test</a>
        </h3>
        <a href="/forums/hd.62/">HD</a>
        <a href="/search/12345/?page-2">Nächste</a>
        """
        parser = _SearchResultParser("https://myboerse.bz")
        parser.feed(html)
        parser.flush_pending()

        assert parser.next_page_url == "/search/12345/?page-2"

    def test_no_next_page(self) -> None:
        html = """
        <h3 class="contentRow-title">
          <a href="/threads/test.123/">Test</a>
        </h3>
        <a href="/forums/hd.62/">HD</a>
        """
        parser = _SearchResultParser("https://myboerse.bz")
        parser.feed(html)
        parser.flush_pending()

        assert parser.next_page_url == ""

    def test_result_without_forum(self) -> None:
        """Results without a forum link should still be emitted."""
        html = """
        <h3 class="contentRow-title">
          <a href="/threads/orphan.999/">Orphan Result</a>
        </h3>
        """
        parser = _SearchResultParser("https://myboerse.bz")
        parser.feed(html)
        parser.flush_pending()

        assert len(parser.results) == 1
        assert parser.results[0]["title"] == "Orphan Result"
        assert parser.results[0]["forum"] == ""

    def test_flush_pending_emits_last_result(self) -> None:
        """The last result with no forum should be flushed."""
        html = """
        <h3 class="contentRow-title">
          <a href="/threads/first.100/">First</a>
        </h3>
        <a href="/forums/hd.62/">HD</a>
        <h3 class="contentRow-title">
          <a href="/threads/last.200/">Last</a>
        </h3>
        """
        parser = _SearchResultParser("https://myboerse.bz")
        parser.feed(html)
        parser.flush_pending()

        assert len(parser.results) == 2
        assert parser.results[1]["title"] == "Last"


class TestThreadPostParser:
    def test_extracts_container_links(self) -> None:
        html = """
        <div class="bbWrapper">
          <a href="https://hide.cx/container/abc123">Online rapidgator.net</a>
          <a href="https://filecrypt.cc/Container/xyz.html">Online ddownload.com</a>
        </div>
        """
        parser = _ThreadPostParser()
        parser.feed(html)

        assert len(parser.links) == 2
        assert parser.links[0]["link"] == "https://hide.cx/container/abc123"
        assert parser.links[0]["hoster"] == "rapidgator"
        assert parser.links[1]["link"] == "https://filecrypt.cc/Container/xyz.html"
        assert parser.links[1]["hoster"] == "ddownload"

    def test_extracts_xtra_links(self) -> None:
        html = """
        <div class="bbWrapper">
          <a href="https://myboerse.bz/xtra/?x=Batman">Batman Download</a>
        </div>
        """
        parser = _ThreadPostParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert "/xtra/" in parser.links[0]["link"]
        assert parser.links[0]["hoster"] == "myboerse"

    def test_non_download_links_skipped(self) -> None:
        html = """
        <div class="bbWrapper">
          <a href="https://www.imdb.com/title/tt123">IMDB</a>
          <a href="https://youtube.com/watch?v=abc">Trailer</a>
          <a href="https://hide.cx/container/abc123">Online rapidgator.net</a>
        </div>
        """
        parser = _ThreadPostParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert "hide.cx" in parser.links[0]["link"]

    def test_links_outside_bbwrapper_ignored(self) -> None:
        html = """
        <a href="https://hide.cx/container/outside">Outside</a>
        <div class="bbWrapper">
          <a href="https://hide.cx/container/inside">Inside</a>
        </div>
        """
        parser = _ThreadPostParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert "inside" in parser.links[0]["link"]

    def test_duplicate_links_deduplicated(self) -> None:
        html = """
        <div class="bbWrapper">
          <a href="https://hide.cx/container/abc123">rapidgator.net</a>
          <a href="https://hide.cx/container/abc123">rapidgator.net</a>
        </div>
        """
        parser = _ThreadPostParser()
        parser.feed(html)

        assert len(parser.links) == 1

    def test_nested_divs_do_not_exit_early(self) -> None:
        html = """
        <div class="bbWrapper">
          <div class="bbCodeBlock">
            <div class="bbCodeBlock-content">
              NFO content here
            </div>
          </div>
          <a href="https://hide.cx/container/abc123">Online rapidgator.net</a>
        </div>
        """
        parser = _ThreadPostParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert parser.links[0]["hoster"] == "rapidgator"

    def test_multiple_bbwrapper_blocks(self) -> None:
        """Links from multiple posts should all be collected."""
        html = """
        <div class="bbWrapper">
          <a href="https://hide.cx/container/aaa">Online rapidgator.net</a>
        </div>
        <div class="bbWrapper">
          <a href="https://hide.cx/container/bbb">Online ddownload.com</a>
        </div>
        """
        parser = _ThreadPostParser()
        parser.feed(html)

        assert len(parser.links) == 2

    def test_keeplinks_accepted(self) -> None:
        html = """
        <div class="bbWrapper">
          <a href="https://www.keeplinks.org/p53/abc123">RapidGator</a>
        </div>
        """
        parser = _ThreadPostParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert "keeplinks.org" in parser.links[0]["link"]

    def test_tolink_accepted(self) -> None:
        html = """
        <div class="bbWrapper">
          <a href="https://tolink.to/abc123">DDownload</a>
        </div>
        """
        parser = _ThreadPostParser()
        parser.feed(html)

        assert len(parser.links) == 1
        assert "tolink.to" in parser.links[0]["link"]


class TestHelpers:
    def test_is_download_link_container_host(self) -> None:
        assert _is_download_link("https://hide.cx/container/abc") is True
        assert _is_download_link("https://filecrypt.cc/Container/x") is True
        assert _is_download_link("https://keeplinks.org/p53/abc") is True

    def test_is_download_link_xtra(self) -> None:
        assert _is_download_link("https://myboerse.bz/xtra/?x=Batman") is True

    def test_is_download_link_rejects_other(self) -> None:
        assert _is_download_link("https://imdb.com/title/tt123") is False
        assert _is_download_link("https://youtube.com/watch?v=abc") is False

    def test_hoster_from_text_online_pattern(self) -> None:
        assert _hoster_from_text("Online rapidgator.net") == "rapidgator"
        assert _hoster_from_text("Online ddownload.com") == "ddownload"

    def test_hoster_from_text_plain_name(self) -> None:
        assert _hoster_from_text("RapidGator") == "rapidgator"
        assert _hoster_from_text("DDownload") == "ddownload"

    def test_hoster_from_text_empty(self) -> None:
        assert _hoster_from_text("") == ""

    def test_hoster_from_url(self) -> None:
        assert _hoster_from_url("https://hide.cx/container/abc") == "hide"
        assert _hoster_from_url("https://www.keeplinks.org/p53/abc") == "keeplinks"

    def test_hoster_from_url_xtra(self) -> None:
        assert _hoster_from_url("https://myboerse.bz/xtra/?x=Batman") == "myboerse"

    def test_forum_name_to_torznab(self) -> None:
        assert _forum_name_to_torznab("HD") == 2000
        assert _forum_name_to_torznab("Serien") == 5000
        assert _forum_name_to_torznab("Anime") == 5070
        assert _forum_name_to_torznab("Unterhaltung") == 7000
        assert _forum_name_to_torznab("Musik") == 3000
        assert _forum_name_to_torznab("PC Spiele") == 4000
        assert _forum_name_to_torznab("unknown category") == 2000

    def test_node_id_from_url(self) -> None:
        assert _node_id_from_url("/forums/hd.62/") == 62
        assert _node_id_from_url("/forums/uhd-4k.75/") == 75
        assert _node_id_from_url("/forums/filme.60/") == 60
        assert _node_id_from_url("/other/path") is None


class TestCategoryMapping:
    def test_torznab_to_nodes_has_movies(self) -> None:
        assert 2000 in _TORZNAB_TO_NODE_IDS
        assert 60 in _TORZNAB_TO_NODE_IDS[2000]  # Filme

    def test_torznab_to_nodes_has_tv(self) -> None:
        assert 5000 in _TORZNAB_TO_NODE_IDS
        assert 63 in _TORZNAB_TO_NODE_IDS[5000]  # Serien

    def test_torznab_to_nodes_has_audio(self) -> None:
        assert 3000 in _TORZNAB_TO_NODE_IDS
        assert 51 in _TORZNAB_TO_NODE_IDS[3000]  # Musik

    def test_torznab_to_nodes_has_games(self) -> None:
        assert 4000 in _TORZNAB_TO_NODE_IDS
        assert 24 in _TORZNAB_TO_NODE_IDS[4000]  # PC Spiele

    def test_torznab_to_nodes_has_books(self) -> None:
        assert 7000 in _TORZNAB_TO_NODE_IDS
        assert 37 in _TORZNAB_TO_NODE_IDS[7000]  # Comics

    def test_torznab_to_nodes_has_anime(self) -> None:
        assert 5070 in _TORZNAB_TO_NODE_IDS
        assert 64 in _TORZNAB_TO_NODE_IDS[5070]  # Anime

    def test_torznab_to_nodes_has_documentary(self) -> None:
        assert 5080 in _TORZNAB_TO_NODE_IDS
        assert 65 in _TORZNAB_TO_NODE_IDS[5080]  # Dokumentationen

    def test_torznab_to_nodes_has_audiobooks(self) -> None:
        assert 3030 in _TORZNAB_TO_NODE_IDS
        assert 52 in _TORZNAB_TO_NODE_IDS[3030]  # Hörbücher

    def test_torznab_to_nodes_has_console(self) -> None:
        assert 1000 in _TORZNAB_TO_NODE_IDS
        assert 29 in _TORZNAB_TO_NODE_IDS[1000]  # Sony

    def test_torznab_to_nodes_has_software(self) -> None:
        assert 5020 in _TORZNAB_TO_NODE_IDS
        assert 9 in _TORZNAB_TO_NODE_IDS[5020]  # Windows

    def test_node_to_torznab_reverse_map(self) -> None:
        assert _NODE_TO_TORZNAB[60] == 2000  # Filme → Movies
        assert _NODE_TO_TORZNAB[63] == 5000  # Serien → TV
        assert _NODE_TO_TORZNAB[51] == 3000  # Musik → Audio
        assert _NODE_TO_TORZNAB[24] == 4000  # PC Spiele → Games
        assert _NODE_TO_TORZNAB[37] == 7000  # Comics → Books

    def test_all_nodes_have_reverse_mapping(self) -> None:
        for tz_cat, nodes in _TORZNAB_TO_NODE_IDS.items():
            for nid in nodes:
                assert nid in _NODE_TO_TORZNAB
                assert _NODE_TO_TORZNAB[nid] == tz_cat


class TestLogin:
    async def test_login_success(self) -> None:
        plugin = _make_plugin()

        login_html = (
            "<html><body><form>"
            '<input type="hidden" name="_xfToken" value="token123">'
            "</form></body></html>"
        )

        mock_jar = MagicMock()
        mock_cookie = MagicMock()
        mock_cookie.name = "xf_user"
        mock_jar.__iter__ = MagicMock(return_value=iter([mock_cookie]))

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        login_resp = MagicMock()
        login_resp.text = login_html
        login_resp.raise_for_status = MagicMock()
        post_resp = MagicMock()
        post_resp.raise_for_status = MagicMock()

        mock_client.get = AsyncMock(return_value=login_resp)
        mock_client.post = AsyncMock(return_value=post_resp)
        mock_client.cookies = MagicMock()
        mock_client.cookies.jar = mock_jar

        plugin._client = mock_client

        with patch.dict(os.environ, _TEST_CREDENTIALS):
            await plugin._login()

        assert plugin._logged_in is True
        mock_client.post.assert_awaited_once()

    async def test_login_missing_credentials_raises(self) -> None:
        plugin = _make_plugin()
        plugin._client = AsyncMock(spec=httpx.AsyncClient)

        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(RuntimeError, match="Missing credentials"),
        ):
            os.environ.pop("SCAVENGARR_MYBOERSE_USERNAME", None)
            os.environ.pop("SCAVENGARR_MYBOERSE_PASSWORD", None)
            await plugin._login()

    async def test_login_missing_token_raises(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock()
        resp.text = "<html><body>No token here</body></html>"
        resp.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=resp)

        plugin._client = mock_client

        with (
            patch.dict(os.environ, _TEST_CREDENTIALS),
            pytest.raises(RuntimeError, match="Could not extract _xfToken"),
        ):
            await plugin._login()

    async def test_login_no_session_cookie_raises(self) -> None:
        plugin = _make_plugin()

        login_html = '<input type="hidden" name="_xfToken" value="token123">'

        mock_jar = MagicMock()
        mock_jar.__iter__ = MagicMock(return_value=iter([]))

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        login_resp = MagicMock()
        login_resp.text = login_html
        login_resp.raise_for_status = MagicMock()
        post_resp = MagicMock()
        post_resp.raise_for_status = MagicMock()

        mock_client.get = AsyncMock(return_value=login_resp)
        mock_client.post = AsyncMock(return_value=post_resp)
        mock_client.cookies = MagicMock()
        mock_client.cookies.jar = mock_jar

        plugin._client = mock_client

        with (
            patch.dict(os.environ, _TEST_CREDENTIALS),
            pytest.raises(RuntimeError, match="Login failed"),
        ):
            await plugin._login()

    async def test_session_reuse(self) -> None:
        plugin = _make_plugin()
        plugin._logged_in = True
        plugin._domain_verified = True
        plugin._client = AsyncMock(spec=httpx.AsyncClient)

        await plugin._login()
        plugin._client.get.assert_not_awaited()


class TestSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()
        plugin._logged_in = True
        plugin._domain_verified = True

        search_html = """
        <html><body>
        <h3 class="contentRow-title">
          <a href="/threads/batman-4k.123/">Batman 4K</a>
        </h3>
        <a href="/forums/uhd-4k.75/">UHD/4K</a>
        </body></html>
        """

        thread_html = """
        <html><body>
        <div class="bbWrapper">
          <a href="https://hide.cx/container/abc">Online rapidgator.net</a>
          <a href="https://hide.cx/container/def">Online ddownload.com</a>
        </div>
        </body></html>
        """

        search_resp = MagicMock()
        search_resp.text = search_html
        search_resp.raise_for_status = MagicMock()
        thread_resp = MagicMock()
        thread_resp.text = thread_html
        thread_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(return_value=thread_resp)
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert len(results) == 1
        assert results[0].title == "Batman 4K"
        assert "hide.cx" in results[0].download_link
        assert len(results[0].download_links) == 2
        assert results[0].download_links[0]["hoster"] == "rapidgator"
        assert results[0].category == 2000

    async def test_search_with_category(self) -> None:
        plugin = _make_plugin()
        plugin._logged_in = True
        plugin._domain_verified = True

        search_html = """
        <h3 class="contentRow-title">
          <a href="/threads/album.456/">Some Album</a>
        </h3>
        <a href="/forums/musik.51/">Musik</a>
        """

        thread_html = """
        <div class="bbWrapper">
          <a href="https://hide.cx/container/abc">Online rapidgator.net</a>
        </div>
        """

        search_resp = MagicMock()
        search_resp.text = search_html
        search_resp.raise_for_status = MagicMock()
        thread_resp = MagicMock()
        thread_resp.text = thread_html
        thread_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(return_value=thread_resp)
        plugin._client = mock_client

        results = await plugin.search("album", category=3000)

        assert len(results) == 1
        assert results[0].category == 3000
        # Verify node IDs were passed in search
        call_kwargs = mock_client.post.call_args
        data = call_kwargs.kwargs.get("data", {})
        assert "c[nodes][]" in data

    async def test_search_no_results(self) -> None:
        plugin = _make_plugin()
        plugin._logged_in = True
        plugin._domain_verified = True

        empty_html = "<html><body>No results</body></html>"
        search_resp = MagicMock()
        search_resp.text = empty_html
        search_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        plugin._client = mock_client

        results = await plugin.search("nonexistent")
        assert results == []

    async def test_search_thread_without_links_skipped(self) -> None:
        plugin = _make_plugin()
        plugin._logged_in = True
        plugin._domain_verified = True

        search_html = """
        <h3 class="contentRow-title">
          <a href="/threads/test.123/">Test Thread</a>
        </h3>
        <a href="/forums/hd.62/">HD</a>
        """

        thread_html = """
        <div class="bbWrapper">
          <p>Just text, no download links.</p>
        </div>
        """

        search_resp = MagicMock()
        search_resp.text = search_html
        search_resp.raise_for_status = MagicMock()
        thread_resp = MagicMock()
        thread_resp.text = thread_html
        thread_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(return_value=thread_resp)
        plugin._client = mock_client

        results = await plugin.search("test")
        assert results == []

    async def test_search_paginates(self) -> None:
        plugin = _make_plugin()
        plugin._logged_in = True
        plugin._domain_verified = True

        search_html_p1 = """
        <h3 class="contentRow-title">
          <a href="/threads/page1.100/">Page 1 Result</a>
        </h3>
        <a href="/forums/hd.62/">HD</a>
        <a href="/search/999/?page-2">Nächste</a>
        """

        search_html_p2 = """
        <h3 class="contentRow-title">
          <a href="/threads/page2.200/">Page 2 Result</a>
        </h3>
        <a href="/forums/hd.62/">HD</a>
        """

        thread_html = """
        <div class="bbWrapper">
          <a href="https://hide.cx/container/abc">Online rapidgator.net</a>
        </div>
        """

        search_resp_p1 = MagicMock()
        search_resp_p1.text = search_html_p1
        search_resp_p1.raise_for_status = MagicMock()
        search_resp_p2 = MagicMock()
        search_resp_p2.text = search_html_p2
        search_resp_p2.raise_for_status = MagicMock()
        thread_resp = MagicMock()
        thread_resp.text = thread_html
        thread_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp_p1)
        mock_client.get = AsyncMock(
            side_effect=[search_resp_p2, thread_resp, thread_resp]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        assert len(results) == 2

    async def test_search_with_xtra_links(self) -> None:
        plugin = _make_plugin()
        plugin._logged_in = True
        plugin._domain_verified = True

        search_html = """
        <h3 class="contentRow-title">
          <a href="/threads/batman.123/">Batman</a>
        </h3>
        <a href="/forums/filme.60/">Filme</a>
        """

        thread_html = """
        <div class="bbWrapper">
          <a href="https://myboerse.bz/xtra/?x=Batman">Download</a>
        </div>
        """

        search_resp = MagicMock()
        search_resp.text = search_html
        search_resp.raise_for_status = MagicMock()
        thread_resp = MagicMock()
        thread_resp.text = thread_html
        thread_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(return_value=thread_resp)
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert len(results) == 1
        assert "/xtra/" in results[0].download_link
        assert results[0].category == 2000


class TestCleanup:
    async def test_cleanup_closes_client(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client
        plugin._logged_in = True
        plugin._domain_verified = True

        await plugin.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert plugin._client is None
        assert plugin._logged_in is False

    async def test_cleanup_no_client(self) -> None:
        plugin = _make_plugin()
        plugin._client = None

        await plugin.cleanup()
        assert plugin._client is None
