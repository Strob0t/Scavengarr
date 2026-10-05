"""Plugin parsers against pages captured from the live sites.

The other plugin tests feed hand-written HTML shaped like what the parsers
expect, so a parser that drifted from a site's markup still passes them.
These pages are the sites' own (captured 2026-10-01, the per-visitor
``dle_login_hash`` scrubbed, gzipped). Recapture them from a live run when
a site changes its theme, and update the expected values.
"""

from __future__ import annotations

import gzip
import importlib.util
import inspect
import sys
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from scavengarr.infrastructure.plugins import devideosrc

_ROOT = Path(__file__).resolve().parents[3]
_PAGES = _ROOT / "tests" / "fixtures" / "html"


def _page(site: str, name: str) -> str:
    return gzip.decompress((_PAGES / site / f"{name}.html.gz").read_bytes()).decode()


# Plugin files named other than the plugin (fixtures go by plugin name)
_PLUGIN_FILES = {"filmpalast": "filmpalast_to"}


@cache
def _plugin_module(site: str) -> ModuleType:
    path = _ROOT / "plugins" / f"{_PLUGIN_FILES.get(site, site)}.py"
    spec = importlib.util.spec_from_file_location(f"{site}_real_pages", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _search_hits(site: str, base_url: str, name: str) -> list[dict[str, Any]]:
    parser = _plugin_module(site)._SearchResultParser(base_url)
    parser.feed(_page(site, name))
    return parser.results


def _detail(site: str, base_url: str, name: str) -> Any:
    parser_cls = _plugin_module(site)._DetailPageParser
    takes_url = bool(inspect.signature(parser_cls).parameters)
    parser = parser_cls(base_url) if takes_url else parser_cls()
    parser.feed(_page(site, name))
    finalize = getattr(parser, "finalize", None)
    if finalize is not None:
        finalize()
    return parser


def _hit(hits: list[dict[str, Any]], title: str) -> dict[str, Any]:
    matching = [h for h in hits if h["title"] == title]
    assert len(matching) == 1, [h["title"] for h in hits]
    return matching[0]


# hdfilme, streamcloud and streamkiste: one DLE database behind three themes,
# hoster links from the devideosrc player of the detail page
_DEVIDEOSRC_SITES = [
    (
        "hdfilme",
        "https://hdfilme.ceo",
        "https://hdfilme.ceo/filme1/23684-oppenheimer-stream.html",
    ),
    (
        "streamcloud",
        "https://streamcloud.download",
        "https://streamcloud.download/23684-oppenheimer-stream-deutsch.html",
    ),
    (
        "streamkiste",
        "https://streamkiste.bid",
        "https://streamkiste.bid/movie/23684-oppenheimer-stream-kostenlos.html",
    ),
]


@pytest.mark.parametrize(("site", "base_url", "film_url"), _DEVIDEOSRC_SITES)
class TestDevideosrcSites:
    def test_search_lists_the_film(
        self, site: str, base_url: str, film_url: str
    ) -> None:
        hits = _search_hits(site, base_url, "search-oppenheimer")
        assert _hit(hits, "Oppenheimer")["url"] == film_url

    def test_detail_page_embeds_the_movie_player(
        self, site: str, base_url: str, film_url: str
    ) -> None:
        player = devideosrc.find_player(_page(site, "detail-oppenheimer"))
        assert player == devideosrc.DevideosrcPlayer(kind="movie", imdb_id="tt15398776")

    def test_detail_genres(self, site: str, base_url: str, film_url: str) -> None:
        detail = _detail(site, base_url, "detail-oppenheimer")
        assert detail.genres[:4] == ["Drama", "Historie", "Krieg", "Biographie"]
        assert not detail.is_series

    def test_detail_year(self, site: str, base_url: str, film_url: str) -> None:
        """The film's year, not one of the related films listed below it."""
        assert _detail(site, base_url, "detail-oppenheimer").year == "2023"


class TestStreamkiste:
    """The current theme's markup (span.movie-release, span/strong.average)."""

    _BASE = "https://streamkiste.bid"

    def test_search_hit_year_and_genres(self) -> None:
        hit = _hit(
            _search_hits("streamkiste", self._BASE, "search-oppenheimer"), "Oppenheimer"
        )
        assert hit["year"] == "2023"
        assert hit["genres"] == ["Drama", "Historie", "Krieg", "Biographie"]

    def test_detail_imdb_rating(self) -> None:
        detail = _detail("streamkiste", self._BASE, "detail-oppenheimer")
        assert detail.imdb_rating == "8.2"


class TestKinoger:
    _BASE = "https://kinoger.com"

    def test_search_lists_the_film(self) -> None:
        hit = _hit(
            _search_hits("kinoger", self._BASE, "search-oppenheimer"), "Oppenheimer"
        )
        assert hit["url"] == "https://kinoger.com/stream/13171-oppenheimer-stream.html"
        assert hit["genres"] == ["Biography", "Drama", "History", "Thriller"]
        assert hit["is_series"] is False

    def test_search_marks_the_series(self) -> None:
        hits = _search_hits("kinoger", self._BASE, "search-the-last-of-us")
        hit = _hit(hits, "The Last of Us")
        assert hit["badge"] == "S01-02E01-07"
        assert hit["is_series"] is True

    def test_film_stream_tabs(self) -> None:
        detail = _detail("kinoger", self._BASE, "detail-oppenheimer")
        assert detail.title == "Oppenheimer"
        assert [(lk["hoster"], lk["link"]) for lk in detail.stream_links] == [
            ("fsst", "https://fsst.online/embed/973704/"),
            ("veev", "https://veev.pro/e/i3qros8xwd0d"),
            ("kinoger", "https://kinoger.pw/e/5wCjBALU9QDHF"),
        ]

    def test_film_player_is_not_an_episode_list(self) -> None:
        """A film's player tab carries the episode widget too, hidden
        (``<ul id="kinog-serial" style="display: none;">``, one ``1-1``
        entry "1 Часть"): no episode labels, not a series."""
        detail = _detail("kinoger", self._BASE, "detail-oppenheimer")
        assert not detail.is_series
        assert [lk["label"] for lk in detail.stream_links] == [
            "Stream HD+",
            "Stream HD+",
            "Stream HD",
        ]

    def test_film_with_a_single_player(self) -> None:
        """A page with one player has no tabs: the player
        (``<div id="container-video">``) sits outside any ``<section>``."""
        detail = _detail("kinoger", self._BASE, "detail-john-wick-kapitel-4")
        assert not detail.is_series
        assert detail.stream_links == [
            {
                "hoster": "fsst",
                "link": "https://fsst.online/embed/992734/",
                "label": "",
            }
        ]

    def test_series_with_a_single_player(self) -> None:
        detail = _detail("kinoger", self._BASE, "detail-wednesday")
        assert detail.is_series
        assert len(detail.stream_links) == 8 + 8
        assert detail.stream_links[0] == {
            "hoster": "fsst",
            "link": "https://fsst.online/embed/991364/",
            "label": "1x1 ",
        }
        assert detail.stream_links[8]["link"] == "https://fsst.online/embed/991372/"
        assert detail.stream_links[8]["label"] == "2x1 "

    async def test_film_request_gets_the_film(self) -> None:
        """The search also lists a series ("J. Robert Oppenheimer -
        Atomphysiker"); its detail page loads concurrently and is empty."""
        plugin = _plugin_module("kinoger").KinogerPlugin()
        plugin._domain_verified = True

        async def get(url: str, **kwargs: Any) -> httpx.Response:
            if url.endswith("/index.php"):
                first = "search_start" not in kwargs.get("params", {})
                text = _page("kinoger", "search-oppenheimer") if first else ""
            elif "13171-oppenheimer" in url:
                text = _page("kinoger", "detail-oppenheimer")
            else:
                text = ""
            return httpx.Response(200, text=text, request=httpx.Request("GET", url))

        client = AsyncMock()
        client.get = AsyncMock(side_effect=get)
        plugin._client = client

        results = await plugin.search("Oppenheimer", 2000)

        assert [(r.title, r.category) for r in results] == [("Oppenheimer", 2000)]
        assert results[0].download_link == "https://fsst.online/embed/973704/"

    def test_film_detail_metadata(self) -> None:
        """Year from the title ``Oppenheimer (2023)``, genres from the
        category list (``<li class="category">``)."""
        detail = _detail("kinoger", self._BASE, "detail-oppenheimer")
        assert detail.year == "2023"
        assert detail.genres == ["Biography", "Drama", "History", "Thriller"]

    def test_series_detail_metadata(self) -> None:
        detail = _detail("kinoger", self._BASE, "detail-the-last-of-us")
        assert detail.title == "The Last of Us"
        assert detail.year == "2023"
        assert detail.genres == ["Abenteuer", "Drama", "Horror", "Serie", "Thriller"]

    def test_series_tabs_list_every_episode(self) -> None:
        """Each player tab lists both seasons (9 + 7 episodes) as
        ``<span onclick="pw.player('<url>', this);" data-id="1-1">``."""
        detail = _detail("kinoger", self._BASE, "detail-the-last-of-us")
        assert detail.is_series
        assert len(detail.stream_links) == 4 * (9 + 7)
        assert detail.stream_links[0] == {
            "hoster": "fsst",
            "link": "https://fsst.online/embed/1028225/",
            "label": "1x1 Stream HD+",
        }
        fsst = [lk for lk in detail.stream_links if lk["hoster"] == "fsst"]
        assert [lk["label"] for lk in fsst[8:10]] == [
            "1x9 Stream HD+",
            "2x1 Stream HD+",
        ]
        assert fsst[9]["link"] == "https://fsst.online/embed/1028233/"

    async def test_episode_request_gets_that_episode(self) -> None:
        plugin = _plugin_module("kinoger").KinogerPlugin()
        plugin._domain_verified = True
        pages = [
            _page("kinoger", "search-the-last-of-us"),
            "",  # search page 2: no more hits
            _page("kinoger", "detail-the-last-of-us"),
        ]
        request = httpx.Request("GET", "https://kinoger.com/")
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[httpx.Response(200, text=p, request=request) for p in pages]
        )
        plugin._client = client

        results = await plugin.search("The Last of Us", 5000, season=1, episode=5)

        assert len(results) == 1
        assert results[0].category == 5000
        assert [lk["link"] for lk in results[0].download_links or []] == [
            "https://fsst.online/embed/1028220/",
            "https://kinoger.pw/e/a2hA9FVKnoyGU",
            "https://firestream.site/e/D9ExMIOv",
            "https://kinoger.ru/e/v30jzqij3g8w",
        ]
        assert results[0].download_link == "https://fsst.online/embed/1028220/"


