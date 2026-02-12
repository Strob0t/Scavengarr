"""Tests for the filmfans.org Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "filmfans.py"


def _load_filmfans_module() -> ModuleType:
    """Load filmfans.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("filmfans_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once at module level for parser tests
_filmfans = _load_filmfans_module()
_FilmfansPlugin = _filmfans.FilmfansPlugin
_ReleaseParser = _filmfans._ReleaseParser


def _make_plugin() -> object:
    """Create FilmfansPlugin instance."""
    return _FilmfansPlugin()


# Sample movie page HTML matching filmfans.org structure
_MOVIE_PAGE_HTML = """
<html><body>
<h1>Batman Begins (2005)</h1>
<div class="entry">
  <div class="row">
    <div class="col">
      <label for="sec1">
        <h3>
          <label class="opn" for="sec1"></label>
          <span class="morespec">Batman.Begins.2005.German.2160p.x265-PaTrol</span>
        </h3>
        <span class="audiotag"><small>Auflösung:</small> 2160p</span>
        <span class="audiotag"><small>Größe:</small> 15.8 GB</span>
        <span class="audiotag"><small>Releasegruppe:</small> PaTrol</span>
      </label>
    </div>
  </div>
  <div class="row">
    <a class="dlb row" target="_blank" href="/external/abc123?_=1770805446764">
      <i class="st"></i>
      <div class="col"><span>ddownload</span></div>
    </a>
    <a class="dlb row" target="_blank" href="/external/def456?_=1770805446764">
      <i class="st"></i>
      <div class="col"><span>rapidgator</span></div>
    </a>
  </div>
</div>
<div class="entry">
  <div class="row">
    <div class="col">
      <label for="sec2">
        <h3>
          <label class="opn" for="sec2"></label>
          <span class="morespec">Batman.Begins.2005.German.1080p.x264-SharpHD</span>
        </h3>
        <span class="audiotag"><small>Auflösung:</small> 1080p</span>
        <span class="audiotag"><small>Größe:</small> 37.3 GB</span>
        <span class="audiotag"><small>Releasegruppe:</small> SharpHD</span>
      </label>
    </div>
  </div>
  <div class="row">
    <a class="dlb row" target="_blank" href="/external/ghi789?_=1770805446764">
      <i class="st"></i>
      <div class="col"><span>katfile</span></div>
    </a>
  </div>
</div>
</body></html>
"""

# Movie page HTML with initMovie() script (releases are JS-loaded, not inline)
_MOVIE_PAGE_WITH_INIT = """
<html><body>
<h1>Batman Begins (2005)</h1>
<div class="list" id="list">Lade ...</div>
<script>initMovie('testhash123', '', '', '', '', '');</script>
</body></html>
"""

# API v1 response containing the releases HTML
_API_V1_RESPONSE = {
    "qualitys": ["2160p", "1080p"],
    "languages": ["DE", "EN"],
    "html": _MOVIE_PAGE_HTML,
}

_EMPTY_MOVIE_HTML = "<html><body><h1>Not Found</h1></body></html>"

# Search API JSON response
_SEARCH_API_RESPONSE = {
    "result": [
        {
            "url_id": "batman-begins",
            "year": 2005,
            "title": "Batman Begins",
            "description": "After training with his mentor...",
            "image": [{"media_url": "/media/123/70/105"}],
        },
        {
            "url_id": "batman-hush",
            "year": 2019,
            "title": "Batman: Hush",
            "description": "A mysterious villain...",
            "image": [{"media_url": "/media/456/70/105"}],
        },
    ],
    "resultCounterPart": [
        {
            "url_id": "batman-animated",
            "year": 1992,
            "title": "Batman: The Animated Series",
        }
    ],
    "counterPartIs": "serienfans.org",
}

_EMPTY_SEARCH_RESPONSE = {
    "result": [],
    "resultCounterPart": [],
    "counterPartIs": "serienfans.org",
}


class TestReleaseParser:
    def test_parses_two_releases(self) -> None:
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(_MOVIE_PAGE_HTML)

        assert len(parser.releases) == 2

    def test_release_name_extracted(self) -> None:
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(_MOVIE_PAGE_HTML)

        assert parser.releases[0]["release_name"] == (
            "Batman.Begins.2005.German.2160p.x265-PaTrol"
        )

    def test_second_release_name(self) -> None:
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(_MOVIE_PAGE_HTML)

        assert parser.releases[1]["release_name"] == (
            "Batman.Begins.2005.German.1080p.x264-SharpHD"
        )

    def test_size_extracted(self) -> None:
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(_MOVIE_PAGE_HTML)

        assert parser.releases[0]["size"] == "15.8 GB"

    def test_second_release_size(self) -> None:
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(_MOVIE_PAGE_HTML)

        assert parser.releases[1]["size"] == "37.3 GB"

    def test_download_links_extracted(self) -> None:
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(_MOVIE_PAGE_HTML)

        links = parser.releases[0]["download_links"]
        assert len(links) == 2
        assert links[0]["hoster"] == "ddownload"
        assert links[0]["link"] == (
            "https://filmfans.org/external/abc123?_=1770805446764"
        )
        assert links[1]["hoster"] == "rapidgator"

    def test_single_download_link(self) -> None:
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(_MOVIE_PAGE_HTML)

        links = parser.releases[1]["download_links"]
        assert len(links) == 1
        assert links[0]["hoster"] == "katfile"

    def test_empty_html_returns_no_releases(self) -> None:
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(_EMPTY_MOVIE_HTML)

        assert parser.releases == []

    def test_entry_without_download_links_skipped(self) -> None:
        html = """
        <div class="entry">
          <div class="row">
            <div class="col">
              <label for="sec1">
                <h3><span class="morespec">Some.Release.Name</span></h3>
                <span class="audiotag"><small>Größe:</small> 5 GB</span>
              </label>
            </div>
          </div>
        </div>
        """
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(html)

        assert parser.releases == []

    def test_entry_without_release_name_skipped(self) -> None:
        html = """
        <div class="entry">
          <div class="row">
            <a class="dlb row" href="/external/abc123">
              <div class="col"><span>ddownload</span></div>
            </a>
          </div>
        </div>
        """
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(html)

        assert parser.releases == []

    def test_nested_divs_do_not_exit_entry_early(self) -> None:
        html = """
        <div class="entry">
          <div class="row">
            <div class="col">
              <label for="sec1">
                <h3><span class="morespec">Nested.Test.Release</span></h3>
              </label>
            </div>
          </div>
          <div class="row">
            <a class="dlb row" href="/external/xyz">
              <div class="col"><span>rapidgator</span></div>
            </a>
          </div>
        </div>
        """
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(html)

        assert len(parser.releases) == 1
        assert parser.releases[0]["release_name"] == "Nested.Test.Release"

    def test_relative_href_gets_base_url(self) -> None:
        html = """
        <div class="entry">
          <div class="row">
            <div class="col">
              <h3><span class="morespec">Test.Release</span></h3>
            </div>
          </div>
          <div class="row">
            <a class="dlb row" href="/external/hash123">
              <div class="col"><span>turbobit</span></div>
            </a>
          </div>
        </div>
        """
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(html)

        assert parser.releases[0]["download_links"][0]["link"] == (
            "https://filmfans.org/external/hash123"
        )

    def test_size_not_set_without_groesse_label(self) -> None:
        html = """
        <div class="entry">
          <div class="row">
            <div class="col">
              <h3><span class="morespec">Test.Release</span></h3>
              <span class="audiotag"><small>Auflösung:</small> 1080p</span>
            </div>
          </div>
          <div class="row">
            <a class="dlb row" href="/external/hash">
              <div class="col"><span>ddownload</span></div>
            </a>
          </div>
        </div>
        """
        parser = _ReleaseParser("https://filmfans.org")
        parser.feed(html)

        assert parser.releases[0]["size"] == ""


class TestPluginAttributes:
    def test_name(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "filmfans"

    def test_version(self) -> None:
        plugin = _make_plugin()
        assert plugin.version == "1.0.0"

    def test_mode(self) -> None:
        plugin = _make_plugin()
        assert plugin.mode == "httpx"


class TestSearchApi:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()

        # Mock search API response
        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = _SEARCH_API_RESPONSE
        search_response.raise_for_status = lambda: None

        # Mock movie page response (with initMovie script)
        movie_page_response = AsyncMock(spec=httpx.Response)
        movie_page_response.status_code = 200
        movie_page_response.text = _MOVIE_PAGE_WITH_INIT
        movie_page_response.raise_for_status = lambda: None

        # Mock API v1 response (release HTML)
        api_v1_response = AsyncMock(spec=httpx.Response)
        api_v1_response.status_code = 200
        api_v1_response.json.return_value = _API_V1_RESPONSE
        api_v1_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                search_response,
                movie_page_response,
                api_v1_response,
                movie_page_response,
                api_v1_response,
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")

        # 2 movies × 2 releases each = 4 results
        assert len(results) == 4
        # 1 search + 2 × (movie page + api v1) = 5
        assert mock_client.get.await_count == 5

    async def test_search_api_url(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = _EMPTY_SEARCH_RESPONSE
        search_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=search_response)
        plugin._client = mock_client

        await plugin.search("batman")

        mock_client.get.assert_awaited_once_with(
            "https://filmfans.org/api/v2/search",
            params={"q": "batman", "ql": "DE"},
        )

    async def test_search_empty_results(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = _EMPTY_SEARCH_RESPONSE
        search_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=search_response)
        plugin._client = mock_client

        results = await plugin.search("nonexistent")

        assert results == []

    async def test_search_api_error_returns_empty(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("unreachable"))
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert results == []

    async def test_search_invalid_json_returns_empty(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.side_effect = json.JSONDecodeError("err", "", 0)
        search_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=search_response)
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert results == []

    async def test_empty_query_returns_empty(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        results = await plugin.search("")

        assert results == []


class TestCategoryFiltering:
    async def test_movie_category_accepted(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = _EMPTY_SEARCH_RESPONSE
        search_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=search_response)
        plugin._client = mock_client

        results = await plugin.search("batman", category=2000)

        # Should proceed (search made), even if empty
        mock_client.get.assert_awaited_once()
        assert results == []

    async def test_tv_category_rejected(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        results = await plugin.search("batman", category=5000)

        assert results == []

    async def test_game_category_rejected(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        results = await plugin.search("batman", category=4000)

        assert results == []

    async def test_no_category_accepted(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = _EMPTY_SEARCH_RESPONSE
        search_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=search_response)
        plugin._client = mock_client

        results = await plugin.search("batman", category=None)

        mock_client.get.assert_awaited_once()
        assert results == []


class TestSearchResultConstruction:
    async def test_result_has_release_name_as_title(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"url_id": "batman-begins", "year": 2005, "title": "Batman Begins"}
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        movie_page_response = AsyncMock(spec=httpx.Response)
        movie_page_response.status_code = 200
        movie_page_response.text = _MOVIE_PAGE_WITH_INIT
        movie_page_response.raise_for_status = lambda: None

        api_v1_response = AsyncMock(spec=httpx.Response)
        api_v1_response.status_code = 200
        api_v1_response.json.return_value = _API_V1_RESPONSE
        api_v1_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, movie_page_response, api_v1_response]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert results[0].title == ("Batman.Begins.2005.German.2160p.x265-PaTrol")

    async def test_result_has_download_links(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"url_id": "batman-begins", "year": 2005, "title": "Batman Begins"}
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        movie_page_response = AsyncMock(spec=httpx.Response)
        movie_page_response.status_code = 200
        movie_page_response.text = _MOVIE_PAGE_WITH_INIT
        movie_page_response.raise_for_status = lambda: None

        api_v1_response = AsyncMock(spec=httpx.Response)
        api_v1_response.status_code = 200
        api_v1_response.json.return_value = _API_V1_RESPONSE
        api_v1_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, movie_page_response, api_v1_response]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert results[0].download_links[0]["hoster"] == "ddownload"
        assert results[0].download_links[1]["hoster"] == "rapidgator"

    async def test_result_has_source_url(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"url_id": "batman-begins", "year": 2005, "title": "Batman Begins"}
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        movie_page_response = AsyncMock(spec=httpx.Response)
        movie_page_response.status_code = 200
        movie_page_response.text = _MOVIE_PAGE_WITH_INIT
        movie_page_response.raise_for_status = lambda: None

        api_v1_response = AsyncMock(spec=httpx.Response)
        api_v1_response.status_code = 200
        api_v1_response.json.return_value = _API_V1_RESPONSE
        api_v1_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, movie_page_response, api_v1_response]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert results[0].source_url == "https://filmfans.org/batman-begins"

    async def test_result_category_always_movies(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [{"url_id": "test", "year": 2024, "title": "Test"}],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        movie_page_response = AsyncMock(spec=httpx.Response)
        movie_page_response.status_code = 200
        movie_page_response.text = _MOVIE_PAGE_WITH_INIT
        movie_page_response.raise_for_status = lambda: None

        api_v1_response = AsyncMock(spec=httpx.Response)
        api_v1_response.status_code = 200
        api_v1_response.json.return_value = _API_V1_RESPONSE
        api_v1_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, movie_page_response, api_v1_response]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        for r in results:
            assert r.category == 2000

    async def test_result_has_size(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [{"url_id": "test", "year": 2024, "title": "Test"}],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        movie_page_response = AsyncMock(spec=httpx.Response)
        movie_page_response.status_code = 200
        movie_page_response.text = _MOVIE_PAGE_WITH_INIT
        movie_page_response.raise_for_status = lambda: None

        api_v1_response = AsyncMock(spec=httpx.Response)
        api_v1_response.status_code = 200
        api_v1_response.json.return_value = _API_V1_RESPONSE
        api_v1_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, movie_page_response, api_v1_response]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        assert results[0].size == "15.8 GB"
        assert results[1].size == "37.3 GB"

    async def test_result_has_year_as_published_date(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [{"url_id": "test", "year": 2005, "title": "Test"}],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        movie_page_response = AsyncMock(spec=httpx.Response)
        movie_page_response.status_code = 200
        movie_page_response.text = _MOVIE_PAGE_WITH_INIT
        movie_page_response.raise_for_status = lambda: None

        api_v1_response = AsyncMock(spec=httpx.Response)
        api_v1_response.status_code = 200
        api_v1_response.json.return_value = _API_V1_RESPONSE
        api_v1_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, movie_page_response, api_v1_response]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        assert results[0].published_date == "2005"


class TestMoviePageErrors:
    async def test_movie_page_error_skips_movie(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"url_id": "good-movie", "year": 2024, "title": "Good"},
                {"url_id": "bad-movie", "year": 2024, "title": "Bad"},
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        # good-movie: movie page with initMovie script
        movie_page_response = AsyncMock(spec=httpx.Response)
        movie_page_response.status_code = 200
        movie_page_response.text = _MOVIE_PAGE_WITH_INIT
        movie_page_response.raise_for_status = lambda: None

        # good-movie: API v1 response with release HTML
        api_v1_response = AsyncMock(spec=httpx.Response)
        api_v1_response.status_code = 200
        api_v1_response.json.return_value = _API_V1_RESPONSE
        api_v1_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                search_response,
                movie_page_response,  # good-movie page
                api_v1_response,  # good-movie API v1
                httpx.ConnectError("timeout"),  # bad-movie page fails
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        # Only good-movie's 2 releases should be returned
        assert len(results) == 2

    async def test_movie_without_url_id_skipped(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"year": 2024, "title": "No URL ID"},
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=search_response)
        plugin._client = mock_client

        results = await plugin.search("test")

        assert results == []
        # Only search API call, no movie page fetch
        assert mock_client.get.await_count == 1


class TestCleanup:
    async def test_cleanup_closes_client(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert plugin._client is None

    async def test_cleanup_noop_without_client(self) -> None:
        plugin = _make_plugin()

        await plugin.cleanup()

        assert plugin._client is None
