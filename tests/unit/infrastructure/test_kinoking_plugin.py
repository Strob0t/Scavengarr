"""Tests for the kinoking.cc Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "kinoking.py"
_BASE = "https://kinoking.cc"


def _load_module() -> ModuleType:
    """Load kinoking.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("kinoking_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_KinokingPlugin = _mod.KinokingPlugin
_SearchCardParser = _mod._SearchCardParser


def _make_plugin() -> object:
    plug = _KinokingPlugin()
    plug._domain_verified = True
    return plug


# ---------------------------------------------------------------------------
# Fixtures (markup from the live site, 2026-09-28)
# ---------------------------------------------------------------------------


def _card(card_id: str, card_type: str, title: str) -> str:
    return (
        '<div class="group/card relative fav-data-source cursor-pointer" '
        f'data-id="{card_id}" data-type="{card_type}" data-tmdb="1726" '
        f'data-title="{title}" data-quality="HD"><p>{title}</p></div>'
    )


_TEMPLATE_CARD = (
    '<div class="fav-data-source" data-id="${item.id}" '
    'data-type="${targetType}" data-title="${item.title}"></div>'
)

_SEARCH_HTML = (
    "<html><body>"
    + _card("12712", "movie", "Iron Man")
    + _card("10767", "series", "Iron Man - die Zukunft beginnt")
    + _card("12712", "movie", "Iron Man")  # duplicate card
    + _TEMPLATE_CARD
    + "</body></html>"
)

_SERVERS = [
    {
        "id": "meinecloud_backup",
        "name": "Backup Yes",
        "mirrors": ["https://meinecloud.click/movie/tt0371746"],
    },
    {
        "id": "backup",
        "name": "VIDSYNC AUTO",
        "mirrors": ["https://vidsync.xyz/embed/movie/1726"],
    },
    {
        "id": "group_huhu_erver1",
        "name": "Server A1 (DE)",
        "mirrors": ["https://dood.to/e/de1"],
    },
    {
        "id": "group_huhu_erver1",
        "name": "Server B1 (EN)",
        "mirrors": ["https://dood.yt/w/en1"],
    },
    {
        "id": "group_filmo_",
        "name": "VOE (FILMO DE)",
        "mirrors": ["https://voe.sx/e/filmo1", "https://dood.to/e/de1"],
    },
    {
        "id": "group_filmo_",
        "name": "VOE (FILMO FR)",
        "mirrors": ["https://voe.sx/e/fr1"],
    },
    {
        "id": "group_kk_ares",
        "name": "ARES",
        "mirrors": ["https://streamtape.com/e/kk1"],
    },
    {
        "id": "group_kk_mixdrop_x",
        "name": "MIXDROP",
        "mirrors": ["https://mixdrop.ag/e/kk2"],
    },
    {
        "id": "dyn_fpto_3c35d3",
        "name": "VOE (LIVE)",
        "mirrors": [f"https://voe.sx/e/live{i}" for i in range(5)],
    },
    {
        "id": "cinesrc_backup",
        "name": "Kinoking UP",
        "mirrors": ["https://cinesrc.st/embed/movie/1726"],
    },
]

_MOVIE_HTML = f"""<html><body><h1>Iron Man</h1>
<script>
    const SERVERS = {json.dumps(_SERVERS)};
    const other = 1;
</script></body></html>"""

_EPISODES = [
    {
        "season_number": 1,
        "episode_number": 2,
        "name": "Folge 2",
        "video_links": "https://voe.sx/e/s1e2",
    },
    {
        "season_number": 1,
        "episode_number": 1,
        "name": "Folge 1",
        "video_links": "https://voe.sx/e/s1e1, https://dood.to/e/s1e1",
        "custom_video_url": None,
    },
    {"season_number": 1, "episode_number": 3, "name": "Folge 3", "video_links": ""},
    {
        "season_number": 2,
        "episode_number": 1,
        "name": "Folge 1",
        "video_links": "",
        "custom_video_url": "https://voe.sx/e/s2e1",
    },
]

_SERIES_HTML = f"""<html><body><h1>KINOKING</h1>
<script>
    const seriesTitle = "Iron Man - die Zukunft beginnt";
    const allEpisodesData = {json.dumps(_EPISODES)};
</script></body></html>"""


def _mock_site(search_html: str = _SEARCH_HTML) -> respx.Route:
    search = respx.get(_BASE + "/index.php").mock(
        side_effect=lambda request: httpx.Response(
            200,
            text=search_html if request.url.params.get("page") == "1" else "",
        )
    )
    respx.get(_BASE + "/movie.php").respond(200, text=_MOVIE_HTML)
    respx.get(_BASE + "/series.php").respond(200, text=_SERIES_HTML)
    return search


# ---------------------------------------------------------------------------
# Parser / helper tests
# ---------------------------------------------------------------------------


class TestSearchCardParser:
    def test_cards_parsed_deduped_templates_skipped(self) -> None:
        parser = _SearchCardParser()
        parser.feed(_SEARCH_HTML)

        assert parser.results == [
            {
                "id": "12712",
                "type": "movie",
                "title": "Iron Man",
                "tmdb": "1726",
                "quality": "HD",
            },
            {
                "id": "10767",
                "type": "series",
                "title": "Iron Man - die Zukunft beginnt",
                "tmdb": "1726",
                "quality": "HD",
            },
        ]

    def test_other_divs_ignored(self) -> None:
        parser = _SearchCardParser()
        parser.feed('<div data-id="1" data-type="movie" data-title="x"></div>')
        assert parser.results == []


class TestMovieLinks:
    def test_filters_servers(self) -> None:
        links = [link["link"] for link in _mod._movie_links(_MOVIE_HTML)]

        assert links == [
            "https://dood.to/e/de1",
            "https://voe.sx/e/filmo1",  # dood.to/e/de1 deduped
            "https://voe.sx/e/live0",
            "https://voe.sx/e/live1",
            "https://voe.sx/e/live2",  # capped per server
        ]

    def test_hoster_names(self) -> None:
        links = _mod._movie_links(_MOVIE_HTML)
        assert {link["hoster"] for link in links} == {"dood", "voe"}

    def test_no_servers(self) -> None:
        assert _mod._movie_links("<html></html>") == []
        assert _mod._movie_links("const SERVERS = [not json];\n") == []


class TestEpisodes:
    def test_first_season_sorted_by_default(self) -> None:
        eps = _mod._pick_episodes(_EPISODES, None, None)
        assert [(e["season_number"], e["episode_number"]) for e in eps] == [
            (1, 1),
            (1, 2),
            (1, 3),
        ]

    def test_season_and_episode(self) -> None:
        eps = _mod._pick_episodes(_EPISODES, 1, 2)
        assert [e["name"] for e in eps] == ["Folge 2"]

    def test_unknown_season(self) -> None:
        assert _mod._pick_episodes(_EPISODES, 9, None) == []

    def test_episode_links_split_and_custom_url(self) -> None:
        assert [link["link"] for link in _mod._episode_links(_EPISODES[1])] == [
            "https://voe.sx/e/s1e1",
            "https://dood.to/e/s1e1",
        ]
        assert [link["link"] for link in _mod._episode_links(_EPISODES[3])] == [
            "https://voe.sx/e/s2e1"
        ]
        assert _mod._episode_links(_EPISODES[2]) == []


# ---------------------------------------------------------------------------
# Plugin tests (respx)
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name(self) -> None:
        assert _make_plugin().name == "kinoking"

    def test_mode(self) -> None:
        assert _make_plugin().mode == "httpx"

    def test_base_url(self) -> None:
        assert _KinokingPlugin().base_url == _BASE


class TestSearch:
    @respx.mock
    @pytest.mark.asyncio
    async def test_movies_and_series(self) -> None:
        plug = _make_plugin()
        _mock_site()

        results = await plug.search("Iron Man")
        await plug.cleanup()

        titles = [r.title for r in results]
        assert "Iron Man" in titles
        # S1 episodes with links (E3 has none)
        assert "Iron Man - die Zukunft beginnt S01E01" in titles
        assert "Iron Man - die Zukunft beginnt S01E02" in titles
        assert len(results) == 3
        movie = next(r for r in results if r.title == "Iron Man")
        assert movie.category == 2000
        assert movie.source_url == f"{_BASE}/movie.php?id=12712"
        episode = next(r for r in results if r.title.endswith("S01E01"))
        assert episode.category == 5000
        assert episode.source_url == f"{_BASE}/series.php?id=10767"

    @respx.mock
    @pytest.mark.asyncio
    async def test_loose_matches_are_not_scraped(self) -> None:
        # A cold movie page takes up to 15 s on this site: only hits whose
        # title contains every query word get their page loaded
        plug = _make_plugin()
        _mock_site(
            "<html><body>"
            + _card("12712", "movie", "Iron Man")
            + _card("999", "movie", "The Iron Giant")
            + "</body></html>"
        )

        results = await plug.search("Iron Man", 2000)
        await plug.cleanup()

        assert [r.title for r in results] == ["Iron Man"]
        movie_ids = [
            c.request.url.params["id"]
            for c in respx.calls
            if c.request.url.path == "/movie.php"
        ]
        assert movie_ids == ["12712"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_paginates_until_empty_page(self) -> None:
        plug = _make_plugin()
        search = _mock_site()

        await plug.search("Iron Man")
        await plug.cleanup()

        pages = [c.request.url.params["page"] for c in search.calls]
        assert pages == ["1", "2"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_season_episode_skips_movies(self) -> None:
        plug = _make_plugin()
        _mock_site()

        results = await plug.search("Iron Man", season=2, episode=1)
        await plug.cleanup()

        assert [r.title for r in results] == ["Iron Man - die Zukunft beginnt S02E01"]
        assert results[0].download_link == "https://voe.sx/e/s2e1"

    @respx.mock
    @pytest.mark.asyncio
    async def test_category_movies_only(self) -> None:
        plug = _make_plugin()
        _mock_site()

        results = await plug.search("Iron Man", category=2000)
        await plug.cleanup()

        assert [r.title for r in results] == ["Iron Man"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_category_series_only(self) -> None:
        plug = _make_plugin()
        _mock_site()

        results = await plug.search("Iron Man", category=5000)
        await plug.cleanup()

        assert all(r.category == 5000 for r in results)
        assert len(results) == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_anime_request_gets_the_series(self) -> None:
        """kinoking does not tell anime apart; results keep the site's label."""
        plug = _make_plugin()
        _mock_site()

        results = await plug.search("Iron Man", category=5070)
        await plug.cleanup()

        assert [r.category for r in results] == [5000, 5000]

    @pytest.mark.asyncio
    async def test_category_the_site_does_not_serve(self) -> None:
        """Audio used to get the films, Books/Other the series."""
        plug = _make_plugin()
        plug._client = AsyncMock(spec=httpx.AsyncClient)

        assert await plug.search("Iron Man", category=3000) == []
        assert await plug.search("Iron Man", category=7000) == []
        plug._client.get.assert_not_awaited()

    @respx.mock
    @pytest.mark.asyncio
    async def test_detail_errors_skipped(self) -> None:
        plug = _make_plugin()
        _mock_site()
        respx.get(_BASE + "/movie.php").respond(500)
        respx.get(_BASE + "/series.php").respond(500)

        assert await plug.search("Iron Man") == []
        await plug.cleanup()

    @respx.mock
    @pytest.mark.asyncio
    async def test_no_cards(self) -> None:
        plug = _make_plugin()
        _mock_site("<html>nothing</html>")

        assert await plug.search("zzz") == []
        await plug.cleanup()

    @pytest.mark.asyncio
    async def test_empty_query(self) -> None:
        plug = _make_plugin()
        plug._client = AsyncMock()
        assert await plug.search("") == []


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        plug._client = mock_client

        await plug.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert plug._client is None