class TestMegakino:
    _BASE = "https://megakino21.com"

    def test_search_lists_the_film(self) -> None:
        hit = _hit(
            _search_hits("megakino", self._BASE, "search-oppenheimer"), "Oppenheimer"
        )
        assert hit["url"] == "https://megakino21.com/films/3133-oppenheimer.html"
        assert hit["year"] == "2023"
        assert hit["genres"] == ["Drama", "History"]

    def test_film_detail(self) -> None:
        detail = _detail("megakino", self._BASE, "detail-oppenheimer")
        assert detail.title == "Oppenheimer"
        assert detail.year == "2023"
        # The plot, not the last user comment (also a "full-text" div)
        assert detail.description.startswith("In einer Anhörung über seinen")
        assert not detail.is_series
        assert [lk["link"] for lk in detail.stream_links] == [
            "https://voe.sx/e/qld0n0mjfq3y",
            "https://watch.gxplayer.xyz/watch?v=4QGFS5I1",
        ]

    def test_season_page_labels_its_episodes(self) -> None:
        detail = _detail("megakino", self._BASE, "detail-the-last-of-us-1-staffel")
        assert detail.is_series
        assert [lk["label"] for lk in detail.stream_links] == [
            f"1x{n} Voe" for n in range(1, 10)
        ]


