"""Tests for the serienfans.org Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "serienfans.py"


def _load_serienfans_module() -> ModuleType:
    """Load serienfans.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location(
        "serienfans_plugin", str(_PLUGIN_PATH)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once at module level for parser tests
_serienfans = _load_serienfans_module()
_SerienfansPlugin = _serienfans.SerienfansPlugin
_ReleaseParser = _serienfans._ReleaseParser
_IndexPageParser = _serienfans._IndexPageParser


def _make_plugin() -> object:
    """Create SerienfansPlugin instance."""
    return _SerienfansPlugin()


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

# Season API HTML response with 2 releases and episode list
_SEASON_HTML = """
<input type="checkbox" id="se123">
<div class="entry">
  <div class="row">
    <div class="col">
      <h3>
        <label class="opn" for="se123"></label>
        Staffel 1
        <div class="complete"><span>Complete</span></div>
        <span class="morespec">1080p | 15.2 GB</span>
        <span class="audiotag"><img src="/images/DE.svg" />5.1</span>
        <span class="grouptag">HDSource</span>
      </h3>
      <small>Breaking.Bad.S01.German.DL.1080p.WEB.h264-HDSource</small>
    </div>
  </div>
  <div class="row">
    <a class="dlb row" target="_blank" href="/external/2/hash1abc">
      <i class="st"></i>
      <div class="col"><span>1fichier</span></div>
    </a>
    <a class="dlb row" target="_blank" href="/external/2/hash2def">
      <i class="st"></i>
      <div class="col"><span>rapidgator</span></div>
    </a>
  </div>
  <div class="list simple">
    <div class="row head">
      <div>Nr.</div>
      <div>Titel</div>
      <div>Download</div>
    </div>
    <div class="row">
      <div>1.</div>
      <div>Pilot</div>
      <div class="row">
        <a class="dlb row" target="_blank" href="/external/2/ep1hash1">
          <i class="st"></i>
          <div class="col"><span>1F</span></div>
        </a>
        <a class="dlb row" target="_blank" href="/external/2/ep1hash2">
          <i class="st"></i>
          <div class="col"><span>RG</span></div>
        </a>
      </div>
    </div>
    <div class="row">
      <div>2.</div>
      <div>Die Katze ist im Sack</div>
      <div class="row">
        <a class="dlb row" target="_blank" href="/external/2/ep2hash1">
          <i class="st"></i>
          <div class="col"><span>1F</span></div>
        </a>
      </div>
    </div>
  </div>
</div>
<input type="checkbox" id="se456">
<div class="entry">
  <div class="row">
    <div class="col">
      <h3>
        <label class="opn" for="se456"></label>
        Staffel 1
        <span class="morespec">720p | 8.1 GB</span>
        <span class="grouptag">SharpHD</span>
      </h3>
      <small>Breaking.Bad.S01.German.DL.720p.WEB.h264-SharpHD</small>
    </div>
  </div>
  <div class="row">
    <a class="dlb row" target="_blank" href="/external/2/hash3ghi">
      <i class="st"></i>
      <div class="col"><span>uploaded</span></div>
    </a>
  </div>
</div>
"""

# Season API JSON response
_SEASON_API_RESPONSE = {
    "qualitys": ["1080p", "720p"],
    "bubblesQuality": {},
    "bubblesLanguage": {},
    "languages": ["DE"],
    "html": _SEASON_HTML,
}

# Empty season HTML
_EMPTY_SEASON_HTML = """
<div class="entry">
  <div class="row">
    <div class="col">
      <h3>Keine Einträge vorhanden</h3>
    </div>
  </div>
</div>
"""

# Detail page HTML (server-rendered)
_DETAIL_PAGE_HTML = """
<html>
<head>
<meta property="og:description" content="A chemistry teacher turns to crime.">
</head>
<body>
<div class="content splitview">
  <h2>Breaking Bad <i>(2008)</i></h2>
  <div class="tags">
    <a class="genre" href="/genre/18">Drama</a>
    <a class="genre" href="/genre/80">Krimi</a>
  </div>
  <div>
    <div>
      <i class="cover"><img src="/media/123/200/300"></i>
      <ul class="info">
        <li><strong>Staffeln</strong>
        <span>5</span>
        </li>
        <li><strong>Laufzeit</strong>
        <span>45min</span>
        </li>
        <li>
          <i class="rating excellent">9.5</i>
          <strong>Bewertung</strong>
          <span><a href="https://www.imdb.com/title/tt0903747/" target="_new">IMDB</a></span>
        </li>
      </ul>
    </div>
  </div>
  <div class="description main">
    <select id="set12" onchange="initSeason('testSeriesId123abc', $(this).val(), '', 'ALL');">
      <option value="ALL">Alle Staffeln</option>
      <option value="1">Staffel 1</option>
      <option value="2">Staffel 2</option>
    </select>
    <script>
      if('1' != '') {
        initSeason('testSeriesId123abc', 1, '', 'ALL');
      }
    </script>
  </div>
</div>
</body></html>
"""

# Detail page without initSeason (broken page)
_DETAIL_NO_INIT = """
<html><body>
<h2>Broken Page <i>(2024)</i></h2>
</body></html>
"""

# Search API JSON response
_SEARCH_API_RESPONSE = {
    "result": [
        {
            "url_id": "breaking-bad",
            "year": 2008,
            "title": "Breaking Bad",
            "description": "A chemistry teacher turns to crime.",
            "image": [{"media_url": "/media/123/70/105"}],
        },
        {
            "url_id": "better-call-saul",
            "year": 2015,
            "title": "Better Call Saul",
            "description": "Prequel to Breaking Bad.",
            "image": [{"media_url": "/media/456/70/105"}],
        },
    ],
    "resultCounterPart": [
        {
            "url_id": "el-camino",
            "year": 2019,
            "title": "El Camino: Ein Breaking-Bad-Film",
        }
    ],
    "counterPartIs": "filmfans.org",
}

_EMPTY_SEARCH_RESPONSE = {
    "result": [],
    "resultCounterPart": [],
    "counterPartIs": "filmfans.org",
}

# Index page HTML
_INDEX_PAGE_HTML = """
<html><body>
<div class="list txt">
  <div>
    <a href="/breaking-bad"><strong>Breaking Bad</strong><small>(2008)</small></a>
    <a href="/better-call-saul"><strong>Better Call Saul</strong><small>(2015)</small></a>
    <a href="/bridgerton"><strong>Bridgerton</strong><small>(2020)</small></a>
  </div>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Release parser tests
# ---------------------------------------------------------------------------


class TestReleaseParser:
    def test_parses_two_releases(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(_SEASON_HTML)

        assert len(parser.releases) == 2

    def test_first_release_name(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(_SEASON_HTML)

        assert parser.releases[0]["release_name"] == (
            "Breaking.Bad.S01.German.DL.1080p.WEB.h264-HDSource"
        )

    def test_second_release_name(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(_SEASON_HTML)

        assert parser.releases[1]["release_name"] == (
            "Breaking.Bad.S01.German.DL.720p.WEB.h264-SharpHD"
        )

    def test_first_release_size(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(_SEASON_HTML)

        assert parser.releases[0]["size"] == "1080p | 15.2 GB"

    def test_second_release_size(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(_SEASON_HTML)

        assert parser.releases[1]["size"] == "720p | 8.1 GB"

    def test_first_release_download_links(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(_SEASON_HTML)

        links = parser.releases[0]["download_links"]
        assert len(links) == 2
        assert links[0]["hoster"] == "1fichier"
        assert links[0]["link"] == "https://serienfans.org/external/2/hash1abc"
        assert links[1]["hoster"] == "rapidgator"
        assert links[1]["link"] == "https://serienfans.org/external/2/hash2def"

    def test_second_release_single_link(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(_SEASON_HTML)

        links = parser.releases[1]["download_links"]
        assert len(links) == 1
        assert links[0]["hoster"] == "uploaded"

    def test_episodes_parsed(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(_SEASON_HTML)

        assert len(parser.episodes) == 2

    def test_first_episode_number(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(_SEASON_HTML)

        assert parser.episodes[0]["episode_num"] == "1"

    def test_first_episode_title(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(_SEASON_HTML)

        assert parser.episodes[0]["episode_title"] == "Pilot"

    def test_first_episode_links(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(_SEASON_HTML)

        links = parser.episodes[0]["download_links"]
        assert len(links) == 2
        assert links[0]["hoster"] == "1F"
        assert links[0]["link"] == "https://serienfans.org/external/2/ep1hash1"

    def test_second_episode(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(_SEASON_HTML)

        assert parser.episodes[1]["episode_num"] == "2"
        assert parser.episodes[1]["episode_title"] == "Die Katze ist im Sack"
        assert len(parser.episodes[1]["download_links"]) == 1

    def test_empty_html_returns_no_releases(self) -> None:
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed("<html><body></body></html>")

        assert parser.releases == []
        assert parser.episodes == []

    def test_entry_without_download_links_skipped(self) -> None:
        html = """
        <div class="entry">
          <div class="row">
            <div class="col">
              <small>Some.Release.Name</small>
            </div>
          </div>
        </div>
        """
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(html)

        assert parser.releases == []

    def test_relative_href_gets_base_url(self) -> None:
        html = """
        <div class="entry">
          <div class="row">
            <div class="col">
              <small>Test.Release</small>
            </div>
          </div>
          <div class="row">
            <a class="dlb row" href="/external/2/hashXYZ">
              <div class="col"><span>fikper</span></div>
            </a>
          </div>
        </div>
        """
        parser = _ReleaseParser("https://serienfans.org")
        parser.feed(html)

        assert parser.releases[0]["download_links"][0]["link"] == (
            "https://serienfans.org/external/2/hashXYZ"
        )


# ---------------------------------------------------------------------------
# Index page parser tests
# ---------------------------------------------------------------------------


class TestIndexPageParser:
    def test_parses_three_series(self) -> None:
        parser = _IndexPageParser()
        parser.feed(_INDEX_PAGE_HTML)

        assert len(parser.series) == 3

    def test_first_series(self) -> None:
        parser = _IndexPageParser()
        parser.feed(_INDEX_PAGE_HTML)

        assert parser.series[0]["url_id"] == "breaking-bad"
        assert parser.series[0]["title"] == "Breaking Bad"
        assert parser.series[0]["year"] == "2008"

    def test_second_series(self) -> None:
        parser = _IndexPageParser()
        parser.feed(_INDEX_PAGE_HTML)

        assert parser.series[1]["url_id"] == "better-call-saul"
        assert parser.series[1]["title"] == "Better Call Saul"
        assert parser.series[1]["year"] == "2015"

    def test_empty_html(self) -> None:
        parser = _IndexPageParser()
        parser.feed("<html><body></body></html>")

        assert parser.series == []

    def test_ignores_navigation_links(self) -> None:
        html = """
        <a href="/genre">Genre</a>
        <a href="/top10">Top 10</a>
        <a href="https://filmfans.org">FilmFans</a>
        """
        parser = _IndexPageParser()
        parser.feed(html)

        # These should be ignored (no strong/small structure)
        assert parser.series == []


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "serienfans"

    def test_version(self) -> None:
        plugin = _make_plugin()
        assert plugin.version == "1.0.0"

    def test_mode(self) -> None:
        plugin = _make_plugin()
        assert plugin.mode == "httpx"

    def test_provides(self) -> None:
        plugin = _make_plugin()
        assert plugin.provides == "download"

    def test_default_language(self) -> None:
        plugin = _make_plugin()
        assert plugin.default_language == "de"

    def test_base_url(self) -> None:
        plugin = _make_plugin()
        assert plugin.base_url == "https://serienfans.org"

    def test_domains(self) -> None:
        plugin = _make_plugin()
        assert plugin._domains == ["serienfans.org"]


# ---------------------------------------------------------------------------
# Search API tests
# ---------------------------------------------------------------------------


class TestSearchApi:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()

        # Mock search API response
        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = _SEARCH_API_RESPONSE
        search_response.raise_for_status = lambda: None

        # Mock detail page response
        detail_response = AsyncMock(spec=httpx.Response)
        detail_response.status_code = 200
        detail_response.text = _DETAIL_PAGE_HTML
        detail_response.raise_for_status = lambda: None

        # Mock season API response
        season_response = AsyncMock(spec=httpx.Response)
        season_response.status_code = 200
        season_response.json.return_value = _SEASON_API_RESPONSE
        season_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                search_response,
                detail_response,  # breaking-bad detail
                season_response,  # breaking-bad seasons
                detail_response,  # better-call-saul detail
                season_response,  # better-call-saul seasons
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("breaking bad")

        # 2 series × 2 releases each = 4 results
        assert len(results) == 4

    async def test_search_api_url(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = _EMPTY_SEARCH_RESPONSE
        search_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=search_response)
        plugin._client = mock_client

        await plugin.search("breaking bad")

        mock_client.get.assert_awaited_once_with(
            "https://serienfans.org/api/v2/search",
            params={"q": "breaking bad", "ql": "DE"},
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
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("unreachable")
        )
        plugin._client = mock_client

        results = await plugin.search("test")

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

        results = await plugin.search("test")

        assert results == []


# ---------------------------------------------------------------------------
# Category filtering tests
# ---------------------------------------------------------------------------


class TestCategoryFiltering:
    async def test_tv_category_accepted(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = _EMPTY_SEARCH_RESPONSE
        search_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=search_response)
        plugin._client = mock_client

        results = await plugin.search("test", category=5000)

        # Should proceed (search made)
        mock_client.get.assert_awaited_once()
        assert results == []

    async def test_movie_category_rejected(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        results = await plugin.search("test", category=2000)

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

        results = await plugin.search("test", category=None)

        mock_client.get.assert_awaited_once()
        assert results == []


# ---------------------------------------------------------------------------
# Search result construction tests
# ---------------------------------------------------------------------------


class TestSearchResultConstruction:
    async def test_result_has_release_name_as_title(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {
                    "url_id": "breaking-bad",
                    "year": 2008,
                    "title": "Breaking Bad",
                }
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        detail_response = AsyncMock(spec=httpx.Response)
        detail_response.status_code = 200
        detail_response.text = _DETAIL_PAGE_HTML
        detail_response.raise_for_status = lambda: None

        season_response = AsyncMock(spec=httpx.Response)
        season_response.status_code = 200
        season_response.json.return_value = _SEASON_API_RESPONSE
        season_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, detail_response, season_response]
        )
        plugin._client = mock_client

        results = await plugin.search("breaking bad")

        assert results[0].title == (
            "Breaking.Bad.S01.German.DL.1080p.WEB.h264-HDSource"
        )

    async def test_result_has_download_links(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {
                    "url_id": "breaking-bad",
                    "year": 2008,
                    "title": "Breaking Bad",
                }
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        detail_response = AsyncMock(spec=httpx.Response)
        detail_response.status_code = 200
        detail_response.text = _DETAIL_PAGE_HTML
        detail_response.raise_for_status = lambda: None

        season_response = AsyncMock(spec=httpx.Response)
        season_response.status_code = 200
        season_response.json.return_value = _SEASON_API_RESPONSE
        season_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, detail_response, season_response]
        )
        plugin._client = mock_client

        results = await plugin.search("breaking bad")

        assert results[0].download_links[0]["hoster"] == "1fichier"
        assert results[0].download_links[1]["hoster"] == "rapidgator"

    async def test_result_has_source_url(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {
                    "url_id": "breaking-bad",
                    "year": 2008,
                    "title": "Breaking Bad",
                }
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        detail_response = AsyncMock(spec=httpx.Response)
        detail_response.status_code = 200
        detail_response.text = _DETAIL_PAGE_HTML
        detail_response.raise_for_status = lambda: None

        season_response = AsyncMock(spec=httpx.Response)
        season_response.status_code = 200
        season_response.json.return_value = _SEASON_API_RESPONSE
        season_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, detail_response, season_response]
        )
        plugin._client = mock_client

        results = await plugin.search("breaking bad")

        assert results[0].source_url == "https://serienfans.org/breaking-bad"

    async def test_result_category_always_tv(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"url_id": "test", "year": 2024, "title": "Test"}
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        detail_response = AsyncMock(spec=httpx.Response)
        detail_response.status_code = 200
        detail_response.text = _DETAIL_PAGE_HTML
        detail_response.raise_for_status = lambda: None

        season_response = AsyncMock(spec=httpx.Response)
        season_response.status_code = 200
        season_response.json.return_value = _SEASON_API_RESPONSE
        season_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, detail_response, season_response]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        for r in results:
            assert r.category == 5000

    async def test_result_has_size(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"url_id": "test", "year": 2024, "title": "Test"}
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        detail_response = AsyncMock(spec=httpx.Response)
        detail_response.status_code = 200
        detail_response.text = _DETAIL_PAGE_HTML
        detail_response.raise_for_status = lambda: None

        season_response = AsyncMock(spec=httpx.Response)
        season_response.status_code = 200
        season_response.json.return_value = _SEASON_API_RESPONSE
        season_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, detail_response, season_response]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        assert results[0].size == "1080p | 15.2 GB"
        assert results[1].size == "720p | 8.1 GB"

    async def test_result_has_year_as_published_date(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"url_id": "test", "year": 2008, "title": "Test"}
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        detail_response = AsyncMock(spec=httpx.Response)
        detail_response.status_code = 200
        detail_response.text = _DETAIL_PAGE_HTML
        detail_response.raise_for_status = lambda: None

        season_response = AsyncMock(spec=httpx.Response)
        season_response.status_code = 200
        season_response.json.return_value = _SEASON_API_RESPONSE
        season_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, detail_response, season_response]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        assert results[0].published_date == "2008"


# ---------------------------------------------------------------------------
# Season filtering tests
# ---------------------------------------------------------------------------


class TestSeasonFiltering:
    async def test_season_param_passed_to_api(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"url_id": "test", "year": 2024, "title": "Test"}
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        detail_response = AsyncMock(spec=httpx.Response)
        detail_response.status_code = 200
        detail_response.text = _DETAIL_PAGE_HTML
        detail_response.raise_for_status = lambda: None

        season_response = AsyncMock(spec=httpx.Response)
        season_response.status_code = 200
        season_response.json.return_value = _SEASON_API_RESPONSE
        season_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, detail_response, season_response]
        )
        plugin._client = mock_client

        await plugin.search("test", season=3)

        # Third call should be season API with season=3
        season_call = mock_client.get.call_args_list[2]
        assert "/season/3" in season_call.args[0]

    async def test_no_season_fetches_all(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"url_id": "test", "year": 2024, "title": "Test"}
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        detail_response = AsyncMock(spec=httpx.Response)
        detail_response.status_code = 200
        detail_response.text = _DETAIL_PAGE_HTML
        detail_response.raise_for_status = lambda: None

        season_response = AsyncMock(spec=httpx.Response)
        season_response.status_code = 200
        season_response.json.return_value = _SEASON_API_RESPONSE
        season_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, detail_response, season_response]
        )
        plugin._client = mock_client

        await plugin.search("test")

        # Third call should be season API with season=ALL
        season_call = mock_client.get.call_args_list[2]
        assert "/season/ALL" in season_call.args[0]


# ---------------------------------------------------------------------------
# Detail page error handling tests
# ---------------------------------------------------------------------------


class TestDetailPageErrors:
    async def test_detail_error_skips_series(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"url_id": "good-series", "year": 2024, "title": "Good"},
                {"url_id": "bad-series", "year": 2024, "title": "Bad"},
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        detail_response = AsyncMock(spec=httpx.Response)
        detail_response.status_code = 200
        detail_response.text = _DETAIL_PAGE_HTML
        detail_response.raise_for_status = lambda: None

        season_response = AsyncMock(spec=httpx.Response)
        season_response.status_code = 200
        season_response.json.return_value = _SEASON_API_RESPONSE
        season_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                search_response,
                detail_response,  # good-series detail
                season_response,  # good-series seasons
                httpx.ConnectError("timeout"),  # bad-series detail fails
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        # Only good-series' 2 releases should be returned
        assert len(results) == 2

    async def test_detail_without_init_season_skipped(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"url_id": "broken", "year": 2024, "title": "Broken"}
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        detail_response = AsyncMock(spec=httpx.Response)
        detail_response.status_code = 200
        detail_response.text = _DETAIL_NO_INIT
        detail_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[search_response, detail_response]
        )
        plugin._client = mock_client

        results = await plugin.search("broken")

        assert results == []

    async def test_series_without_url_id_skipped(self) -> None:
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
        assert mock_client.get.await_count == 1

    async def test_season_api_error_returns_empty(self) -> None:
        plugin = _make_plugin()

        search_response = AsyncMock(spec=httpx.Response)
        search_response.status_code = 200
        search_response.json.return_value = {
            "result": [
                {"url_id": "test", "year": 2024, "title": "Test"}
            ],
            "resultCounterPart": [],
        }
        search_response.raise_for_status = lambda: None

        detail_response = AsyncMock(spec=httpx.Response)
        detail_response.status_code = 200
        detail_response.text = _DETAIL_PAGE_HTML
        detail_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                search_response,
                detail_response,
                httpx.ConnectError("season api down"),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        assert results == []


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

    async def test_cleanup_noop_without_client(self) -> None:
        plugin = _make_plugin()

        await plugin.cleanup()

        assert plugin._client is None
