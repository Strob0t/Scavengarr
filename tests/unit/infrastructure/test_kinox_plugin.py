"""Unit tests for the kinox.to plugin."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "kinox.py"


@pytest.fixture()
def kinox_mod():
    """Import kinox plugin module."""
    spec = importlib.util.spec_from_file_location("kinox", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kinox"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("kinox", None)


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

SEARCH_HTML = """\
<div id="Vadda">
  <div onclick="location.href='/Stream/Batman_Begins.html';"
       style="float: left; width: 371px;">
    <div class="ModuleHead mHead">
      <div class="Opt leftOpt Headlne">
        <a title="Batman Begins" href="/Stream/Batman_Begins.html">
          <h1>Batman Begins</h1>
        </a>
      </div>
    </div>
    <div class="MiniEntry">
      <div class="Descriptor">A young Bruce Wayne...</div>
      <div class="Genre">
        <div class="floatleft">
          <b>Genre:</b>
          <a href="/Genre/Action">Action</a>,
          <a href="/Genre/Crime">Crime</a>
        </div>
        <div class="floatright"><b>IMDb:</b> 8 / 10</div>
      </div>
    </div>
    <div class="ModuleFooter"></div>
  </div>
  <div onclick="location.href='/Stream/The_Batman-3.html';"
       style="float: left; width: 371px;">
    <div class="ModuleHead mHead">
      <div class="Opt leftOpt Headlne">
        <a title="The Batman" href="/Stream/The_Batman-3.html">
          <h1>The Batman</h1>
        </a>
      </div>
    </div>
    <div class="MiniEntry">
      <div class="Descriptor">A reclusive young billionaire...</div>
      <div class="Genre">
        <div class="floatleft">
          <b>Genre:</b>
          <a href="/Genre/Action">Action</a>
        </div>
        <div class="floatright"><b>IMDb:</b> 7.8 / 10</div>
      </div>
    </div>
    <div class="ModuleFooter"></div>
  </div>
</div>
"""

DETAIL_MOVIE_HTML = """\
<h1>Navigation</h1>
<div class="ModuleHead mHead">
  <div class="Opt leftOpt Headlne">
    <h1>
      <span style="display: inline-block">Batman Begins</span>
      <span class="Year">(2005)</span>
    </h1>
  </div>
</div>
<ul id="HosterList" class="Sortable">
  <li id="Hoster_92" class="MirBtn" rel="Batman_Begins&amp;Hoster=92">
    <div class="Named">Voe.SX</div>
    <div class="Data"><b>Mirror</b>: 1/1</div>
  </li>
  <li id="Hoster_104" class="MirBtn" rel="Batman_Begins&amp;Hoster=104">
    <div class="Named">Vinovo.to</div>
    <div class="Data"><b>Mirror</b>: 1/1</div>
  </li>
</ul>
"""

DETAIL_SERIES_HTML = """\
<div class="ModuleHead mHead">
  <div class="Opt leftOpt Headlne">
    <h1>
      <span style="display: inline-block">Breaking Bad</span>
      <span class="Year">(2008)</span>
    </h1>
  </div>
</div>
<select id="SeasonSelection">
  <option value="1">Staffel 1</option>
  <option value="2">Staffel 2</option>
</select>
<ul id="HosterList" class="Sortable">
  <li id="Hoster_92" class="MirBtn" rel="Breaking_Bad&amp;Hoster=92">
    <div class="Named">Voe.SX</div>
    <div class="Data"><b>Mirror</b>: 1/1</div>
  </li>
</ul>
"""

EMPTY_SEARCH_HTML = """\
<div id="Vadda">
  <div class="ModuleHead mHead">
    <div class="Opt leftOpt Headlne"><h1>Keine Ergebnisse</h1></div>
  </div>
</div>
"""

MIRROR_AJAX_HTML_VOE = """\
<div id="IfrBox">
  <iframe src="https://voe.sx/e/abc123" width="100%" height="100%"></iframe>
