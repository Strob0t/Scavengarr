"""Unit tests for the nox.to plugin."""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from scavengarr.domain.plugins import GrabResolvingPlugin

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "nox.py"
_BASE = "https://nox.to"


@pytest.fixture()
def nox_mod():
    """Import nox plugin module."""
    spec = importlib.util.spec_from_file_location("nox", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["nox"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("nox", None)


def _make_plugin(mod) -> object:
    """Create a fresh NoxPlugin with domain verification skipped."""
    p = mod.NoxPlugin()
    p._domain_verified = True
    return p


# ---------------------------------------------------------------------------
# JSON fixtures (shapes from the live API, 2026-09-28)
# ---------------------------------------------------------------------------

_SEARCH_ITEMS = [
    {
        "id": 25422,
        "type": "movie",
        "slug": "iron-man-3-r1rz58EAW-",
        "title": "Iron Man 3",
        "productionYear": 2013,
    },
    {
        "id": 58270,
        "type": "series",
        "slug": "iron-man-und-freunde-2025",
        "title": "Iron Man und seine fantastischen Freunde",
        "productionYear": 2025,
    },
    {"id": 7, "type": "game", "slug": "iron-man-game", "title": "Iron Man Game"},
]

_MEDIA_MOVIE = {
    "id": 25422,
    "type": "movie",
    "slug": "iron-man-3-r1rz58EAW-",
    "title": "Iron Man 3",
    "imdbId": "tt1300854",
    "imdbRating": 7.1,
    "duration": 140,
    "description": "Tony Stark gegen The Mandarin.",
    "posterUrl": "/h0vJ6sTYWQmqenNqRa1IiW1t0Ys.jpg",
    "productionYear": 2013,
    "genres": [
        {"id": 1, "name": "Action", "slug": "action"},
        {"id": 3, "name": "Sci-Fi", "slug": "sci-fi"},
    ],
    "releases": [
        {
            "id": 122846,
            "slug": "iron-man-3-german-dl-1080p-bluray-x264-exquisite",
            "name": "Iron.Man.3.German.DL.1080p.BluRay.x264-EXQUiSiTE",
            "onlineLinks": 1,
            "totalLinks": 1,
            "video": "Blu-ray",
            "audio": "DL DTS 5.1",
            "codec": "x264",
            "size": "6594",
            "sizeUnit": "MB",
            "publishAt": "2026-03-05T23:01:00.000Z",
        },
        {
            "id": 99001,
            "slug": "iron-man-3-offline",
            "name": "Iron.Man.3.German.720p.BluRay.x264-OFFLINE",
            "onlineLinks": 0,
            "totalLinks": 1,
            "size": "4",
            "sizeUnit": "GB",
        },
    ],
}

_MEDIA_SERIES = {
    "id": 58270,
    "type": "series",
    "slug": "iron-man-und-freunde-2025",
    "title": "Iron Man und seine fantastischen Freunde",
    "productionYear": 2025,
    "releases": [
        {
            "id": 125482,
            "name": "Iron.Man.und.seine.fantastischen.Freunde.S01.German.DL.1080p",
            "onlineLinks": 2,
            "size": "12",
            "sizeUnit": "GB",
        },
    ],
}

_RECENT = [
    {
        "id": 377801,
        "name": "President.Curtis.S01E10.GERMAN.DL.720P.WEB.H264-WAYNE",
        "type": "series",
        "mediaTitle": "President Curtis",
        "mediaSlug": "president-curtis-yzaGs1xjipEQ",
        "mediaPosterUrl": "/bT9yFXPUfpFoOaouLesq1bEDggJ.jpg",
        "imdbRating": 7.2,
        "size": "524",
        "sizeUnit": "MB",
        "publishAt": "2026-09-28T13:10:58.699Z",
    },
    {
        "id": 377802,
        "name": "Some.Movie.2024.German.DL.1080p.WEB.x264-GRP",
        "type": "movie",
        "mediaTitle": "Some Movie",
        "mediaSlug": "some-movie-abc",
        "size": "2",
        "sizeUnit": "GB",
    },
    {
        "id": 377803,
        "name": "Some.Game-GRP",
        "type": "game",
        "mediaTitle": "Some Game",
        "mediaSlug": "some-game",
    },
]


def _mock_api(
    items: list[dict] | None = None,
    total_pages: int = 1,
) -> respx.Route:
    """Route search pages and media details; returns the search route."""
    respx.get(f"{_BASE}/api/media/iron-man-3-r1rz58EAW-").respond(
        200, json=_MEDIA_MOVIE
    )
    respx.get(f"{_BASE}/api/media/iron-man-und-freunde-2025").respond(
        200, json=_MEDIA_SERIES
    )
    search_items = _SEARCH_ITEMS if items is None else items
    return respx.get(f"{_BASE}/api/search").respond(
        200,
        json={
            "items": search_items,
            "total": len(search_items),
            "page": 1,
            "totalPages": total_pages,
            "releases": [],
        },
    )


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestFormatSize:
    """Tests for _format_size helper."""

    def test_normal(self, nox_mod):
        assert nox_mod._format_size("1481", "MB") == "1481 MB"

    def test_gb(self, nox_mod):
        assert nox_mod._format_size("4", "GB") == "4 GB"

    def test_none_size(self, nox_mod):
        assert nox_mod._format_size(None, "MB") is None

    def test_empty_size(self, nox_mod):
        assert nox_mod._format_size("", "MB") is None

    def test_none_unit_defaults_to_mb(self, nox_mod):
        assert nox_mod._format_size("100", None) == "100 MB"


class TestCategoryForType:
    """Tests for _category_for_type helper."""

    def test_movie(self, nox_mod):
        assert nox_mod._category_for_type("movie") == 2000

    def test_series(self, nox_mod):
        assert nox_mod._category_for_type("series") == 5000

    def test_doku(self, nox_mod):
        assert nox_mod._category_for_type("doku") == 5080

    def test_game_unsupported(self, nox_mod):
        assert nox_mod._category_for_type("game") is None

    def test_unknown_type(self, nox_mod):
        assert nox_mod._category_for_type("audio") is None


class TestExtractYear:
    """Tests for _extract_year helper."""

    def test_year_in_scene_name(self, nox_mod):
        assert nox_mod._extract_year("Iron.Man.2008.German.DL") == "2008"

    def test_year_in_title(self, nox_mod):
        assert nox_mod._extract_year("Iron Man [2008]") == "2008"

    def test_no_year(self, nox_mod):
        assert nox_mod._extract_year("Iron Man") is None

    def test_empty(self, nox_mod):
        assert nox_mod._extract_year("") is None


class TestGenreNames:
    """Tests for _genre_names helper."""

    def test_dict_genres(self, nox_mod):
        assert nox_mod._genre_names(_MEDIA_MOVIE["genres"]) == "Action, Sci-Fi"

    def test_string_genres(self, nox_mod):
        assert nox_mod._genre_names(["Drama", ""]) == "Drama"

    def test_not_a_list(self, nox_mod):
        assert nox_mod._genre_names(None) == ""


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestNoxPluginAttributes:
    """Tests for plugin class attributes."""

    def test_name(self, nox_mod):
        assert nox_mod.plugin.name == "nox"

    def test_provides(self, nox_mod):
        assert nox_mod.plugin.provides == "download"

    def test_default_language(self, nox_mod):
        assert nox_mod.plugin.default_language == "de"

    def test_base_url(self, nox_mod):
        assert nox_mod.plugin.base_url == "https://nox.to"

    def test_domains(self, nox_mod):
        assert nox_mod.plugin._domains == ["nox.to", "nox.tv"]

    def test_mode(self, nox_mod):
        assert nox_mod.plugin.mode == "httpx"


# ---------------------------------------------------------------------------
# Build result tests
# ---------------------------------------------------------------------------


class TestBuildResult:
    """Tests for NoxPlugin._build_result."""

    def test_movie_result(self, nox_mod):
        p = _make_plugin(nox_mod)
        sr = p._build_result(_MEDIA_MOVIE["releases"][0], _MEDIA_MOVIE)

        assert sr.title == "Iron Man 3 (2013)"
        assert sr.download_link == (
            "https://nox.to/media/iron-man-3-r1rz58EAW-?release=122846"
        )
        assert sr.source_url == sr.download_link
        assert sr.release_name == "Iron.Man.3.German.DL.1080p.BluRay.x264-EXQUiSiTE"
        assert sr.size == "6594 MB"
        assert sr.published_date == "2026-03-05"
        assert sr.category == 2000
        assert sr.description == "Tony Stark gegen The Mandarin."

    def test_metadata_fields(self, nox_mod):
        p = _make_plugin(nox_mod)
        sr = p._build_result(_MEDIA_MOVIE["releases"][0], _MEDIA_MOVIE)

        assert sr.metadata == {
            "imdb_id": "tt1300854",
            "rating": "7.1",
            "genres": "Action, Sci-Fi",
            "runtime": "140",
            "poster": "https://nox.to/api/image/w342/h0vJ6sTYWQmqenNqRa1IiW1t0Ys.jpg",
            "codec": "x264",
            "video": "Blu-ray",
            "audio": "DL DTS 5.1",
        }

    def test_series_result(self, nox_mod):
        p = _make_plugin(nox_mod)
        sr = p._build_result(_MEDIA_SERIES["releases"][0], _MEDIA_SERIES)

        assert sr.category == 5000
        assert sr.title == "Iron Man und seine fantastischen Freunde (2025)"

    def test_year_from_scene_name(self, nox_mod):
        p = _make_plugin(nox_mod)
        media = {"type": "movie", "slug": "x", "title": "Some Movie"}
        sr = p._build_result({"id": 1, "name": "Some.Movie.2024.German"}, media)

        assert sr.title == "Some Movie (2024)"

    def test_game_returns_none(self, nox_mod):
        p = _make_plugin(nox_mod)
        media = {"type": "game", "slug": "g", "title": "Game"}
        assert p._build_result({"id": 1}, media) is None

    def test_missing_release_id_returns_none(self, nox_mod):
        p = _make_plugin(nox_mod)
        assert p._build_result({"name": "x"}, _MEDIA_MOVIE) is None

    def test_missing_title_returns_none(self, nox_mod):
        p = _make_plugin(nox_mod)
        media = {"type": "movie", "slug": "x", "title": ""}
        assert p._build_result({"id": 1}, media) is None

    def test_long_description_truncated(self, nox_mod):
        p = _make_plugin(nox_mod)
        media = {**_MEDIA_MOVIE, "description": "A" * 500}
        sr = p._build_result(_MEDIA_MOVIE["releases"][0], media)

        assert len(sr.description) == 300
        assert sr.description.endswith("...")

    def test_missing_optional_fields(self, nox_mod):
        p = _make_plugin(nox_mod)
        media = {"type": "movie", "slug": "x", "title": "Bare"}
        sr = p._build_result({"id": 1}, media)

        assert sr.size is None
        assert sr.published_date is None
        assert sr.description is None
        assert sr.metadata["poster"] == ""
        assert sr.metadata["codec"] == ""


# ---------------------------------------------------------------------------
# Search tests (respx)
# ---------------------------------------------------------------------------


class TestNoxSearch:
    """Tests for NoxPlugin.search() with mocked HTTP."""

    @pytest.fixture()
    def plugin(self, nox_mod):
        return _make_plugin(nox_mod)

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_returns_release_results(self, plugin):
        search = _mock_api()

        results = await plugin.search("Iron Man")
        await plugin.cleanup()

        # movie: 1 online release (offline one skipped), series: 1, game skipped
        assert [r.download_link for r in results] == [
            "https://nox.to/media/iron-man-3-r1rz58EAW-?release=122846",
            "https://nox.to/media/iron-man-und-freunde-2025?release=125482",
        ]
        params = search.calls[0].request.url.params
        assert params["q"] == "Iron Man"
        assert params["page"] == "1"

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_paginates(self, plugin):
        search = _mock_api(items=_SEARCH_ITEMS[:1], total_pages=3)

        await plugin.search("Iron Man")
        await plugin.cleanup()

        pages = [c.request.url.params["page"] for c in search.calls]
        assert pages == ["1", "2", "3"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_page_cap(self, plugin, nox_mod):
        search = _mock_api(items=_SEARCH_ITEMS[:1], total_pages=99)

        await plugin.search("Iron Man")
        await plugin.cleanup()

        assert len(search.calls) == nox_mod._MAX_SEARCH_PAGES

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_no_results(self, plugin):
        _mock_api(items=[])

        assert await plugin.search("nothing") == []
        await plugin.cleanup()

    @respx.mock
    @pytest.mark.asyncio
    async def test_search_http_error(self, plugin):
        respx.get(f"{_BASE}/api/search").respond(500)

        assert await plugin.search("Iron Man") == []
        await plugin.cleanup()

    @respx.mock
    @pytest.mark.asyncio
    async def test_media_error_skipped(self, plugin):
        _mock_api()
        respx.get(f"{_BASE}/api/media/iron-man-3-r1rz58EAW-").respond(404)

        results = await plugin.search("Iron Man")
        await plugin.cleanup()

        assert [r.category for r in results] == [5000]

    @respx.mock
    @pytest.mark.asyncio
    async def test_category_movies(self, plugin):
        _mock_api()

        results = await plugin.search("Iron Man", category=2000)
        await plugin.cleanup()

        assert [r.category for r in results] == [2000]
        # series media is not fetched at all
        assert not any(
            "iron-man-und-freunde" in str(c.request.url) for c in respx.calls
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_category_tv(self, plugin):
        _mock_api()

        results = await plugin.search("Iron Man", category=5000)
        await plugin.cleanup()

        assert [r.category for r in results] == [5000]

    @pytest.mark.asyncio
    async def test_category_rejects_unsupported(self, plugin):
        assert await plugin.search("Iron Man", category=4000) == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_empty_query_browses_recent(self, plugin):
        route = respx.get(f"{_BASE}/api/releases/recent/1").respond(200, json=_RECENT)
        respx.get(f"{_BASE}/api/releases/recent/3").respond(200, json=_RECENT)
        respx.get(f"{_BASE}/api/releases/recent/7").respond(200, json=_RECENT)

        results = await plugin.search("")
        await plugin.cleanup()

        assert route.called
        assert [r.title for r in results] == ["President Curtis", "Some Movie (2024)"]
        first = results[0]
        assert first.download_link == (
            "https://nox.to/media/president-curtis-yzaGs1xjipEQ?release=377801"
        )
        assert first.category == 5000
        assert first.metadata["rating"] == "7.2"
        assert first.metadata["poster"] == (
            "https://nox.to/api/image/w342/bT9yFXPUfpFoOaouLesq1bEDggJ.jpg"
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_browse_escalates_days(self, plugin, nox_mod):
        routes = [
            respx.get(f"{_BASE}/api/releases/recent/{days}").respond(200, json=_RECENT)
            for days in nox_mod._BROWSE_DAYS
        ]

        await plugin.search("")
        await plugin.cleanup()

        # fewer than _BROWSE_MIN_RESULTS each time -> every window is tried
        assert all(r.called for r in routes)

    @respx.mock
    @pytest.mark.asyncio
    async def test_browse_stops_when_enough(self, plugin, nox_mod):
        many = [
            {**_RECENT[1], "id": i, "mediaSlug": f"m-{i}"}
            for i in range(nox_mod._BROWSE_MIN_RESULTS)
        ]
        first = respx.get(f"{_BASE}/api/releases/recent/1").respond(200, json=many)
        later = respx.get(f"{_BASE}/api/releases/recent/3").respond(200, json=many)

        results = await plugin.search("")
        await plugin.cleanup()

        assert first.called
        assert not later.called
        assert len(results) == nox_mod._BROWSE_MIN_RESULTS


class TestNoxCleanup:
    """Tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self, nox_mod):
        p = _make_plugin(nox_mod)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        p._client = mock_client

        await p.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert p._client is None

    @pytest.mark.asyncio
    async def test_cleanup_without_client(self, nox_mod):
        p = _make_plugin(nox_mod)

        await p.cleanup()  # Should not raise


# ---------------------------------------------------------------------------
# Grab-time link resolution
# ---------------------------------------------------------------------------

_RELEASE_PAGE = "https://nox.to/media/iron-man-3-r1rz58EAW-?release=122846"
_RELEASE_SLUG = "iron-man-3-german-dl-1080p-bluray-x264-exquisite"

# cost 1 + one-hex-digit prefix: solved within a few attempts
_CHALLENGE = {
    "parameters": {
        "algorithm": "PBKDF2/SHA-256",
        "cost": 1,
        "keyLength": 32,
        "keyPrefix": "0",
        "nonce": "819e0a36785d38c70fc29964db8e2344",
        "salt": "03e7caa891db9dec7ae70c388b35dd8a",
    },
    "signature": "sig",
}

_LINKS = [
    {"id": 1, "hoster": "filer.net", "isOffline": False, "downloadToken": "tokA"},
    {"id": 2, "hoster": "rapidgator.net", "isOffline": True, "downloadToken": "tokB"},
    {"id": 3, "hoster": "tolink.to", "isOffline": False, "downloadToken": "tokC"},
]


def _mock_gateway(
    *,
    base: str = _BASE,
    verify_status: int = 200,
    links: list[dict] | None = None,
) -> respx.Route:
    """Route media, release, captcha and gateway endpoints; returns verify."""
    respx.get(f"{base}/api/media/iron-man-3-r1rz58EAW-").respond(200, json=_MEDIA_MOVIE)
    respx.get(f"{base}/api/releases/{_RELEASE_SLUG}").respond(
        200, json={"links": _LINKS if links is None else links}
    )
    respx.get(f"{base}/api/captcha/challenge").respond(200, json=_CHALLENGE)
    verify = respx.post(f"{base}/api/captcha/verify").respond(
        verify_status,
        json={"token": "pass"} if verify_status == 200 else {"error": "invalid"},
    )
    respx.get(f"{base}/go/tokA/url", params={"cp": "pass"}).respond(
        200, json={"url": "https://filer.net/folder/a"}
    )
    respx.get(f"{base}/go/tokC/url", params={"cp": "pass"}).respond(
        200, json={"url": "https://tolink.to/f/c"}
    )
    return verify


def _gateway_calls() -> list[str]:
    return [str(c.request.url) for c in respx.calls if "/go/" in str(c.request.url)]


class TestNoxResolveDownload:
    """Tests for NoxPlugin.resolve_download() (ALTCHA gateway)."""

    @pytest.fixture()
    def plugin(self, nox_mod):
        return _make_plugin(nox_mod)

    def test_is_grab_resolving(self, plugin):
        assert isinstance(plugin, GrabResolvingPlugin)

    @respx.mock
    @pytest.mark.asyncio
    async def test_online_links_are_unlocked_with_one_captcha(self, plugin):
        verify = _mock_gateway()

        urls = await plugin.resolve_download(_RELEASE_PAGE)
        await plugin.cleanup()

        assert urls == ["https://filer.net/folder/a", "https://tolink.to/f/c"]
        assert verify.call_count == 1
        payload = json.loads(verify.calls[0].request.content)["payload"]
        body = json.loads(base64.b64decode(payload))
        assert body["challenge"]["signature"] == "sig"
        assert body["solution"]["derivedKey"].startswith("0")
        assert not any("tokB" in url for url in _gateway_calls())

    @respx.mock
    @pytest.mark.asyncio
    async def test_failed_captcha_returns_nothing(self, plugin):
        _mock_gateway(verify_status=400)

        urls = await plugin.resolve_download(_RELEASE_PAGE)
        await plugin.cleanup()

        assert urls == []
        assert _gateway_calls() == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_blocked_link_is_skipped(self, plugin):
        _mock_gateway()
        respx.get(f"{_BASE}/go/tokC/url", params={"cp": "pass"}).respond(
            403, json={"message": "blocked", "reason": "hourly_limit"}
        )

        urls = await plugin.resolve_download(_RELEASE_PAGE)
        await plugin.cleanup()

        assert urls == ["https://filer.net/folder/a"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_offline_release_needs_no_captcha(self, plugin):
        verify = _mock_gateway(links=[_LINKS[1]])

        urls = await plugin.resolve_download(_RELEASE_PAGE)
        await plugin.cleanup()

        assert urls == []
        assert verify.call_count == 0

    @respx.mock
    @pytest.mark.asyncio
    async def test_unknown_release_returns_nothing(self, plugin):
        _mock_gateway()

        urls = await plugin.resolve_download(
            "https://nox.to/media/iron-man-3-r1rz58EAW-?release=1"
        )
        await plugin.cleanup()

        assert urls == []

    @pytest.mark.asyncio
    async def test_non_release_url_returns_nothing(self, plugin):
        assert await plugin.resolve_download("https://filer.net/folder/a") == []
        assert await plugin.resolve_download(f"{_BASE}/media/x") == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_uses_the_domain_of_the_url(self, plugin):
        _mock_gateway(base="https://nox.tv")

        urls = await plugin.resolve_download(_RELEASE_PAGE.replace("nox.to", "nox.tv"))
        await plugin.cleanup()

        assert urls == ["https://filer.net/folder/a", "https://tolink.to/f/c"]
