"""Unit tests for the moflix-stream.xyz plugin (JSON API over httpx)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "moflix.py"


@pytest.fixture()
def moflix_mod():
    """Import moflix plugin module."""
    spec = importlib.util.spec_from_file_location("moflix", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["moflix"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("moflix", None)


# ---------------------------------------------------------------------------
# JSON fixtures (same as API responses)
# ---------------------------------------------------------------------------

SEARCH_RESPONSE = {
    "results": [
        {
            "id": 2809,
            "name": "The Batman",
            "type": "movie",
            "release_date": "2022-03-01T00:00:00.000000Z",
            "description": "Ein düsterer Rachefeldzug...",
            "poster": "https://image.tmdb.org/t/p/original/poster.jpg",
            "backdrop": "https://image.tmdb.org/t/p/w1280/backdrop.jpg",
            "runtime": 176,
            "imdb_id": "tt1877830",
            "tmdb_id": 414906,
            "year": 2022,
            "rating": 7.7,
            "is_series": False,
            "vote_count": 10000,
            "certification": "pg13",
            "views": 500000,
            "popularity": 200,
            "model_type": "title",
        },
        {
            "id": 9232,
            "name": "Batman: Caped Crusader",
            "type": "movie",
            "release_date": "2024-08-01T00:00:00.000000Z",
            "description": "Eine animierte Batman-Serie...",
            "poster": "https://image.tmdb.org/t/p/original/poster2.jpg",
            "runtime": 25,
            "imdb_id": "tt15255200",
            "tmdb_id": 365448,
            "year": 2024,
            "rating": 7.5,
            "is_series": True,
            "vote_count": 500,
            "model_type": "title",
        },
    ],
}

DETAIL_MOVIE_RESPONSE = {
    "title": {
        "id": 2809,
        "name": "The Batman",
        "is_series": False,
        "year": 2022,
        "videos": [
            {
                "id": 456,
                "name": "Mirror 1",
                "src": "https://doods.to/e/abc123",
                "type": "embed",
                "quality": "1080p/5.1",
                "language": "de",
                "category": "full",
                "season_num": None,
                "episode_num": None,
            },
            {
                "id": 789,
                "name": "Mirror 2",
                "src": "https://moflix.upns.xyz/#xyz",
                "type": "embed",
                "quality": "1080p/5.1",
                "language": "de",
                "category": "full",
                "season_num": None,
                "episode_num": None,
            },
        ],
        "genres": [
            {"id": 1, "name": "thriller"},
            {"id": 2, "name": "krimi"},
            {"id": 3, "name": "mystery"},
        ],
    },
}

DETAIL_SERIES_RESPONSE = {
    "title": {
        "id": 9232,
        "name": "Batman: Caped Crusader",
        "is_series": True,
        "year": 2024,
        "videos": [
            {
                "name": "VOE",
                "src": "https://voe.sx/e/s1e1",
                "quality": "1080p",
                "season_num": 1,
                "episode_num": 1,
            }
        ],
        "genres": [
            {"id": 10, "name": "animation"},
            {"id": 11, "name": "action"},
        ],
    },
}


def _episode_video(season: int, episode: int) -> dict:
    return {
        "name": "VOE",
        "src": f"https://voe.sx/e/s{season}e{episode}",
        "quality": "1080p",
        "season_num": season,
        "episode_num": episode,
    }


EMPTY_SEARCH_RESPONSE: dict = {
    "results": [],
}

DETAIL_NO_VIDEOS_RESPONSE = {
    "title": {
        "id": 9999,
        "name": "No Videos Title",
        "is_series": False,
        "year": 2023,
        "videos": [],
        "genres": [],
    },
}


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    """Tests for plugin class attributes."""

    def test_name(self, moflix_mod):
        assert moflix_mod.plugin.name == "moflix"

    def test_version(self, moflix_mod):
        assert moflix_mod.plugin.version == "1.2.0"

    def test_mode(self, moflix_mod):
        assert moflix_mod.plugin.mode == "httpx"

    def test_provides(self, moflix_mod):
        assert moflix_mod.plugin.provides == "stream"


# ---------------------------------------------------------------------------
# Build search result tests
# ---------------------------------------------------------------------------


class TestBuildSearchResult:
    """Tests for _build_search_result method."""

    def test_movie_with_videos(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = SEARCH_RESPONSE["results"][0]
        detail = DETAIL_MOVIE_RESPONSE["title"]

        sr = p._build_search_result(entry, detail)

        assert sr.title == "The Batman (2022)"
        assert sr.download_link == "https://doods.to/e/abc123"
        assert sr.category == 2000
        assert sr.published_date == "2022"
        assert sr.download_links is not None
        assert len(sr.download_links) == 2

    def test_movie_download_links_have_hoster_info(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = SEARCH_RESPONSE["results"][0]
        detail = DETAIL_MOVIE_RESPONSE["title"]

        sr = p._build_search_result(entry, detail)

        assert sr.download_links[0]["hoster"] == "Mirror 1 (1080p/5.1)"
        assert sr.download_links[0]["link"] == "https://doods.to/e/abc123"
        assert sr.download_links[1]["hoster"] == "Mirror 2 (1080p/5.1)"

    def test_series_category(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = SEARCH_RESPONSE["results"][1]
        detail = {**DETAIL_SERIES_RESPONSE["title"], "videos": [_episode_video(1, 1)]}

        sr = p._build_search_result(entry, detail)

        assert sr.category == 5000
        assert "2024" in sr.title

    def test_episode_request_keeps_only_that_episode(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"
        entry = SEARCH_RESPONSE["results"][1]
        detail = {
            **DETAIL_SERIES_RESPONSE["title"],
            "videos": [
                _episode_video(1, 1),
                _episode_video(2, 5),
                _episode_video(2, 6),
            ],
        }

        sr = p._build_search_result(entry, detail, season=2, episode=5)

        assert [lnk["link"] for lnk in sr.download_links] == ["https://voe.sx/e/s2e5"]

    def test_episode_request_without_that_episode_gives_nothing(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"
        entry = SEARCH_RESPONSE["results"][1]
        detail = {**DETAIL_SERIES_RESPONSE["title"], "videos": [_episode_video(1, 1)]}

        assert p._build_search_result(entry, detail, season=3, episode=1) is None

    def test_no_detail_fallback(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = SEARCH_RESPONSE["results"][0]

        # The site's own title page is no download link or stream
        assert p._build_search_result(entry, None) is None

    def test_premium_player_is_skipped(self, moflix_mod):
        """The "Premium (No Ads)" video is moflix's paid player: its master
        playlist answers, every variant 403s without a paid session."""
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"
        premium = {
            "id": 1620235,
            "name": "Premium (No Ads)",
            "type": "stream",
            "src": "https://batman.moflix-stream.day/movies/Inception.2010/"
            "master.m3u8?md5=ypLpIVq6&expires=1791049766",
            "quality": None,
        }
        detail = {
            **DETAIL_MOVIE_RESPONSE["title"],
            "videos": [premium, *DETAIL_MOVIE_RESPONSE["title"]["videos"]],
        }

        sr = p._build_search_result(SEARCH_RESPONSE["results"][0], detail)

        assert sr is not None
        assert [lk["link"] for lk in sr.download_links or []] == [
            "https://doods.to/e/abc123",
            "https://moflix.upns.xyz/#xyz",
        ]

    def test_title_with_premium_video_only_gives_nothing(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"
        detail = {
            **DETAIL_MOVIE_RESPONSE["title"],
            "videos": [
                {
                    "name": "Premium (No Ads)",
                    "type": "stream",
                    "src": "https://joker.moflix-stream.day/x/master.m3u8",
                }
            ],
        }

        assert p._build_search_result(SEARCH_RESPONSE["results"][0], detail) is None

    def test_no_videos_fallback(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = {
            "id": 9999,
            "name": "No Videos",
            "year": 2023,
            "is_series": False,
        }
        detail = DETAIL_NO_VIDEOS_RESPONSE["title"]

        assert p._build_search_result(entry, detail) is None

    def test_no_year(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = {"id": 1, "name": "Unknown Movie", "is_series": False}

        sr = p._build_search_result(entry, DETAIL_MOVIE_RESPONSE["title"])

        assert sr.title == "Unknown Movie"
        assert sr.published_date is None

    def test_metadata_fields(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = SEARCH_RESPONSE["results"][0]
        detail = DETAIL_MOVIE_RESPONSE["title"]

        sr = p._build_search_result(entry, detail)

        assert sr.metadata["genres"] == "thriller, krimi, mystery"
        assert sr.metadata["imdb_id"] == "tt1877830"
        assert sr.metadata["tmdb_id"] == "414906"
        assert sr.metadata["rating"] == "7.7"
        assert sr.metadata["runtime"] == "176"

    def test_long_description_truncated(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p.base_url = "https://moflix-stream.xyz"

        entry = {
            "id": 1,
            "name": "Long Desc",
            "year": 2023,
            "is_series": False,
            "description": "A" * 500,
        }

        sr = p._build_search_result(entry, DETAIL_MOVIE_RESPONSE["title"])

        assert len(sr.description) == 300
        assert sr.description.endswith("...")


# ---------------------------------------------------------------------------
# Plugin search tests (the site's JSON API over httpx)
# ---------------------------------------------------------------------------

_BASE = "https://moflix-stream.xyz"
# The homepage starts the site's Laravel session; its API answers 401 without
_SESSION_COOKIES = [
    ("Set-Cookie", "XSRF-TOKEN=tok%3D%3D; Path=/"),
    ("Set-Cookie", "moflix_stream_session=s1; Path=/; HttpOnly"),
]
_CF_CHALLENGE = (
    "<html><head><title>Just a moment...</title></head>"
    "<body><div id='challenge-platform'></div></body></html>"
)


def _api_routes(
    *,
    search: dict | None = None,
    details: dict[int, dict] | None = None,
) -> respx.Route:
    """Mock homepage, search and title endpoints; return the homepage route."""
    home = respx.get(f"{_BASE}/").respond(
        200, text="<html>moflix</html>", headers=_SESSION_COOKIES
    )
    respx.get(url__startswith=f"{_BASE}/api/v1/search/").respond(
        200, json=SEARCH_RESPONSE if search is None else search
    )
    if details is None:
        details = {2809: DETAIL_MOVIE_RESPONSE, 9232: DETAIL_SERIES_RESPONSE}
    for title_id, body in details.items():
        respx.get(url__startswith=f"{_BASE}/api/v1/titles/{title_id}").respond(
            200, json=body
        )
    return home


def _api_requests() -> list[httpx.Request]:
    return [c.request for c in respx.calls if "/api/v1/" in str(c.request.url)]


class TestPluginSearch:
    """MoflixPlugin.search() against its JSON API over httpx."""

    @pytest.fixture()
    async def plugin(self, moflix_mod):
        async with httpx.AsyncClient() as client:
            p = moflix_mod.MoflixPlugin()
            p._client = client
            p._domain_verified = True
            p.base_url = _BASE
            yield p

    @respx.mock
    async def test_search_returns_results(self, plugin):
        _api_routes()

        results = await plugin.search("batman")

        assert len(results) == 2
        assert results[0].title == "The Batman (2022)"
        assert results[0].category == 2000
        assert results[1].category == 5000

    @respx.mock
    async def test_api_calls_carry_the_sites_session(self, plugin):
        """JSON, the site as Referer and the XSRF cookie as header (401 else)."""
        _api_routes()

        await plugin.search("batman", category=2000)

        sent = _api_requests()[0]
        assert sent.headers["accept"] == "application/json"
        assert sent.headers["referer"] == f"{_BASE}/"
        assert sent.headers["x-xsrf-token"] == "tok=="
        assert "moflix_stream_session=s1" in sent.headers["cookie"]

    @respx.mock
    async def test_session_is_started_once(self, plugin):
        home = _api_routes()

        await plugin.search("batman")
        await plugin.search("batman", category=2000)

        assert home.call_count == 1

    async def test_search_empty_query(self, plugin):
        assert await plugin.search("") == []

    async def test_search_rejected_category(self, plugin):
        # Music category (3000) not supported
        assert await plugin.search("test", category=3000) == []

    @respx.mock
    async def test_search_movie_category_filters_series(self, plugin):
        _api_routes(details={2809: DETAIL_MOVIE_RESPONSE})

        results = await plugin.search("batman", category=2000)

        assert [r.category for r in results] == [2000]

    @respx.mock
    async def test_search_tv_category_filters_movies(self, plugin):
        _api_routes(details={9232: DETAIL_SERIES_RESPONSE})

        results = await plugin.search("batman", category=5000)

        assert [r.category for r in results] == [5000]

    @respx.mock
    async def test_search_no_results(self, plugin):
        _api_routes(search=EMPTY_SEARCH_RESPONSE)

        assert await plugin.search("xyznonexistent") == []

    @respx.mock
    async def test_search_api_error(self, plugin):
        respx.get(f"{_BASE}/").respond(200, headers=_SESSION_COOKIES)
        respx.get(url__startswith=f"{_BASE}/api/v1/search/").respond(500)

        assert await plugin.search("batman") == []

    @respx.mock
    async def test_detail_failure_gives_no_result(self, plugin):
        """Without the detail (videos) there is no link, only the title page."""
        _api_routes(details={})
        respx.get(url__startswith=f"{_BASE}/api/v1/titles/").respond(500)

        assert await plugin.search("batman") == []

    @respx.mock
    async def test_search_with_videos_in_download_link(self, plugin):
        _api_routes()

        results = await plugin.search("batman")

        assert results[0].download_link == "https://doods.to/e/abc123"

    @respx.mock
    async def test_entry_without_id_skipped(self, plugin):
        _api_routes(search={"results": [{"name": "No ID Movie", "is_series": False}]})

        assert await plugin.search("test") == []


class TestCloudflareChallenge:
    """For some IPs Cloudflare challenges the API: the browser answers that
    call once, the following ones go through httpx with its session."""

    @respx.mock
    async def test_challenged_api_continues_with_the_browser_session(
        self, moflix_mod
    ) -> None:
        from scavengarr.domain.ports.browser_fetcher import BrowserSession
        from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

        respx.get(f"{_BASE}/").respond(200, headers=_SESSION_COOKIES)
        respx.get(url__startswith=f"{_BASE}/api/v1/search/").respond(
            403, text=_CF_CHALLENGE
        )
        detail = respx.get(url__startswith=f"{_BASE}/api/v1/titles/2809").respond(
            200, json=DETAIL_MOVIE_RESPONSE
        )
        fetcher = AsyncMock()
        fetcher.fetch_text = AsyncMock(
            return_value=json.dumps({"results": [SEARCH_RESPONSE["results"][0]]})
        )
        fetcher.session = AsyncMock(
            return_value=BrowserSession(
                cookies={"cf_clearance": "c", "XSRF-TOKEN": "b%3D"},
                user_agent="BrowserUA/1.0",
            )
        )
        HttpxPluginBase.set_browser_fetcher(fetcher)
        try:
            async with httpx.AsyncClient() as client:
                p = moflix_mod.MoflixPlugin()
                p._client = client
                p._domain_verified = True
                p.base_url = _BASE
                results = await p.search("batman")
        finally:
            HttpxPluginBase.set_browser_fetcher(None)

        assert [r.title for r in results] == ["The Batman (2022)"]
        fetcher.fetch_text.assert_awaited_once()
        sent = detail.calls.last.request
        assert sent.headers["x-xsrf-token"] == "b="
        assert sent.headers["user-agent"] == "BrowserUA/1.0"
        assert "cf_clearance=c" in sent.headers["cookie"]


# ---------------------------------------------------------------------------
# Domain verification and cleanup
# ---------------------------------------------------------------------------


class TestDomainVerification:
    """Tests for domain fallback logic."""

    async def test_skips_if_already_verified(self, moflix_mod):
        p = moflix_mod.MoflixPlugin()
        p._domain_verified = True
        p.base_url = "https://custom.domain"

        await p._verify_domain()

        assert p.base_url == "https://custom.domain"


class TestCleanup:
    async def test_cleanup_without_client(self, moflix_mod):
        await moflix_mod.MoflixPlugin().cleanup()  # Should not raise
