"""Tests for the kinoking.cc Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "kinoking.py"


def _load_module() -> ModuleType:
    """Load kinoking.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("kinoking_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once at module level for parser tests
_mod = _load_module()
_KinokingPlugin = _mod.KinokingPlugin
_SearchCardParser = _mod._SearchCardParser
_MovieDetailParser = _mod._MovieDetailParser
_SeriesDetailParser = _mod._SeriesDetailParser
_DOMAINS = _KinokingPlugin._domains
_GENRE_CATEGORY_MAP = _mod._GENRE_CATEGORY_MAP
_genre_to_torznab = _mod._genre_to_torznab
_determine_category = _mod._determine_category


def _make_plugin() -> object:
    """Create KinokingPlugin instance."""
    return _KinokingPlugin()


def _mock_response(
    text: str = "",
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

_SEARCH_HTML = """\
<html><body>
<div class="row-header">
  <h2 class="row-title"><span class="row-emoji">🔍</span>\
Suchergebnisse für "batman"</h2>
</div>
<div class="content-grid">
  <div class="content-card" onclick="playMovie(25656)">
    <img class="card-poster" alt="Batman: The Dark Knight Returns, Teil 2">
    <div class="content-type-badge badge-movie">Film</div>
    <div class="quality-badge quality-hd">HD</div>
    <div class="card-title">Batman: The Dark Knight Returns, Teil 2</div>
    <div class="card-rating"><i class="fas fa-star"></i>7.9</div>
  </div>
  <div class="content-card" onclick="playContent(2098, 'tv', 7515)">
    <img class="card-poster" alt="Batman">
    <div class="content-type-badge badge-series">Serie</div>
    <div class="quality-badge quality-hd">HD</div>
    <div class="card-title">Batman</div>
    <div class="card-rating"><i class="fas fa-star"></i>8.6</div>
  </div>
  <div class="content-card" onclick="playMovie(9456)">
    <img class="card-poster" alt="Batman Begins">
    <div class="content-type-badge badge-movie">Film</div>
    <div class="quality-badge quality-hd">HD</div>
    <div class="card-title">Batman Begins</div>
    <div class="card-rating"><i class="fas fa-star"></i>7.7</div>
  </div>
</div>
</body></html>
"""

_MOVIE_DETAIL_HTML = """\
<html><body>
<div class="movie-container">
  <div class="movie-player">
    <iframe src="https://voe.sx/e/pbqlhye4yta9"></iframe>
  </div>
  <div class="movie-links">
    <h6>Verfügbare Server</h6>
    <div class="movie-link-grid">
      <a href="?id=9456&link=9414" class="movie-link-btn premium active">
        <span>Voe</span><span class="premium-badge">PREMIUM</span>
      </a>
      <a href="?id=9456&link=9415" class="movie-link-btn">
        <span>Vidhideplus</span>
      </a>
    </div>
  </div>
  <div class="movie-info">
    <div class="movie-details">
      <h1>Batman Begins</h1>
      <div class="genre-badges">
        <span class="genre-badge genre-action">Action</span>
        <span class="genre-badge genre-crime">Krimi</span>
        <span class="genre-badge genre-drama">Drama</span>
      </div>
    </div>
  </div>
</div>
</body></html>
"""

_MOVIE_ALT_LINK_HTML = """\
<html><body>
<div class="movie-player">
  <iframe src="https://vidhideplus.com/file/yocl7dbwoner"></iframe>
</div>
</body></html>
"""

_SERIES_DETAIL_HTML = """\
<html><body>
<h1>Batman</h1>
<div class="genre-badges">
  <span class="genre-badge genre-action">Action &amp; Adventure</span>
  <span class="genre-badge genre-animation">Animation</span>
  <span class="genre-badge genre-drama">Drama</span>
</div>
<div class="season-links">
  <a href="?id=7515&season=1">Staffel 1 65 Episoden</a>
  <a href="?id=7515&season=2">Staffel 2 15 Episoden</a>
</div>
<div class="content-grid">
  <div class="content-card" onclick="playEpisode(242886, 'Ep Title 1')">
    <div class="episode-badge">E1</div>
    <h3>Gefährliche Klauen - Teil 1</h3>
  </div>
  <div class="content-card" onclick="playEpisode(242887, 'Ep Title 2')">
    <div class="episode-badge">E2</div>
    <h3>Auf mächtigen Schwingen</h3>
  </div>
</div>
</body></html>
"""

_EPISODE_API_RESPONSE = json.dumps(
    {
        "current": {
            "id": 242886,
            "name": "Gefährliche Klauen - Teil 1",
            "season": 1,
            "episode": 1,
            "series_name": "Batman",
        },
        "previous": None,
        "next": {"id": 242887, "name": "Auf mächtigen Schwingen", "episode": 2},
        "links": ["https://voe.sx/e/8rpr3n9rkkoo"],
    }
)

_EPISODE_API_RESPONSE_2 = json.dumps(
    {
        "current": {
            "id": 242887,
            "name": "Auf mächtigen Schwingen",
            "season": 1,
            "episode": 2,
            "series_name": "Batman",
        },
        "previous": {"id": 242886, "name": "Gefährliche Klauen", "episode": 1},
        "next": None,
        "links": ["https://voe.sx/e/abc123", "https://vidhideplus.com/file/xyz"],
    }
)

_EMPTY_SEARCH_HTML = """\
<html><body>
<div class="row-header">
  <h2 class="row-title">Suchergebnisse</h2>
</div>
<div class="content-grid"></div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Plugin attributes
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name_attribute(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "kinoking"

    def test_version_attribute(self) -> None:
        plugin = _make_plugin()
        assert plugin.version == "1.0.0"

    def test_mode_attribute(self) -> None:
        plugin = _make_plugin()
        assert plugin.mode == "httpx"

    def test_domains_list(self) -> None:
        assert "kinoking.cc" in _DOMAINS
        assert _DOMAINS[0] == "kinoking.cc"


# ---------------------------------------------------------------------------
# Domain verification
# ---------------------------------------------------------------------------


class TestDomainVerification:
    @pytest.mark.asyncio
    async def test_first_domain_reachable(self) -> None:
        plugin = _make_plugin()

        head_resp = MagicMock()
        head_resp.status_code = 200

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=head_resp)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "kinoking.cc" in plugin.base_url

    @pytest.mark.asyncio
    async def test_all_domains_fail(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "kinoking.cc" in plugin.base_url  # Fallback to primary

    @pytest.mark.asyncio
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
# _SearchCardParser
# ---------------------------------------------------------------------------


class TestSearchCardParser:
    def test_parses_movie_cards(self) -> None:
        parser = _SearchCardParser()
        parser.feed(_SEARCH_HTML)

        movies = [r for r in parser.results if r["type"] == "movie"]
        assert len(movies) == 2

        assert movies[0]["title"] == "Batman: The Dark Knight Returns, Teil 2"
        assert movies[0]["id"] == "25656"

        assert movies[1]["title"] == "Batman Begins"
        assert movies[1]["id"] == "9456"

    def test_parses_series_cards(self) -> None:
        parser = _SearchCardParser()
        parser.feed(_SEARCH_HTML)

        series = [r for r in parser.results if r["type"] == "series"]
        assert len(series) == 1
        assert series[0]["title"] == "Batman"
        assert series[0]["id"] == "7515"

    def test_parses_badge_text(self) -> None:
        parser = _SearchCardParser()
        parser.feed(_SEARCH_HTML)

        movies = [r for r in parser.results if r["type"] == "movie"]
        assert movies[0]["badge"] == "film"

        series = [r for r in parser.results if r["type"] == "series"]
        assert series[0]["badge"] == "serie"

    def test_parses_rating(self) -> None:
        parser = _SearchCardParser()
        parser.feed(_SEARCH_HTML)

        assert parser.results[0]["rating"] == "7.9"

    def test_empty_search(self) -> None:
        parser = _SearchCardParser()
        parser.feed(_EMPTY_SEARCH_HTML)

        assert len(parser.results) == 0

    def test_no_html(self) -> None:
        parser = _SearchCardParser()
        parser.feed("")

        assert len(parser.results) == 0

    def test_card_without_onclick(self) -> None:
        html = '<div class="content-card"><div class="card-title">X</div></div>'
        parser = _SearchCardParser()
        parser.feed(html)

        assert len(parser.results) == 0


# ---------------------------------------------------------------------------
# _MovieDetailParser
# ---------------------------------------------------------------------------


class TestMovieDetailParser:
    def test_parses_iframe_src(self) -> None:
        parser = _MovieDetailParser()
        parser.feed(_MOVIE_DETAIL_HTML)

        assert parser.iframe_src == "https://voe.sx/e/pbqlhye4yta9"

    def test_parses_title(self) -> None:
        parser = _MovieDetailParser()
        parser.feed(_MOVIE_DETAIL_HTML)

        assert parser.title == "Batman Begins"

    def test_parses_genres(self) -> None:
        parser = _MovieDetailParser()
        parser.feed(_MOVIE_DETAIL_HTML)

        assert "Action" in parser.genres
        assert "Krimi" in parser.genres
        assert "Drama" in parser.genres

    def test_parses_server_links(self) -> None:
        parser = _MovieDetailParser()
        parser.feed(_MOVIE_DETAIL_HTML)

        assert len(parser.server_links) == 2
        assert parser.server_links[0]["name"] == "Voe"
        assert "link=9414" in parser.server_links[0]["href"]
        assert parser.server_links[1]["name"] == "Vidhideplus"
        assert "link=9415" in parser.server_links[1]["href"]

    def test_empty_html(self) -> None:
        parser = _MovieDetailParser()
        parser.feed("<html><body></body></html>")

        assert parser.iframe_src == ""
        assert parser.title == ""
        assert parser.genres == []
        assert parser.server_links == []

    def test_alt_link_html(self) -> None:
        parser = _MovieDetailParser()
        parser.feed(_MOVIE_ALT_LINK_HTML)

        assert parser.iframe_src == "https://vidhideplus.com/file/yocl7dbwoner"


# ---------------------------------------------------------------------------
# _SeriesDetailParser
# ---------------------------------------------------------------------------


class TestSeriesDetailParser:
    def test_parses_title(self) -> None:
        parser = _SeriesDetailParser()
        parser.feed(_SERIES_DETAIL_HTML)

        assert parser.title == "Batman"

    def test_parses_genres(self) -> None:
        parser = _SeriesDetailParser()
        parser.feed(_SERIES_DETAIL_HTML)

        assert "Action & Adventure" in parser.genres
        assert "Animation" in parser.genres
        assert "Drama" in parser.genres

    def test_parses_seasons(self) -> None:
        parser = _SeriesDetailParser()
        parser.feed(_SERIES_DETAIL_HTML)

        assert len(parser.seasons) == 2
        assert parser.seasons[0]["number"] == "1"
        assert parser.seasons[1]["number"] == "2"

    def test_parses_episodes(self) -> None:
        parser = _SeriesDetailParser()
        parser.feed(_SERIES_DETAIL_HTML)

        assert len(parser.episodes) == 2
        assert parser.episodes[0]["episode_id"] == "242886"
        assert parser.episodes[0]["title"] == "Gefährliche Klauen - Teil 1"
        assert parser.episodes[1]["episode_id"] == "242887"
        assert parser.episodes[1]["title"] == "Auf mächtigen Schwingen"

    def test_empty_html(self) -> None:
        parser = _SeriesDetailParser()
        parser.feed("<html><body></body></html>")

        assert parser.title == ""
        assert parser.genres == []
        assert parser.seasons == []
        assert parser.episodes == []


# ---------------------------------------------------------------------------
# Genre → Torznab category mapping
# ---------------------------------------------------------------------------


class TestCategoryMapping:
    def test_movie_action(self) -> None:
        assert _genre_to_torznab("Action", is_series=False) == 2000

    def test_series_action(self) -> None:
        assert _genre_to_torznab("Action", is_series=True) == 5000

    def test_anime_always_5070(self) -> None:
        assert _genre_to_torznab("Anime", is_series=False) == 5070
        assert _genre_to_torznab("Anime", is_series=True) == 5070

    def test_animation_always_5070(self) -> None:
        assert _genre_to_torznab("Animation", is_series=False) == 5070
        assert _genre_to_torznab("Animation", is_series=True) == 5070

    def test_documentary(self) -> None:
        assert _genre_to_torznab("Dokumentarfilm", is_series=False) == 5080
        assert _genre_to_torznab("Dokumentation", is_series=True) == 5080

    def test_unknown_genre_movie(self) -> None:
        assert _genre_to_torznab("Unbekannt", is_series=False) == 2000

    def test_unknown_genre_series(self) -> None:
        assert _genre_to_torznab("Unbekannt", is_series=True) == 5000

    def test_determine_category_with_override(self) -> None:
        assert _determine_category(["Action"], False, 5070) == 5070

    def test_determine_category_anime_genre(self) -> None:
        assert _determine_category(["Anime", "Action"], True, None) == 5070

    def test_determine_category_default_movie(self) -> None:
        assert _determine_category(["Action"], False, None) == 2000

    def test_determine_category_default_series(self) -> None:
        assert _determine_category(["Action"], True, None) == 5000


# ---------------------------------------------------------------------------
# search() integration
# ---------------------------------------------------------------------------


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_returns_movie_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://kinoking.cc"

        search_resp = _mock_response(text=_SEARCH_HTML)
        movie_detail_resp = _mock_response(text=_MOVIE_DETAIL_HTML)
        movie_alt_resp = _mock_response(text=_MOVIE_ALT_LINK_HTML)
        series_detail_resp = _mock_response(text=_SERIES_DETAIL_HTML)
        ep_api_resp_1 = _mock_response(text=_EPISODE_API_RESPONSE)
        ep_api_resp_2 = _mock_response(text=_EPISODE_API_RESPONSE_2)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                search_resp,  # search
                movie_detail_resp,  # movie 25656 detail
                movie_detail_resp,  # movie 9456 detail
                movie_alt_resp,  # movie 25656 link 9414
                movie_alt_resp,  # movie 25656 link 9415
                movie_alt_resp,  # movie 9456 link 9414
                movie_alt_resp,  # movie 9456 link 9415
                series_detail_resp,  # series 7515 detail
                ep_api_resp_1,  # episode 242886 API
                ep_api_resp_2,  # episode 242887 API
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert len(results) > 0
        # Should have both movies and series results
        movie_results = [r for r in results if r.category < 5000]
        series_results = [r for r in results if r.category >= 5000]
        assert len(movie_results) > 0 or len(series_results) > 0

    @pytest.mark.asyncio
    async def test_search_empty_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://kinoking.cc"

        search_resp = _mock_response(text=_EMPTY_SEARCH_HTML)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=search_resp)
        plugin._client = mock_client

        results = await plugin.search("nonexistent_xyz")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_movie_category_filter(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://kinoking.cc"

        search_resp = _mock_response(text=_SEARCH_HTML)
        movie_detail_resp = _mock_response(text=_MOVIE_DETAIL_HTML)
        movie_alt_resp = _mock_response(text=_MOVIE_ALT_LINK_HTML)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                search_resp,  # search
                movie_detail_resp,  # movie detail
                movie_detail_resp,  # movie detail
                movie_alt_resp,  # alt link
                movie_alt_resp,  # alt link
                movie_alt_resp,  # alt link
                movie_alt_resp,  # alt link
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman", category=2000)

        # Should only have movie results (category < 5000)
        for r in results:
            assert r.category == 2000

    @pytest.mark.asyncio
    async def test_search_network_failure(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://kinoking.cc"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        plugin._client = mock_client

        results = await plugin.search("test")

        assert results == []


# ---------------------------------------------------------------------------
# Episode API
# ---------------------------------------------------------------------------


class TestEpisodeApi:
    @pytest.mark.asyncio
    async def test_fetch_episode_links(self) -> None:
        plugin = _make_plugin()
        plugin.base_url = "https://kinoking.cc"

        ep_resp = _mock_response(text=_EPISODE_API_RESPONSE)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=ep_resp)
        plugin._client = mock_client

        links = await plugin._fetch_episode_links("242886")

        assert len(links) == 1
        assert links[0] == "https://voe.sx/e/8rpr3n9rkkoo"

    @pytest.mark.asyncio
    async def test_fetch_episode_links_multiple(self) -> None:
        plugin = _make_plugin()
        plugin.base_url = "https://kinoking.cc"

        ep_resp = _mock_response(text=_EPISODE_API_RESPONSE_2)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=ep_resp)
        plugin._client = mock_client

        links = await plugin._fetch_episode_links("242887")

        assert len(links) == 2
        assert "voe.sx" in links[0]
        assert "vidhideplus.com" in links[1]

    @pytest.mark.asyncio
    async def test_fetch_episode_links_failure(self) -> None:
        plugin = _make_plugin()
        plugin.base_url = "https://kinoking.cc"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("fail"))
        plugin._client = mock_client

        links = await plugin._fetch_episode_links("999999")

        assert links == []


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert plugin._client is None

    @pytest.mark.asyncio
    async def test_cleanup_noop_when_no_client(self) -> None:
        plugin = _make_plugin()
        assert plugin._client is None

        await plugin.cleanup()  # Should not raise
