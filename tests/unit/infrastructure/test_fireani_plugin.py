"""Tests for the fireani.me Python plugin (httpx JSON API-based)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "fireani.py"


def _load_module() -> ModuleType:
    """Load fireani.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location(
        "fireani_plugin", str(_PLUGIN_PATH)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_FireaniPlugin = _mod.FireaniPlugin
_build_stream_links = _mod._build_stream_links
_EXCLUDED_PLAYERS = _mod._EXCLUDED_PLAYERS
_LANG_LABELS = _mod._LANG_LABELS


def _make_plugin() -> object:
    """Create FireaniPlugin instance."""
    return _FireaniPlugin()


def _mock_response(
    body: str | dict | list = "",
    status_code: int = 200,
) -> httpx.Response:
    """Create a mock httpx.Response with given body."""
    if isinstance(body, (dict, list)):
        content = json.dumps(body).encode()
        headers = {"content-type": "application/json"}
    else:
        content = body.encode() if isinstance(body, str) else body
        headers = {"content-type": "text/html"}
    return httpx.Response(
        status_code=status_code,
        content=content,
        headers=headers,
        request=httpx.Request("GET", "https://fireani.me/test"),
    )


# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

_SEARCH_RESPONSE = {
    "data": [
        {
            "id": 1143,
            "slug": "naruto",
            "title": "Naruto",
            "alternate_titles": "火影忍者",
            "generes": ["Fighting-Shounen", "Action", "Abenteuer"],
            "imdb": "tt4907198",
            "tmdb": 46260,
            "tmdb_type": "tv",
            "desc": "Ein riesiges Fuchsmonster greift Konohagakure an.",
            "start": 2002,
            "end": 2007,
            "poster": "abc.png",
            "vote_avg": 8.355,
            "vote_count": 5431,
        },
        {
            "id": 1144,
            "slug": "naruto-shippuden",
            "title": "Naruto Shippuden",
            "alternate_titles": "Naruto Shippûden",
            "generes": ["Fighting-Shounen", "Action"],
            "imdb": "tt0988824",
            "tmdb": 31910,
            "tmdb_type": "tv",
            "desc": "Naruto kehrt von seiner Trainingsreise zurück.",
            "start": 2007,
            "end": 2017,
            "poster": "def.png",
            "vote_avg": 8.547,
            "vote_count": 8019,
        },
    ],
    "pages": 1,
    "status": 200,
}

_ANIME_DETAIL_RESPONSE = {
    "data": {
        "id": 1143,
        "slug": "naruto",
        "title": "Naruto",
        "generes": ["Fighting-Shounen", "Action"],
        "anime_seasons": [
            {
                "id": 1839,
                "season": "Filme",
                "anime_id": 1143,
                "anime_episodes": [
                    {
                        "id": 27901,
                        "episode": "1",
                        "has_ger_sub": True,
                        "has_ger_dub": False,
                        "has_eng_sub": True,
                    },
                ],
            },
            {
                "id": 1840,
                "season": "1",
                "anime_id": 1143,
                "anime_episodes": [
                    {
                        "id": 27907,
                        "episode": "1",
                        "has_ger_sub": True,
                        "has_ger_dub": True,
                        "has_eng_sub": True,
                    },
                    {
                        "id": 27908,
                        "episode": "2",
                        "has_ger_sub": True,
                        "has_ger_dub": True,
                        "has_eng_sub": True,
                    },
                ],
            },
            {
                "id": 1841,
                "season": "2",
                "anime_id": 1143,
                "anime_episodes": [
                    {
                        "id": 27960,
                        "episode": "1",
                        "has_ger_sub": True,
                        "has_ger_dub": False,
                        "has_eng_sub": True,
                    },
                ],
            },
        ],
    },
    "status": 200,
}

_EPISODE_RESPONSE = {
    "data": {
        "id": 27907,
        "episode": "1",
        "image": "abc.webp",
        "view_count": 726,
        "anime_season_id": 1840,
        "has_ger_sub": True,
        "has_ger_dub": True,
        "has_eng_sub": True,
        "anime_episode_links": [
            {
                "id": 12828954,
                "link": "https://voe.sx/e/l2pwwfwxravg",
                "lang": "ger-dub",
                "name": "VOE",
                "anime_episode_id": 27907,
            },
            {
                "id": 12828955,
                "link": "https://voe.sx/e/cvrluqyituus",
                "lang": "eng-sub",
                "name": "VOE",
                "anime_episode_id": 27907,
            },
            {
                "id": 12828956,
                "link": "https://voe.sx/e/yda8qozrvf1c",
                "lang": "ger-sub",
                "name": "VOE",
                "anime_episode_id": 27907,
            },
            {
                "id": 1,
                "link": "http://0.0.0.0:3002/embed?slug=naruto&season=1&episode=1&id=l2pwwfwxravg",
                "lang": "ger-dub",
                "name": "ProxyPlayerSlow",
                "anime_episode_id": 0,
            },
            {
                "id": 2,
                "link": "http://0.0.0.0:3002/embed?slug=naruto&season=1&episode=1&id=cvrluqyituus",
                "lang": "eng-sub",
                "name": "ProxyPlayerSlow",
                "anime_episode_id": 0,
            },
        ],
    },
    "status": 200,
}

# Episode response with no external hosters (only proxy)
_EPISODE_RESPONSE_PROXY_ONLY = {
    "data": {
        "id": 99999,
        "episode": "1",
        "anime_episode_links": [
            {
                "id": 1,
                "link": "http://0.0.0.0:3002/embed?id=abc",
                "lang": "ger-dub",
                "name": "ProxyPlayerSlow",
                "anime_episode_id": 0,
            },
        ],
    },
    "status": 200,
}


# ---------------------------------------------------------------------------
# Unit tests: _build_stream_links
# ---------------------------------------------------------------------------


class TestBuildStreamLinks:
    """Tests for _build_stream_links utility."""

    def test_filters_proxy_players(self) -> None:
        """ProxyPlayerSlow links are excluded."""
        links = _build_stream_links(
            _EPISODE_RESPONSE["data"]["anime_episode_links"]
        )
        assert len(links) == 3
        for link in links:
            assert link["hoster"] != "proxyplayerslow"

    def test_builds_correct_format(self) -> None:
        """Each link has hoster, link, and language keys."""
        links = _build_stream_links(
            _EPISODE_RESPONSE["data"]["anime_episode_links"]
        )
        for link in links:
            assert "hoster" in link
            assert "link" in link
            assert "language" in link
            assert link["link"].startswith("https://")

    def test_language_labels(self) -> None:
        """Language keys are mapped to human-readable labels."""
        links = _build_stream_links(
            _EPISODE_RESPONSE["data"]["anime_episode_links"]
        )
        languages = {link["language"] for link in links}
        assert "German Dub" in languages
        assert "English Sub" in languages
        assert "German Sub" in languages

    def test_empty_input(self) -> None:
        """Empty list returns empty output."""
        assert _build_stream_links([]) == []

    def test_invalid_link_skipped(self) -> None:
        """Links without valid URLs are skipped."""
        links = _build_stream_links(
            [
                {"name": "VOE", "link": "", "lang": "ger-dub"},
                {"name": "VOE", "link": "not-a-url", "lang": "ger-dub"},
                {
                    "name": "VOE",
                    "link": "https://voe.sx/e/abc",
                    "lang": "ger-dub",
                },
            ]
        )
        assert len(links) == 1
        assert links[0]["link"] == "https://voe.sx/e/abc"

    def test_unknown_language_passthrough(self) -> None:
        """Unknown lang values pass through as-is."""
        links = _build_stream_links(
            [
                {
                    "name": "VOE",
                    "link": "https://voe.sx/e/abc",
                    "lang": "jpn-sub",
                },
            ]
        )
        assert links[0]["language"] == "jpn-sub"


# ---------------------------------------------------------------------------
# Unit tests: _find_first_episode
# ---------------------------------------------------------------------------


class TestFindFirstEpisode:
    """Tests for FireaniPlugin._find_first_episode."""

    def test_prefers_numbered_season(self) -> None:
        """Numbered seasons are preferred over 'Filme'."""
        plug = _make_plugin()
        detail = _ANIME_DETAIL_RESPONSE["data"]
        result = plug._find_first_episode(detail)
        assert result == ("1", "1")

    def test_filme_only(self) -> None:
        """Falls back to Filme season if no numbered seasons."""
        plug = _make_plugin()
        detail = {
            "anime_seasons": [
                {
                    "season": "Filme",
                    "anime_episodes": [
                        {"episode": "1"},
                        {"episode": "2"},
                    ],
                },
            ],
        }
        result = plug._find_first_episode(detail)
        assert result == ("Filme", "1")

    def test_empty_seasons(self) -> None:
        """Returns None when no seasons."""
        plug = _make_plugin()
        assert plug._find_first_episode({"anime_seasons": []}) is None

    def test_no_seasons_key(self) -> None:
        """Returns None when anime_seasons missing."""
        plug = _make_plugin()
        assert plug._find_first_episode({}) is None

    def test_season_with_no_episodes(self) -> None:
        """Skips seasons with empty episode lists."""
        plug = _make_plugin()
        detail = {
            "anime_seasons": [
                {"season": "1", "anime_episodes": []},
                {
                    "season": "2",
                    "anime_episodes": [{"episode": "1"}],
                },
            ],
        }
        result = plug._find_first_episode(detail)
        assert result == ("2", "1")

    def test_sorts_seasons_numerically(self) -> None:
        """Season '2' comes before '10' in sorting."""
        plug = _make_plugin()
        detail = {
            "anime_seasons": [
                {
                    "season": "10",
                    "anime_episodes": [{"episode": "1"}],
                },
                {
                    "season": "2",
                    "anime_episodes": [{"episode": "1"}],
                },
            ],
        }
        result = plug._find_first_episode(detail)
        assert result == ("2", "1")

    def test_sorts_episodes_numerically(self) -> None:
        """First episode is selected by numeric sort."""
        plug = _make_plugin()
        detail = {
            "anime_seasons": [
                {
                    "season": "1",
                    "anime_episodes": [
                        {"episode": "10"},
                        {"episode": "2"},
                        {"episode": "1"},
                    ],
                },
            ],
        }
        result = plug._find_first_episode(detail)
        assert result == ("1", "1")


# ---------------------------------------------------------------------------
# Unit tests: _EXCLUDED_PLAYERS and _LANG_LABELS
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for module-level constants."""

    def test_excluded_players(self) -> None:
        """Excluded player list contains expected entries."""
        assert "proxyplayerslow" in _EXCLUDED_PLAYERS
        assert "proxyplayer" in _EXCLUDED_PLAYERS

    def test_lang_labels(self) -> None:
        """Language labels contain expected mappings."""
        assert _LANG_LABELS["ger-dub"] == "German Dub"
        assert _LANG_LABELS["ger-sub"] == "German Sub"
        assert _LANG_LABELS["eng-sub"] == "English Sub"


# ---------------------------------------------------------------------------
# Integration tests: search method
# ---------------------------------------------------------------------------


def _route_get(
    search_resp: dict | None = None,
    detail_resp: dict | None = None,
    episode_resp: dict | None = None,
) -> AsyncMock:
    """Build a mock client.get that routes by URL path.

    Routes:
    - /api/anime/search → search_resp
    - /api/anime/episode → episode_resp
    - /api/anime → detail_resp (must be checked last, as it's a prefix)
    """

    async def _side_effect(url: str, **kwargs: object) -> httpx.Response:
        url_str = str(url)
        if "/api/anime/search" in url_str:
            return _mock_response(
                search_resp if search_resp is not None else {"data": [], "pages": 1, "status": 200}
            )
        if "/api/anime/episode" in url_str:
            return _mock_response(
                episode_resp if episode_resp is not None else {"data": {}, "status": 200}
            )
        if "/api/anime" in url_str:
            return _mock_response(
                detail_resp if detail_resp is not None else {"data": {}, "status": 200}
            )
        return _mock_response({"error": "Not Found"}, status_code=404)

    mock = AsyncMock(side_effect=_side_effect)
    return mock


class TestSearchIntegration:
    """Integration tests for FireaniPlugin.search."""

    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        """Search with valid results returns SearchResult list."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.get = _route_get(
            search_resp=_SEARCH_RESPONSE,
            detail_resp=_ANIME_DETAIL_RESPONSE,
            episode_resp=_EPISODE_RESPONSE,
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        results = await plug.search("Naruto")

        assert len(results) == 2
        assert results[0].title == "Naruto"
        assert results[0].category == 5070
        assert results[0].source_url == "https://fireani.me/anime/naruto"
        assert results[0].download_link == "https://voe.sx/e/l2pwwfwxravg"
        assert len(results[0].download_links) == 3

    @pytest.mark.asyncio
    async def test_search_metadata(self) -> None:
        """Search results contain expected metadata."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.get = _route_get(
            search_resp=_SEARCH_RESPONSE,
            detail_resp=_ANIME_DETAIL_RESPONSE,
            episode_resp=_EPISODE_RESPONSE,
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        results = await plug.search("Naruto")

        meta = results[0].metadata
        assert meta["genres"] == "Fighting-Shounen, Action, Abenteuer"
        assert meta["rating"] == "8.355"
        assert meta["votes"] == "5431"
        assert meta["tmdb"] == "46260"
        assert meta["imdb"] == "tt4907198"
        assert meta["year"] == "2002"

    @pytest.mark.asyncio
    async def test_search_description(self) -> None:
        """Search results have a description with genres and year range."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.get = _route_get(
            search_resp=_SEARCH_RESPONSE,
            detail_resp=_ANIME_DETAIL_RESPONSE,
            episode_resp=_EPISODE_RESPONSE,
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        results = await plug.search("Naruto")

        desc = results[0].description
        assert "Fighting-Shounen" in desc
        assert "(2002 - 2007)" in desc
        assert "Fuchsmonster" in desc

    @pytest.mark.asyncio
    async def test_search_empty_query(self) -> None:
        """Empty query returns empty list."""
        plug = _make_plugin()
        results = await plug.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_non_anime_category(self) -> None:
        """Non-anime category (e.g., 2000 Movies) returns empty."""
        plug = _make_plugin()
        results = await plug.search("Naruto", category=2000)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_anime_category_passes(self) -> None:
        """Anime category 5070 returns results normally."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.get = _route_get(
            search_resp=_SEARCH_RESPONSE,
            detail_resp=_ANIME_DETAIL_RESPONSE,
            episode_resp=_EPISODE_RESPONSE,
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        results = await plug.search("Naruto", category=5070)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_search_no_results(self) -> None:
        """Search returning empty data returns empty list."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.get = _route_get(
            search_resp={"data": [], "pages": 1, "status": 200},
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        results = await plug.search("nonexistent_anime_xyz")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_api_error(self) -> None:
        """Search API returning error status is handled gracefully."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.get = _route_get(
            search_resp={"error": "Internal Server Error", "status": 500},
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        results = await plug.search("Naruto")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self) -> None:
        """HTTP error during search is handled gracefully."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        results = await plug.search("Naruto")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_proxy_only_episodes_skipped(self) -> None:
        """Anime with only proxy player links are skipped."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.get = _route_get(
            search_resp={
                "data": [
                    {
                        "id": 1,
                        "slug": "test-anime",
                        "title": "Test Anime",
                        "generes": [],
                        "desc": "",
                        "start": 2024,
                        "end": 2024,
                    },
                ],
                "pages": 1,
                "status": 200,
            },
            detail_resp=_ANIME_DETAIL_RESPONSE,
            episode_resp=_EPISODE_RESPONSE_PROXY_ONLY,
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        results = await plug.search("test")
        assert results == []


# ---------------------------------------------------------------------------
# Unit tests: _scrape_anime
# ---------------------------------------------------------------------------


class TestScrapeAnime:
    """Tests for FireaniPlugin._scrape_anime."""

    @pytest.mark.asyncio
    async def test_missing_slug(self) -> None:
        """Anime without slug returns None."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        result = await plug._scrape_anime({"title": "Test", "slug": ""})
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_title(self) -> None:
        """Anime without title returns None."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        result = await plug._scrape_anime({"slug": "test", "title": ""})
        assert result is None

    @pytest.mark.asyncio
    async def test_detail_fallback_to_s1e1(self) -> None:
        """When detail API fails, falls back to season 1 episode 1."""
        plug = _make_plugin()
        plug.base_url = "https://fireani.me"

        call_log: list[str] = []

        async def _side_effect(url: str, **kwargs: object) -> httpx.Response:
            url_str = str(url)
            call_log.append(url_str)
            if "/api/anime/episode" in url_str:
                return _mock_response(_EPISODE_RESPONSE)
            if "/api/anime" in url_str:
                # Detail fails
                return _mock_response({"error": "Not Found"}, status_code=404)
            return _mock_response({}, status_code=404)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_side_effect)
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        result = await plug._scrape_anime(
            {
                "slug": "naruto",
                "title": "Naruto",
                "generes": [],
                "desc": "",
                "start": 2002,
            }
        )

        assert result is not None
        assert result.title == "Naruto"
        # Check that episode API was called (fallback path)
        episode_calls = [c for c in call_log if "/api/anime/episode" in c]
        assert len(episode_calls) == 1


# ---------------------------------------------------------------------------
# Unit tests: cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    """Tests for FireaniPlugin.cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self) -> None:
        """Cleanup closes the httpx client."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        await plug.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert plug._client is None

    @pytest.mark.asyncio
    async def test_cleanup_no_client(self) -> None:
        """Cleanup with no client does nothing."""
        plug = _make_plugin()
        plug._client = None
        await plug.cleanup()  # Should not raise


# ---------------------------------------------------------------------------
# Unit tests: plugin module-level export
# ---------------------------------------------------------------------------


class TestModuleExport:
    """Tests for the module-level plugin export."""

    def test_plugin_exported(self) -> None:
        """Module exports a `plugin` attribute."""
        assert hasattr(_mod, "plugin")
        assert _mod.plugin.name == "fireani"

    def test_plugin_version(self) -> None:
        """Plugin has expected version."""
        assert _mod.plugin.version == "1.0.0"

    def test_plugin_mode(self) -> None:
        """Plugin mode is httpx."""
        assert _mod.plugin.mode == "httpx"


# ---------------------------------------------------------------------------
# Unit tests: _api_search edge cases
# ---------------------------------------------------------------------------


class TestApiSearch:
    """Tests for FireaniPlugin._api_search edge cases."""

    @pytest.mark.asyncio
    async def test_invalid_json_response(self) -> None:
        """Non-JSON response is handled gracefully."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_mock_response("not json at all")
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        result = await plug._api_search("test")
        assert result == []

    @pytest.mark.asyncio
    async def test_wrong_status_in_body(self) -> None:
        """JSON response with non-200 status field returns empty."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_mock_response(
                {"data": [], "status": 500, "error": "fail"}
            )
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        result = await plug._api_search("test")
        assert result == []

    @pytest.mark.asyncio
    async def test_data_not_list(self) -> None:
        """JSON response with non-list data returns empty."""
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_mock_response(
                {"data": "not a list", "status": 200}
            )
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        result = await plug._api_search("test")
        assert result == []


# ---------------------------------------------------------------------------
# Unit tests: _get_episode_links edge cases
# ---------------------------------------------------------------------------


class TestGetEpisodeLinks:
    """Tests for FireaniPlugin._get_episode_links edge cases."""

    @pytest.mark.asyncio
    async def test_episode_no_links(self) -> None:
        """Episode response with no links returns empty."""
        plug = _make_plugin()
        plug.base_url = "https://fireani.me"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_mock_response(
                {
                    "data": {
                        "id": 1,
                        "episode": "1",
                        "anime_episode_links": [],
                    },
                    "status": 200,
                }
            )
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        links = await plug._get_episode_links("test", "1", "1")
        assert links == []

    @pytest.mark.asyncio
    async def test_episode_null_links(self) -> None:
        """Episode response with null links returns empty."""
        plug = _make_plugin()
        plug.base_url = "https://fireani.me"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_mock_response(
                {
                    "data": {
                        "id": 1,
                        "episode": "1",
                        "anime_episode_links": None,
                    },
                    "status": 200,
                }
            )
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        links = await plug._get_episode_links("test", "1", "1")
        assert links == []

    @pytest.mark.asyncio
    async def test_episode_http_error(self) -> None:
        """HTTP error for episode is handled gracefully."""
        plug = _make_plugin()
        plug.base_url = "https://fireani.me"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        links = await plug._get_episode_links("test", "1", "1")
        assert links == []


# ---------------------------------------------------------------------------
# Unit tests: _get_anime_detail edge cases
# ---------------------------------------------------------------------------


class TestGetAnimeDetail:
    """Tests for FireaniPlugin._get_anime_detail edge cases."""

    @pytest.mark.asyncio
    async def test_detail_not_found(self) -> None:
        """404 response returns None."""
        plug = _make_plugin()
        plug.base_url = "https://fireani.me"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_mock_response(
                {"error": "Not Found"}, status_code=404
            )
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        result = await plug._get_anime_detail("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_detail_valid(self) -> None:
        """Valid detail response returns data dict."""
        plug = _make_plugin()
        plug.base_url = "https://fireani.me"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_mock_response(_ANIME_DETAIL_RESPONSE)
        )
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        result = await plug._get_anime_detail("naruto")
        assert result is not None
        assert result["slug"] == "naruto"
        assert len(result["anime_seasons"]) == 3


# ---------------------------------------------------------------------------
# Description edge cases
# ---------------------------------------------------------------------------


class TestDescriptionFormatting:
    """Tests for description formatting in _scrape_anime."""

    @pytest.mark.asyncio
    async def test_long_description_truncated(self) -> None:
        """Descriptions longer than 300 chars are truncated."""
        plug = _make_plugin()
        plug.base_url = "https://fireani.me"

        long_desc = "A" * 500

        async def _side_effect(url: str, **kwargs: object) -> httpx.Response:
            url_str = str(url)
            if "/api/anime/episode" in url_str:
                return _mock_response(_EPISODE_RESPONSE)
            if "/api/anime" in url_str:
                return _mock_response(_ANIME_DETAIL_RESPONSE)
            return _mock_response({}, status_code=404)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_side_effect)
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        result = await plug._scrape_anime(
            {
                "slug": "test",
                "title": "Test",
                "generes": [],
                "desc": long_desc,
                "start": 2024,
                "end": 2024,
            }
        )

        assert result is not None
        assert len(result.description) <= 310  # 300 + "..." + year/genre prefix
        assert result.description.endswith("...")

    @pytest.mark.asyncio
    async def test_same_start_end_year(self) -> None:
        """When start == end, year shows single value."""
        plug = _make_plugin()
        plug.base_url = "https://fireani.me"

        async def _side_effect(url: str, **kwargs: object) -> httpx.Response:
            url_str = str(url)
            if "/api/anime/episode" in url_str:
                return _mock_response(_EPISODE_RESPONSE)
            if "/api/anime" in url_str:
                return _mock_response(_ANIME_DETAIL_RESPONSE)
            return _mock_response({}, status_code=404)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_side_effect)
        mock_client.aclose = AsyncMock()
        plug._client = mock_client

        result = await plug._scrape_anime(
            {
                "slug": "test",
                "title": "Test",
                "generes": ["Action"],
                "desc": "Some desc",
                "start": 2024,
                "end": 2024,
            }
        )

        assert result is not None
        assert "(2024)" in result.description
        assert "2024 - 2024" not in result.description
