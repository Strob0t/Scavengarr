"""Unit tests for the kinox.to plugin."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from scavengarr.domain.plugins.base import PluginUnreachableError

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
<table><tbody>
  <tr><td class="Label" nowrap>IMDb Wertung:</td><td class="Value">8.2 / 10</td></tr>
  <tr><td class="Label" nowrap>Genre:</td>
      <td class="Value"><a href="/Genre/Action">Action</a>
          <a href="/Genre/Crime">Crime</a> </td></tr>
</tbody></table>
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
    resp = MagicMock(spec=httpx.Response, history=[])
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

    def test_movie_genres(self, kinox_mod):
        parser = kinox_mod._DetailPageParser()
        parser.feed(DETAIL_MOVIE_HTML)

        assert parser.genres == ["Action", "Crime"]

    def test_genres_from_the_detail_list_without_the_table(self, kinox_mod):
        parser = kinox_mod._DetailPageParser()
        parser.feed(
            '<li class="DetailDat" title="Genre"><span class="Genre"></span>'
            "Drama, Thriller</li>"
        )

        assert parser.genres == ["Drama", "Thriller"]

    def test_no_genres_on_the_page(self, kinox_mod):
        parser = kinox_mod._DetailPageParser()
        parser.feed(DETAIL_SERIES_HTML)

        assert parser.genres == []

    def test_result_metadata_year_and_genres(self, kinox_mod):
        parser = kinox_mod._DetailPageParser()
        parser.feed(DETAIL_MOVIE_HTML)
        plugin = kinox_mod.KinoxPlugin()

        sr = plugin._build_search_result(
            {"title": "Batman Begins", "url": "/Stream/Batman_Begins.html"},
            parser,
            [{"hoster": "Voe.SX", "link": "https://voe.sx/e/abc123"}],
        )

        assert sr.title == "Batman Begins (2005)"
        assert sr.metadata == {"year": 2005, "genres": "Action, Crime"}

    def test_result_metadata_without_year_and_genres(self, kinox_mod):
        parser = kinox_mod._DetailPageParser()
        parser.feed("<h1><span>Unknown</span></h1>")
        plugin = kinox_mod.KinoxPlugin()

        sr = plugin._build_search_result({"title": "Unknown", "url": "/x"}, parser)

        assert sr.metadata == {"genres": ""}

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

    def test_year_of_the_title_not_of_related_entries(self, kinox_mod):
        """Related entries below the film carry Year spans too; the page's last
        one gave the film another year."""
        related = (
            '<div class="Grahpics"><h1><a href="/Stream/Batman_Forever.html">'
            'Batman Forever</a></h1><span class="Year">(1995)</span></div>\n'
        )
        parser = kinox_mod._DetailPageParser()
        parser.feed(DETAIL_MOVIE_HTML + related)

        assert parser.title == "Batman Begins"
        assert parser.year == "2005"

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
    async def test_scrapes_only_hits_matching_the_query(self, plugin, mock_client):
        # The site also lists loose matches ("Dark" finds 77 titles), each
        # costing a detail page and mirror requests: kinox ran into the
        # Stremio search budget on most requests
        detail_urls: list[str] = []

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "Search.html" in url_str:
                return _make_response(SEARCH_HTML)
            if "/aGET/Mirror/" in url_str:
                return _make_response(MIRROR_AJAX_HTML_VOE)
            detail_urls.append(url_str)
            return _make_response(DETAIL_MOVIE_HTML)

        mock_client.get = AsyncMock(side_effect=mock_get)

        await plugin.search("Batman Begins")

        assert detail_urls == ["https://www22.kinox.to/Stream/Batman_Begins.html"]

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
    async def test_episode_request_skips_series(self, plugin, mock_client):
        """kinox's mirror API serves the page's default episode; returning it
        for S03E05 would present the wrong episode as a match."""
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

        results = await plugin.search("breaking bad", season=3, episode=5)

        assert results == []
        mirror_calls = [
            c for c in mock_client.get.call_args_list if "/aGET/Mirror/" in str(c)
        ]
        assert mirror_calls == []

    @pytest.mark.asyncio
    async def test_episode_request_loads_nothing(self, plugin, mock_client):
        """No answer is possible (series give the default episode, films are
        another category), so an episode request costs no request at all: in
        the Stremio test kinox loaded "Dark Paradise", "Dark Harvest" and
        "Dark Hearts" with their mirrors for "Dark" S01E01."""
        mock_client.get = AsyncMock()

        results = await plugin.search("dark", category=5000, season=1, episode=1)

        assert results == []
        mock_client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mirror_requests_share_one_limit(self, plugin, mock_client):
        """Mirror AJAX calls of all entries share the plugin's concurrency."""
        search_resp = _make_response(SEARCH_HTML)
        detail_resp = _make_response(DETAIL_MOVIE_HTML)
        mirror_resp = _make_response(MIRROR_AJAX_HTML_VOE)
        active = 0
        peak = 0

        async def mock_get(url, **kwargs):
            nonlocal active, peak
            url_str = str(url)
            if "Search.html" in url_str:
                return search_resp
            if "/aGET/Mirror/" in url_str:
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1
                return mirror_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)
        plugin._max_concurrent = 1

        await plugin.search("batman")

        assert peak == 1

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
    async def test_detail_page_failure_gives_no_result(self, plugin, mock_client):
        """A failed detail page has no hosters: no link, so no result.

        The kinox page itself used to stand in as the "download" link.
        """
        search_resp = _make_response(SEARCH_HTML)
        empty_detail = _make_response("")

        async def mock_get(url, **kwargs):
            if "Search.html" in str(url):
                return search_resp
            return empty_detail

        mock_client.get = AsyncMock(side_effect=mock_get)

        assert await plugin.search("batman") == []

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
        """When all AJAX mirror calls fail, there is no result."""
        search_resp = _make_response(SEARCH_HTML)
        detail_resp = _make_response(DETAIL_MOVIE_HTML)
        error_resp = MagicMock(spec=httpx.Response, history=[])
        error_resp.status_code = 404
        error_resp.text = ""
        error_resp.headers = httpx.Headers()

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "Search.html" in url_str:
                return search_resp
            if "/aGET/Mirror/" in url_str:
                return error_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        # no hoster link left: the result is dropped, not pointed at kinox
        assert await plugin.search("batman") == []

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
        resp = MagicMock(spec=httpx.Response, history=[])
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
            resp = MagicMock(spec=httpx.Response, history=[])
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

        with pytest.raises(PluginUnreachableError):
            await p._verify_domain()

        assert p._domain_verified is False

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


