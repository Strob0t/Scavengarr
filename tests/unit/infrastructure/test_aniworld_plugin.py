"""Tests for the aniworld.to Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import httpx

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "aniworld.py"


def _load_module() -> ModuleType:
    """Load aniworld.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location("aniworld_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_AniworldPlugin = _mod.AniworldPlugin
_DetailPageParser = _mod._DetailPageParser
_EpisodePageParser = _mod._EpisodePageParser
_strip_html_tags = _mod._strip_html_tags


def _make_plugin() -> object:
    """Create AniworldPlugin instance."""
    return _AniworldPlugin()


# ---------------------------------------------------------------------------
# Sample HTML fragments
# ---------------------------------------------------------------------------

_DETAIL_HTML = """\
<html><body>
<div class="seriesCoverBox">
  <img data-src="/public/img/covers/naruto.jpg" alt="Naruto">
</div>

<div class="seri_des"
 data-full-description="Naruto is a young ninja who seeks recognition.">
  <p>Naruto is a young ninja...</p>
</div>

<div class="genres">
  <ul>
    <li><a href="/genre/action">Action</a></li>
    <li><a href="/genre/adventure">Adventure</a></li>
    <li><a href="/genre/comedy">Comedy</a></li>
  </ul>
</div>

<table class="seasonEpisodesList">
  <tbody>
    <tr>
      <td>
        <a href="/anime/stream/naruto/staffel-1/episode-1">Episode 1</a>
      </td>
    </tr>
    <tr>
      <td>
        <a href="/anime/stream/naruto/staffel-1/episode-2">Episode 2</a>
      </td>
    </tr>
  </tbody>
</table>
</body></html>
"""

_DETAIL_HTML_NO_DATA_ATTR = """\
<html><body>
<div class="seri_des">
  Inline description text here.
</div>
</body></html>
"""

_EPISODE_HTML = """\
<html><body>
<ul class="hosterSiteVideo">
  <li data-lang-key="1" data-link-id="100" data-link-target="/redirect/100">
    <div class="watchEpisode">
      <a href="#"><h4>VOE</h4></a>
    </div>
  </li>
  <li data-lang-key="2" data-link-id="101" data-link-target="/redirect/101">
    <div class="watchEpisode">
      <a href="#"><h4>Filemoon</h4></a>
    </div>
  </li>
  <li data-lang-key="1" data-link-id="102" data-link-target="/redirect/102">
    <div class="watchEpisode">
      <a href="#"><h4>Vidmoly</h4></a>
    </div>
  </li>
  <li data-lang-key="3" data-link-id="103" data-link-target="/redirect/103">
    <div class="watchEpisode">
      <a href="#"><h4>Doodstream</h4></a>
    </div>
  </li>
</ul>
</body></html>
"""

_AJAX_SEARCH_RESPONSE = [
    {
        "title": "<em>Naruto</em>",
        "description": "A <em>ninja</em> story",
        "link": "/anime/stream/naruto",
    },
    {
        "title": "<em>Naruto</em> Shippuuden",
        "description": "Continuation of <em>Naruto</em>",
        "link": "/anime/stream/naruto-shippuuden",
    },
]


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "aniworld"

    def test_version(self) -> None:
        plugin = _make_plugin()
        assert plugin.version == "1.0.0"

    def test_mode(self) -> None:
        plugin = _make_plugin()
        assert plugin.mode == "httpx"


# ---------------------------------------------------------------------------
# DetailPageParser tests
# ---------------------------------------------------------------------------


class TestDetailPageParser:
    def test_description_from_data_attr(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed(_DETAIL_HTML)
        assert parser.description == "Naruto is a young ninja who seeks recognition."

    def test_description_fallback_to_text(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed(_DETAIL_HTML_NO_DATA_ATTR)
        assert parser.description == "Inline description text here."

    def test_genres_extracted(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed(_DETAIL_HTML)
        assert parser.genres == ["Action", "Adventure", "Comedy"]

    def test_cover_url(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed(_DETAIL_HTML)
        assert parser.cover_url == "https://aniworld.to/public/img/covers/naruto.jpg"

    def test_first_episode_url(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed(_DETAIL_HTML)
        assert (
            parser.first_episode_url
            == "https://aniworld.to/anime/stream/naruto/staffel-1/episode-1"
        )

    def test_no_episode_table(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed("<html><body><p>No table</p></body></html>")
        assert parser.first_episode_url == ""

    def test_no_genres(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed("<html><body></body></html>")
        assert parser.genres == []


# ---------------------------------------------------------------------------
# EpisodePageParser tests
# ---------------------------------------------------------------------------


class TestEpisodePageParser:
    def test_hoster_links_extracted(self) -> None:
        parser = _EpisodePageParser("https://aniworld.to")
        parser.feed(_EPISODE_HTML)
        assert len(parser.hoster_links) == 4

    def test_hoster_names(self) -> None:
        parser = _EpisodePageParser("https://aniworld.to")
        parser.feed(_EPISODE_HTML)
        names = [h["hoster"] for h in parser.hoster_links]
        assert names == ["voe", "filemoon", "vidmoly", "doodstream"]

    def test_redirect_urls(self) -> None:
        parser = _EpisodePageParser("https://aniworld.to")
        parser.feed(_EPISODE_HTML)
        links = [h["link"] for h in parser.hoster_links]
        assert links == [
            "https://aniworld.to/redirect/100",
            "https://aniworld.to/redirect/101",
            "https://aniworld.to/redirect/102",
            "https://aniworld.to/redirect/103",
        ]

    def test_language_labels(self) -> None:
        parser = _EpisodePageParser("https://aniworld.to")
        parser.feed(_EPISODE_HTML)
        langs = [h["language"] for h in parser.hoster_links]
        assert langs == [
            "German Dub",
            "English Sub",
            "German Dub",
            "German Sub",
        ]

    def test_empty_page(self) -> None:
        parser = _EpisodePageParser("https://aniworld.to")
        parser.feed("<html><body></body></html>")
        assert parser.hoster_links == []

    def test_li_without_lang_key_skipped(self) -> None:
        html = """
        <ul>
          <li data-link-target="/redirect/999">
            <h4>NoLang</h4>
          </li>
        </ul>
        """
        parser = _EpisodePageParser("https://aniworld.to")
        parser.feed(html)
        assert parser.hoster_links == []


# ---------------------------------------------------------------------------
# HTML tag stripper tests
# ---------------------------------------------------------------------------


class TestStripHtmlTags:
    def test_strips_em_tags(self) -> None:
        assert _strip_html_tags("<em>Naruto</em>") == "Naruto"

    def test_strips_mixed_tags(self) -> None:
        assert _strip_html_tags("A <b>bold</b> and <em>italic</em> text") == (
            "A bold and italic text"
        )

    def test_plain_text_unchanged(self) -> None:
        assert _strip_html_tags("plain text") == "plain text"

    def test_empty_string(self) -> None:
        assert _strip_html_tags("") == ""


# ---------------------------------------------------------------------------
# Search integration tests (mocked httpx)
# ---------------------------------------------------------------------------


def _make_mock_response(
    status_code: int = 200,
    json_data: object = None,
    text: str = "",
) -> httpx.Response:
    """Create a mock httpx.Response."""
    resp = httpx.Response(
        status_code=status_code,
        request=httpx.Request("GET", "https://aniworld.to"),
    )
    if json_data is not None:
        import json

        resp._content = json.dumps(json_data).encode()
    elif text:
        resp._content = text.encode()
    else:
        resp._content = b""
    return resp


class TestSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_resp = _make_mock_response(json_data=_AJAX_SEARCH_RESPONSE)
        detail_resp = _make_mock_response(text=_DETAIL_HTML)
        episode_resp = _make_mock_response(text=_EPISODE_HTML)

        def _route_get(url, **_kw):
            if "/staffel-" in str(url) and "/episode-" in str(url):
                return episode_resp
            return detail_resp

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(side_effect=_route_get)
        plugin._client = mock_client

        results = await plugin.search("naruto")

        assert len(results) == 2
        assert results[0].title == "Naruto"
        assert results[0].category == 5070
        assert "redirect/100" in results[0].download_link
        assert len(results[0].download_links) == 4
        assert results[0].download_links[0]["hoster"] == "voe"
        expected_desc = "Naruto is a young ninja who seeks recognition."
        assert results[0].description == expected_desc

    async def test_search_empty_query_returns_empty(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        results = await plugin.search("")
        assert results == []

    async def test_search_non_anime_category_returns_empty(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        results = await plugin.search("naruto", category=2000)
        assert results == []

    async def test_search_anime_category_allowed(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_resp = _make_mock_response(json_data=_AJAX_SEARCH_RESPONSE[:1])
        detail_resp = _make_mock_response(text=_DETAIL_HTML)
        episode_resp = _make_mock_response(text=_EPISODE_HTML)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(
            side_effect=lambda url, **kw: (
                episode_resp
                if "/staffel-" in str(url) and "/episode-" in str(url)
                else detail_resp
            )
        )
        plugin._client = mock_client

        results = await plugin.search("naruto", category=5070)
        assert len(results) == 1

    async def test_search_parent_tv_category_allowed(self) -> None:
        """Parent category 5000 (any TV) must include anime (5070)."""
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_resp = _make_mock_response(json_data=_AJAX_SEARCH_RESPONSE[:1])
        detail_resp = _make_mock_response(text=_DETAIL_HTML)
        episode_resp = _make_mock_response(text=_EPISODE_HTML)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(
            side_effect=lambda url, **kw: (
                episode_resp
                if "/staffel-" in str(url) and "/episode-" in str(url)
                else detail_resp
            )
        )
        plugin._client = mock_client

        results = await plugin.search("naruto", category=5000)
        assert len(results) == 1

    async def test_search_no_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_resp = _make_mock_response(json_data=[])
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        plugin._client = mock_client

        results = await plugin.search("nonexistent_anime_xyz")
        assert results == []

    async def test_search_detail_without_hosters_skipped(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_resp = _make_mock_response(json_data=_AJAX_SEARCH_RESPONSE[:1])
        # Detail page with no episode table → no first_episode_url
        detail_resp = _make_mock_response(text="<html><body>No table</body></html>")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(return_value=detail_resp)
        plugin._client = mock_client

        results = await plugin.search("naruto")
        assert results == []

    async def test_search_ajax_error_returns_empty(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        plugin._client = mock_client

        results = await plugin.search("naruto")
        assert results == []


# ---------------------------------------------------------------------------
# Domain verification tests
# ---------------------------------------------------------------------------


class TestDomainVerification:
    async def test_first_domain_works(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.url = httpx.URL("https://aniworld.to/")
        mock_client.head = AsyncMock(return_value=resp)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "aniworld.to" in plugin.base_url

    async def test_fallback_to_second_domain(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        call_count = 0

        async def mock_head(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("Connection failed")
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            resp.url = httpx.URL("https://aniworld.info/")
            return resp

        mock_client.head = AsyncMock(side_effect=mock_head)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "aniworld.info" in plugin.base_url
        assert call_count == 2

    async def test_all_domains_fail(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(
            side_effect=httpx.ConnectError("Connection failed")
        )
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert _AniworldPlugin._domains[0] in plugin.base_url

    async def test_skips_if_already_verified(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://custom.domain"
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin._verify_domain()

        mock_client.head.assert_not_called()
        assert plugin.base_url == "https://custom.domain"


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestCleanup:
    async def test_cleanup_closes_client(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert plugin._client is None

    async def test_cleanup_no_client(self) -> None:
        plugin = _make_plugin()
        await plugin.cleanup()
        assert plugin._client is None