def _mock_client(*pages: str) -> AsyncMock:
    """httpx client answering GETs with *pages* in order."""
    request = httpx.Request("GET", "https://site.example/")
    client = AsyncMock()
    client.get = AsyncMock(
        side_effect=[httpx.Response(200, text=p, request=request) for p in pages]
    )
    return client


class TestAniworld:
    _BASE = "https://aniworld.to"

    def test_series_page_points_to_the_first_episode(self) -> None:
        detail = _detail("aniworld", self._BASE, "detail-attack-on-titan")
        assert detail.first_episode_url == (
            "https://aniworld.to/anime/stream/attack-on-titan/staffel-1/episode-1"
        )
        assert detail.genres[:4] == ["Actiondrama", "Abenteuer", "Action", "Drama"]

    def test_series_page_full_description(self) -> None:
        """The page names its plot in <p class="seri_des" data-full-description>;
        the parser read div.seri_des only and left the description empty."""
        detail = _detail("aniworld", self._BASE, "detail-attack-on-titan")
        assert detail.description.startswith("Vor mehreren hundert Jahren")
        assert len(detail.description) > 500

    def test_episode_hosters_per_language(self) -> None:
        parser = _plugin_module("aniworld")._EpisodePageParser(self._BASE)
        parser.feed(_page("aniworld", "episode-attack-on-titan-s01e01"))
        links = [(h["hoster"], h["language"]) for h in parser.hoster_links]
        assert links[:5] == [
            ("voe", "German Dub"),
            ("doodstream", "German Dub"),
            ("filemoon", "German Dub"),
            ("vidmoly", "German Dub"),
            ("voe", "English Sub"),
        ]
        assert parser.hoster_links[0]["link"] == "https://aniworld.to/redirect/3540458"