</div>
"""

MIRROR_AJAX_HTML_FILEMOON = """\
<div id="IfrBox">
  <iframe src="https://filemoon.sx/e/def456" width="100%" height="100%"></iframe>
</div>
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_response(text: str) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    """Tests for _SearchResultParser."""

    def test_parses_multiple_results(self, kinox_mod):
        parser = kinox_mod._SearchResultParser()
        parser.feed(SEARCH_HTML)

        assert len(parser.results) == 2

    def test_first_result_fields(self, kinox_mod):
        parser = kinox_mod._SearchResultParser()
        parser.feed(SEARCH_HTML)

        r = parser.results[0]
        assert r["title"] == "Batman Begins"
        assert r["url"] == "/Stream/Batman_Begins.html"
        assert "Action" in r["genre"]
        assert "Crime" in r["genre"]
        assert "8 / 10" in r["imdb"]

    def test_second_result_fields(self, kinox_mod):
        parser = kinox_mod._SearchResultParser()
        parser.feed(SEARCH_HTML)

        r = parser.results[1]
        assert r["title"] == "The Batman"
        assert r["url"] == "/Stream/The_Batman-3.html"
        assert "Action" in r["genre"]
        assert "7.8 / 10" in r["imdb"]

    def test_empty_search(self, kinox_mod):
        parser = kinox_mod._SearchResultParser()
        parser.feed(EMPTY_SEARCH_HTML)

        assert len(parser.results) == 0

    def test_no_html(self, kinox_mod):
        parser = kinox_mod._SearchResultParser()
        parser.feed("")

        assert len(parser.results) == 0


class TestDetailPageParser:
    """Tests for _DetailPageParser."""

    def test_movie_title_and_year(self, kinox_mod):
        parser = kinox_mod._DetailPageParser()
        parser.feed(DETAIL_MOVIE_HTML)

        assert parser.title == "Batman Begins"
        assert parser.year == "2005"
        assert parser.is_series is False

    def test_movie_hosters(self, kinox_mod):
        parser = kinox_mod._DetailPageParser()
        parser.feed(DETAIL_MOVIE_HTML)

        assert len(parser.hosters) == 2
        assert parser.hosters[0] == {"name": "Voe.SX", "id": "92"}
        assert parser.hosters[1] == {"name": "Vinovo.to", "id": "104"}

    def test_series_detected(self, kinox_mod):
        parser = kinox_mod._DetailPageParser()
        parser.feed(DETAIL_SERIES_HTML)

        assert parser.title == "Breaking Bad"
        assert parser.year == "2008"
        assert parser.is_series is True
        assert len(parser.hosters) == 1

    def test_skips_navigation_h1(self, kinox_mod):
        """The first <h1>Navigation</h1> must not become the title."""
        parser = kinox_mod._DetailPageParser()
        parser.feed(DETAIL_MOVIE_HTML)

        assert parser.title == "Batman Begins"

    def test_empty_page(self, kinox_mod):
        parser = kinox_mod._DetailPageParser()
        parser.feed("")

        assert parser.title == ""
        assert parser.year == ""
        assert parser.hosters == []
        assert parser.is_series is False


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    """Tests for plugin class attributes."""

    def test_name(self, kinox_mod):
        assert kinox_mod.plugin.name == "kinox"

    def test_version(self, kinox_mod):
        assert kinox_mod.plugin.version == "1.0.0"

    def test_mode(self, kinox_mod):
        assert kinox_mod.plugin.mode == "httpx"