# kinox answers the mirror AJAX with JSON; the iframe HTML inside is escaped
_JSON_MIRROR_ABSOLUTE = (
    '{"Stream":"<iframe src=\\"https:\\/\\/voe.sx\\/e\\/abc123\\"'
    ' width=\\"100%\\"><\\/iframe>","HosterName":"Voe.SX"}'
)
_JSON_MIRROR_REDIRECT = (
    '{"Stream":"<iframe src=\\"\\/redirect\\/3490d139?t=1790665650\\"\\"'
    ' referrerpolicy=\\"no-referrer\\"><\\/iframe>","HosterName":"Dood.to"}'
)


class TestJsonMirrorAnswer:
    @pytest.fixture()
    def plugin(self, kinox_mod):
        p = kinox_mod.KinoxPlugin()
        p._client = AsyncMock(spec=httpx.AsyncClient)
        p._domain_verified = True
        p.base_url = "https://www22.kinox.to"
        return p

    @pytest.mark.asyncio
    async def test_json_answer_absolute_iframe(self, plugin):
        plugin._client.get = AsyncMock(
            return_value=_make_response(_JSON_MIRROR_ABSOLUTE)
        )

        url = await plugin._fetch_mirror_url("Batman_Begins", "92")

        assert url == "https://voe.sx/e/abc123"

    @pytest.mark.asyncio
    async def test_json_answer_relative_redirect_is_absolutised(self, plugin):
        plugin._client.get = AsyncMock(
            return_value=_make_response(_JSON_MIRROR_REDIRECT)
        )

        url = await plugin._fetch_mirror_url("Oppenheimer", "95")

        assert url == "https://www22.kinox.to/redirect/3490d139?t=1790665650"

    @pytest.mark.asyncio
    async def test_redirect_link_resolved_to_hoster(self, plugin):
        detail = _make_response(DETAIL_MOVIE_HTML)
        mirror = _make_response(_JSON_MIRROR_REDIRECT)
        hop = MagicMock(spec=httpx.Response, history=[])
        hop.status_code = 302
        hop.is_redirect = True
        hop.headers = {"location": "https://dood.to/e/w71gg51eat6x"}

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "Search.html" in url_str:
                return _make_response(SEARCH_HTML)
            if "/aGET/Mirror/" in url_str:
                return mirror
            if "/redirect/" in url_str:
                return hop
            return detail

        plugin._client.get = AsyncMock(side_effect=mock_get)

        results = await plugin.search("batman")

        assert results
        assert results[0].download_link == "https://dood.to/e/w71gg51eat6x"
        assert all("kinox.to" not in dl["link"] for dl in results[0].download_links)

    @pytest.mark.asyncio
    async def test_unresolvable_redirect_is_dropped(self, plugin):
        """kinox's /redirect/ currently loops on a JS "Verifizierung" page."""
        detail = _make_response(DETAIL_MOVIE_HTML)
        mirror = _make_response(_JSON_MIRROR_REDIRECT)
        verification = _make_response("<title>Verifizierung</title>")
        verification.is_redirect = False
        verification.headers = {}

        async def mock_get(url, **kwargs):
            url_str = str(url)
            if "Search.html" in url_str:
                return _make_response(SEARCH_HTML)
            if "/aGET/Mirror/" in url_str:
                return mirror
            if "/redirect/" in url_str:
                return verification
            return detail

        plugin._client.get = AsyncMock(side_effect=mock_get)

        assert await plugin.search("batman") == []