class TestSto:
    _BASE = "https://serienstream.to"

    def test_search_finds_the_series(self) -> None:
        parser = _plugin_module("sto")._SearchSeriesParser(self._BASE)
        parser.feed(_page("sto", "search-the-last-of-us"))
        assert parser.results == [
            {
                "title": "The Last of Us",
                "url": "https://serienstream.to/serie/the-last-of-us",
                "slug": "the-last-of-us",
            }
        ]

    def test_series_page_lists_seasons_and_episodes(self) -> None:
        parser = _plugin_module("sto")._SeriesDetailParser(self._BASE)
        parser.feed(_page("sto", "detail-the-last-of-us"))
        assert parser.title == "The Last of Us"
        assert parser.seasons == [1, 2]
        assert len(parser.episodes) == 9
        assert (
            parser.episodes[0]["de_title"] == "Wenn Du in der Dunkelheit verloren bist"
        )

    def test_episode_hosters_per_language(self) -> None:
        """The redirect tokens are scrubbed in the fixture (``/r?t=scrubbed``)."""
        parser = _plugin_module("sto")._EpisodeHosterParser()
        parser.feed(_page("sto", "episode-the-last-of-us-s01e01"))
        assert [(h["provider"], h["language"]) for h in parser.hosters] == [
            ("VOE", "Deutsch"),
            ("VOE", "Englisch"),
        ]
        assert parser.hosters[0]["play_url"] == "/r?t=scrubbed"


class TestKinoking:
    def test_search_card(self) -> None:
        parser = _plugin_module("kinoking")._SearchCardParser()
        parser.feed(_page("kinoking", "search-oppenheimer"))
        assert parser.results == [
            {
                "id": "13723",
                "type": "movie",
                "title": "Oppenheimer",
                "tmdb": "872585",
                "quality": "HD",
            }
        ]

    def test_movie_servers(self) -> None:
        links = _plugin_module("kinoking")._movie_links(
            _page("kinoking", "movie-oppenheimer")
        )
        assert len(links) == 19
        assert links[0] == {
            "hoster": "filemoon",
            "link": "https://filemoon.to/e/fwzwu9ny19jk",
        }

    def test_series_episodes(self) -> None:
        mod = _plugin_module("kinoking")
        episodes = mod._load_json_array(
            mod._EPISODES_RE, _page("kinoking", "series-the-last-of-us")
        )
        assert len(episodes) == 9 + 7
        picked = mod._pick_episodes(episodes, 1, 5)
        assert [(e["season_number"], e["episode_number"]) for e in picked] == [(1, 5)]
        assert mod._episode_links(episodes[0]) == [
            {"hoster": "voe", "link": "https://voe.sx/e/jwf9glrkk7sd"}
        ]


class TestFilmpalast:
    _FILM = "//filmpalast.to/stream/oppenheimer"

    def _hits(self, name: str) -> list[dict[str, Any]]:
        parser = _plugin_module("filmpalast")._SearchResultParser()
        parser.feed(_page("filmpalast", name))
        return parser.results

    def test_search_lists_the_film(self) -> None:
        assert _hit(self._hits("search-oppenheimer"), "Oppenheimer") == {
            "title": "Oppenheimer",
            "detail_url": self._FILM,
        }

    async def test_film_request_scrapes_the_relevant_hit_only(self) -> None:
        """The site search also lists "Fireball: Visitors from Darker Worlds"
        and "Into the Inferno"; each detail page is 220 KB."""
        plugin = _plugin_module("filmpalast").FilmpalastPlugin()
        plugin._domain_verified = True
        plugin._client = AsyncMock()
        plugin._search_all = AsyncMock(return_value=self._hits("search-oppenheimer"))
        plugin._scrape_detail = AsyncMock(return_value=("", "", []))

        await plugin.search("Oppenheimer", 2000)

        scraped = [c.args[0] for c in plugin._scrape_detail.await_args_list]
        assert scraped == ["https://filmpalast.to/stream/oppenheimer"]

    async def test_episode_request_skips_other_series(self) -> None:
        """Episodes are listed as "<series> S01E01": "Dark" also finds
        "Dark Matter S01E01" and "His Dark Materials S01E01"."""
        plugin = _plugin_module("filmpalast").FilmpalastPlugin()
        plugin._domain_verified = True
        plugin._client = AsyncMock()
        plugin._search_all = AsyncMock(
            return_value=[
                {"title": f"{name} S01E01", "detail_url": f"/stream/{slug}-s01e01"}
                for name, slug in (
                    ("Dark Matter", "dark-matter"),
                    ("Dark", "dark"),
                    ("His Dark Materials", "his-dark-materials"),
                )
            ]
        )
        plugin._scrape_detail = AsyncMock(return_value=("", "", []))

        await plugin.search("Dark", 5000, season=1, episode=1)

        scraped = [c.args[0] for c in plugin._scrape_detail.await_args_list]
        assert scraped == ["https://filmpalast.to/stream/dark-s01e01"]

    def test_film_detail(self) -> None:
        detail = _detail("filmpalast", "https://filmpalast.to", "detail-oppenheimer")
        assert detail.title.strip() == "Oppenheimer"
        assert detail.release_name.strip() == (
            "Oppenheimer.2023.German.DL.1080p.BluRay.x264.RERiP-DETAiLS"
        )
        assert [lk["link"] for lk in detail.links] == ["https://voe.sx/xhoyeqr1jx6s"]

    def test_episode_detail(self) -> None:
        detail = _detail(
            "filmpalast", "https://filmpalast.to", "detail-the-last-of-us-s01e01"
        )
        assert detail.title.strip() == "The Last of Us S01E01"
        assert [lk["link"] for lk in detail.links] == [
            "https://firestream.to/e/OXIURmQ-",
            "https://vidaraa.cc/e/ezpjajrJ48BF2",
            "https://voe.sx/laxed19ikr7i",
            "https://vidsonic.net/e/r00nm90ihj96",
        ]