# ---------------------------------------------------------------------------
# Plugin search tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestPluginSearch:
    """Tests for KinoxPlugin.search() with mocked HTTP."""

    @pytest.fixture()
    def mock_client(self):
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture()
    def plugin(self, kinox_mod, mock_client):
        p = kinox_mod.KinoxPlugin()
        p._client = mock_client
        p._domain_verified = True
        p.base_url = "https://www22.kinox.to"
        return p

    @pytest.mark.asyncio
    async def test_search_returns_results(self, plugin, mock_client):
        search_resp = _make_response(SEARCH_HTML)
        detail_resp = _make_response(DETAIL_MOVIE_HTML)
        mirror_voe_resp = _make_response(MIRROR_AJAX_HTML_VOE)
        mirror_filemoon_resp = _make_response(MIRROR_AJAX_HTML_FILEMOON)

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "Search.html" in url_str:
                return search_resp
            if "/aGET/Mirror/" in url_str:
                if "Hoster=92" in url_str:
                    return mirror_voe_resp
                return mirror_filemoon_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman")

        assert len(results) == 2
        assert all(r.download_link for r in results)
        assert all(r.published_date == "2005" for r in results)
        assert all(r.category == 2000 for r in results)

    @pytest.mark.asyncio
    async def test_search_empty_query(self, plugin):
        results = await plugin.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_rejected_category(self, plugin):
        # Music category (3000) not supported
        results = await plugin.search("test", category=3000)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_movie_category_accepted(self, plugin, mock_client):
        search_resp = _make_response(SEARCH_HTML)
        detail_resp = _make_response(DETAIL_MOVIE_HTML)
        mirror_resp = _make_response(MIRROR_AJAX_HTML_VOE)

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "Search.html" in url_str:
                return search_resp
            if "/aGET/Mirror/" in url_str:
                return mirror_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman", category=2000)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_search_tv_category_filters_movies(self, plugin, mock_client):
        search_resp = _make_response(SEARCH_HTML)
        detail_resp = _make_response(DETAIL_MOVIE_HTML)
        mirror_resp = _make_response(MIRROR_AJAX_HTML_VOE)

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "Search.html" in url_str:
                return search_resp
            if "/aGET/Mirror/" in url_str:
                return mirror_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        # Asking for TV (5000) but all results are movies (2000)
        results = await plugin.search("batman", category=5000)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_search_series_results(self, plugin, mock_client):
        search_resp = _make_response(SEARCH_HTML)
        detail_resp = _make_response(DETAIL_SERIES_HTML)
        mirror_resp = _make_response(MIRROR_AJAX_HTML_VOE)

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "Search.html" in url_str:
                return search_resp
            if "/aGET/Mirror/" in url_str:
                return mirror_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("breaking bad", category=5000)

        assert len(results) == 2
        assert all(r.category == 5000 for r in results)

    @pytest.mark.asyncio
    async def test_search_no_results(self, plugin, mock_client):
        search_resp = _make_response(EMPTY_SEARCH_HTML)
        mock_client.get = AsyncMock(return_value=search_resp)

        results = await plugin.search("xyznonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self, plugin, mock_client):
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        results = await plugin.search("batman")

        assert results == []

    @pytest.mark.asyncio
    async def test_detail_page_failure_falls_back(self, plugin, mock_client):
        """When a detail page fails, use search entry title instead."""
        search_resp = _make_response(SEARCH_HTML)
        empty_detail = _make_response("")

        async def mock_get(url, **kwargs):
            if "Search.html" in str(url):
                return search_resp
            return empty_detail

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman")

        assert len(results) == 2
        # Fallback to search entry title (no year from detail)
        assert results[0].title == "Batman Begins"
        assert results[0].published_date is None

    @pytest.mark.asyncio
    async def test_search_fetches_mirror_urls(self, plugin, mock_client):
        """AJAX mirror calls should produce download_links with hoster URLs."""
        search_resp = _make_response(SEARCH_HTML)
        detail_resp = _make_response(DETAIL_MOVIE_HTML)

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "Search.html" in url_str:
                return search_resp
            if "/aGET/Mirror/" in url_str:
                if "Hoster=92" in url_str:
                    return _make_response(MIRROR_AJAX_HTML_VOE)
                return _make_response(MIRROR_AJAX_HTML_FILEMOON)
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman")

        assert len(results) == 2
        # Each result should have download_links from mirror AJAX
        r = results[0]
        assert r.download_links is not None
        assert len(r.download_links) == 2
        # Hoster 92 = Voe.SX, Hoster 104 = Vinovo.to
        links = {dl["hoster"]: dl["link"] for dl in r.download_links}
        assert links["Voe.SX"] == "https://voe.sx/e/abc123"
        assert links["Vinovo.to"] == "https://filemoon.sx/e/def456"
        # download_link should be the first link
        assert r.download_link == "https://voe.sx/e/abc123"

    @pytest.mark.asyncio
    async def test_mirror_url_failure_graceful(self, plugin, mock_client):
        """When all AJAX mirror calls fail, result still has source_url."""
        search_resp = _make_response(SEARCH_HTML)
        detail_resp = _make_response(DETAIL_MOVIE_HTML)
        error_resp = MagicMock(spec=httpx.Response)
        error_resp.status_code = 404

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "Search.html" in url_str:
                return search_resp
            if "/aGET/Mirror/" in url_str:
                return error_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman")

        assert len(results) == 2
        # No download_links since all mirrors failed
        assert results[0].download_links is None
        # Fallback to source_url
        assert "kinox.to" in results[0].download_link

    @pytest.mark.asyncio
    async def test_mirror_iframe_parsing(self, plugin, mock_client, kinox_mod):
        """Test iframe src extraction from different HTML formats."""
        p = kinox_mod.KinoxPlugin()
        p._client = mock_client
        p._domain_verified = True
        p.base_url = "https://www22.kinox.to"

        # Test with single-quoted iframe
        single_quote_html = "<div><iframe src='https://voe.sx/e/test'></iframe></div>"
        mock_client.get = AsyncMock(return_value=_make_response(single_quote_html))
        url = await p._fetch_mirror_url("Test_Movie", "92")
        assert url == "https://voe.sx/e/test"

        # Test with double-quoted iframe
        double_quote_html = (
            '<div><iframe src="https://filemoon.sx/e/test"></iframe></div>'
        )
        mock_client.get = AsyncMock(return_value=_make_response(double_quote_html))
        url = await p._fetch_mirror_url("Test_Movie", "104")
        assert url == "https://filemoon.sx/e/test"


# ---------------------------------------------------------------------------
# Domain verification tests
# ---------------------------------------------------------------------------


class TestDomainVerification:
    """Tests for domain fallback logic."""

    @pytest.mark.asyncio
    async def test_first_domain_works(self, kinox_mod):
        p = kinox_mod.KinoxPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.url = httpx.URL("https://www22.kinox.to/")
        mock_client.head = AsyncMock(return_value=resp)
        p._client = mock_client

        await p._verify_domain()

        assert p._domain_verified is True
        assert "kinox.to" in p.base_url

    @pytest.mark.asyncio
    async def test_fallback_on_connect_error(self, kinox_mod):
        p = kinox_mod.KinoxPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        call_count = 0

        async def mock_head(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("Connection failed")
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            resp.url = httpx.URL("https://ww22.kinox.to/")
            return resp

        mock_client.head = AsyncMock(side_effect=mock_head)
        p._client = mock_client

        await p._verify_domain()

        assert p._domain_verified is True
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_domains_fail(self, kinox_mod):
        p = kinox_mod.KinoxPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(
            side_effect=httpx.ConnectError("Connection failed")
        )
        p._client = mock_client

        await p._verify_domain()

        assert p._domain_verified is True
        assert kinox_mod.KinoxPlugin._domains[0] in p.base_url

    @pytest.mark.asyncio
    async def test_skips_if_already_verified(self, kinox_mod):
        p = kinox_mod.KinoxPlugin()
        p._domain_verified = True
        p.base_url = "https://custom.domain"
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        p._client = mock_client

        await p._verify_domain()

        mock_client.head.assert_not_called()
        assert p.base_url == "https://custom.domain"


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestCleanup:
    """Tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self, kinox_mod):
        p = kinox_mod.KinoxPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        p._client = mock_client

        await p.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert p._client is None

    @pytest.mark.asyncio
    async def test_cleanup_without_client(self, kinox_mod):
        p = kinox_mod.KinoxPlugin()

        await p.cleanup()  # Should not raise