class TestCloudflareFallback:
    """Pages go through the base helpers (plugin timeout, UA, browser)."""

    @pytest.fixture()
    def challenge(self):
        resp = MagicMock(spec=httpx.Response, history=[])
        resp.status_code = 403
        resp.text = "<title>Just a moment...</title>"
        resp.headers = httpx.Headers()
        resp.url = httpx.URL("https://www22.kinox.to/Search.html?q=batman")
        return resp

    @pytest.fixture()
    def plugin(self, kinox_mod, challenge, monkeypatch):
        from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

        fetcher = AsyncMock()
        monkeypatch.setattr(HttpxPluginBase, "_browser_fetcher", fetcher)
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        p = kinox_mod.KinoxPlugin()
        p._client = AsyncMock(spec=httpx.AsyncClient)
        p._client.get = AsyncMock(return_value=challenge)
        p._domain_verified = True
        p.base_url = "https://www22.kinox.to"
        return p, fetcher

    @pytest.mark.asyncio
    async def test_challenged_search_is_loaded_through_the_browser(self, plugin):
        p, fetcher = plugin
        fetcher.fetch_text = AsyncMock(return_value=SEARCH_HTML)

        results = await p._search_page("batman")

        assert len(results) == 2
        fetcher.fetch_text.assert_awaited_once()
        assert "q=batman" in fetcher.fetch_text.await_args.args[0]

    @pytest.mark.asyncio
    async def test_challenged_mirror_answer_is_loaded_through_the_browser(self, plugin):
        p, fetcher = plugin
        fetcher.fetch_text = AsyncMock(return_value=_JSON_MIRROR_ABSOLUTE)

        url = await p._fetch_mirror_url("Batman_Begins", "92")

        assert url == "https://voe.sx/e/abc123"