class TestMovie2k:
    _BASE = "https://movie2k.cx"
    _FILM = "https://movie2k.cx/stream/oppenheimer--cmja7rspz0001kvuydpbyul22"

    def test_search_lists_the_film(self) -> None:
        hits = _search_hits("movie2k", self._BASE, "search-oppenheimer")
        assert _hit(hits, "Oppenheimer")["url"] == self._FILM

    async def test_film_request_scrapes_the_relevant_hit_only(self) -> None:
        """The site search also lists "Fireball" and "Die Schattenmacher"."""
        plugin = _plugin_module("movie2k").Movie2kPlugin()
        plugin._domain_verified = True
        plugin._client = AsyncMock()
        plugin._search_page = AsyncMock(
            return_value=_search_hits("movie2k", self._BASE, "search-oppenheimer")
        )
        plugin._scrape_detail = AsyncMock(return_value=None)

        await plugin.search("Oppenheimer", 2000)

        scraped = [c.args[0]["url"] for c in plugin._scrape_detail.await_args_list]
        assert scraped == [self._FILM]

    def test_film_detail(self) -> None:
        detail = _detail("movie2k", self._BASE, "detail-oppenheimer")
        assert detail.title == "Oppenheimer"
        assert detail.year == "2023"
        assert [lk["link"] for lk in detail.stream_links] == [
            "https://voe.sx/e/j14flkqjrl14",
            "https://vinovo.to/e/jged78v1upyvn5",
        ]
        # The plot, not the page's inline script (the longest text block)
        assert detail.description.startswith("Als dem Physiker Julius Robert")

    def test_series_page_lists_every_episode(self) -> None:
        """Episodes are ``<table data-episode-id="base64(tt…-s1e1-…)">`` with
        ``<a href="#" onclick="return loadMirror('<url>')">`` mirrors."""
        detail = _detail("movie2k", self._BASE, "detail-the-last-of-us")
        assert detail.episodes_listed
        assert detail.stream_links[:2] == [
            {
                "hoster": "vidoza.net",
                "link": "https://vidoza.net/n1318q60i9gh.html",
                "quality": "HD",
                "label": "1x1 vidoza.net",
            },
            {
                "hoster": "vinovo.to",
                "link": "https://vinovo.to/d/kgv90ek1s821qg",
                "quality": "HD",
                "label": "1x1 vinovo.to",
            },
        ]
        seasons = {lk["label"].split("x")[0] for lk in detail.stream_links}
        assert seasons == {"1"}

    async def test_episode_request_gets_that_episode(self) -> None:
        plugin = _plugin_module("movie2k").Movie2kPlugin()
        plugin._domain_verified = True
        plugin._client = _mock_client(
            _page("movie2k", "search-the-last-of-us"),
            _page("movie2k", "detail-the-last-of-us"),
        )

        results = await plugin.search("The Last of Us", 5000, season=1, episode=1)

        assert len(results) == 1
        assert results[0].category == 5000
        labels = [lk["label"] for lk in results[0].download_links or []]
        assert labels and all(label.startswith("1x1 ") for label in labels)
