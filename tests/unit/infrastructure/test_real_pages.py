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
import sys
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from scavengarr.infrastructure.plugins import devideosrc

_ROOT = Path(__file__).resolve().parents[3]
_PAGES = _ROOT / "tests" / "fixtures" / "html"


def _page(site: str, name: str) -> str:
    return gzip.decompress((_PAGES / site / f"{name}.html.gz").read_bytes()).decode()


@cache
def _plugin_module(site: str) -> ModuleType:
    path = _ROOT / "plugins" / f"{site}.py"
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
    parser = _plugin_module(site)._DetailPageParser(base_url)
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
