"""Tests for the movieblog.to plugin."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "movieblog.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "movieblog_plugin", str(_PLUGIN_PATH)
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_MovieblogPlugin = _mod.MovieblogPlugin
_SearchResultParser = _mod._SearchResultParser
_PaginationParser = _mod._PaginationParser
_DetailPageParser = _mod._DetailPageParser


def _make_plugin() -> object:
    p = _MovieblogPlugin()
    p._domain_verified = True
    return p


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------


def _search_result_html(
    title: str = (
        "Iron.Maze-Im.Netz.der.Leidenschaft"
        ".1991.German.AC3D.1080p.DvD2BD.x264-MMS"
    ),
    url: str = (
        "https://movieblog.to/iron-maze-im-netz-der-"
        "leidenschaft-1991-german-ac3d-1080p-dvd2bd-x264-mms/"
    ),
    post_id: int = 132474,
    month: str = "Feb.",
    day: str = "07",
    categories: list[tuple[str, str]] | None = None,
) -> str:
    if categories is None:
        categories = [
            ("https://movieblog.to/category/drama/", "Drama"),
            ("https://movieblog.to/category/hd-1080p/", "HD - 1080p"),
        ]
    cat_links = ", ".join(
        f'<a href="{href}" rel="category tag">{text}</a>'
        for href, text in categories
    )
    return (
        '<div class="post">'
        f'<div class="post-date">'
        f'<span class="post-month">{month}</span>'
        f'<span class="post-day">{day}</span></div>'
        f'<h1 id="post-{post_id}">'
        f'<a href="{url}" rel="bookmark" '
        f'title="Permanent Link to {title}">{title}</a></h1>'
        f'<p class="date_x">Samstag, 7. Februar 2026 9:33</p>'
        f'<div class="entry_x"><p>Description text</p></div>'
        f'<p class="info_x">Thema: {cat_links}'
        f' <strong>| </strong>'
        f'<a href="{url}#respond">Kommentare (0)</a></p>'
        "</div>"
    )


def _search_page_html(
    results: list[str] | None = None,
    nav_html: str = "",
) -> str:
    if results is None:
        results = [_search_result_html()]
    return (
        "<html><body>"
        '<h2 class="archivtitle">Suchergebnisse</h2>'
        + "".join(results)
        + nav_html
        + "</body></html>"
    )


def _navigation_html(
    next_url: str = "https://movieblog.to/page/2/?s=iron+man",
) -> str:
    return (
        '<div class="navigation_x">'
        '<div class="alignleft">'
        '<a href="https://movieblog.to/?s=iron+man">'
        "&laquo; vorherige Beitr\u00e4ge</a></div>"
        '<div class="alignright">'
        f'<a href="{next_url}">'
        "N\u00e4chste Seite &raquo;</a></div>"
        "</div>"
    )


def _detail_page_html(
    links: list[tuple[str, str, str]] | None = None,
    size_text: str = "7,72 GB",
) -> str:
    """Build a detail page HTML.

    Args:
        links: List of (filecrypt_url, label_type, hoster_name) tuples.
               label_type is "Download" or "Mirror #1".
        size_text: File size string.
    """
    if links is None:
        links = [
            (
                "https://www.filecrypt.cc/Container/08191210B1.html",
                "Download",
                "Rapidgator.net",
            ),
            (
                "https://www.filecrypt.cc/Container/AF1EE2D4AE.html",
                "Mirror #1",
                "Ddownload.com",
            ),
        ]
    link_html_parts = []
    for href, label, hoster in links:
        link_html_parts.append(
            f'<strong>{label}: </strong>'
            f'<a href="{href}" target="_blank" rel="noopener">'
            f"{hoster}</a><br />"
        )
    link_html = "\n".join(link_html_parts)
    return (
        "<html><body>"
        '<div class="post">'
        '<div class="entry_x">'
        f"<p><strong>Dauer: </strong>98 Min| "
        f"<strong>Format: </strong>MKV | "
        f"<strong>Gr\u00f6\u00dfe: </strong>{size_text}| "
        f'<a href="https://www.imdb.com/title/tt0102128/">'
        f"IMDb: Iron Maze </a><br />"
        f"{link_html}"
        f"<strong>Passwort: </strong></strong>movieblog.to</p>"
        "</div></div>"
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# SearchResultParser tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    """Tests for _SearchResultParser."""

    def test_parses_movie_result(self) -> None:
        html = _search_page_html()
        parser = _SearchResultParser()
        parser.feed(html)

        assert len(parser.results) == 1
        r = parser.results[0]
        assert "Iron.Maze" in r["title"]
        assert "movieblog.to" in r["url"]
        assert r["category"] == 2000

    def test_parses_series_result(self) -> None:
        html = _search_page_html(
            results=[
                _search_result_html(
                    title="Ironheart.S01.GERMAN.DL.DV.2160P.WEB.H265-RiLE",
                    url="https://movieblog.to/ironheart-s01/",
                    categories=[
                        (
                            "https://movieblog.to/category/serie/",
                            "Serie",
                        ),
                        (
                            "https://movieblog.to/category/uhd-4k/",
                            "UHD - 4K",
                        ),
                    ],
                )
            ]
        )
        parser = _SearchResultParser()
        parser.feed(html)

        assert len(parser.results) == 1
        assert parser.results[0]["category"] == 5000

    def test_parses_multiple_results(self) -> None:
        results = [
            _search_result_html(
                title="Movie A", url="https://movieblog.to/a/", post_id=1
            ),
            _search_result_html(
                title="Movie B", url="https://movieblog.to/b/", post_id=2
            ),
            _search_result_html(
                title="Movie C", url="https://movieblog.to/c/", post_id=3
            ),
        ]
        html = _search_page_html(results=results)
        parser = _SearchResultParser()
        parser.feed(html)

        assert len(parser.results) == 3
        assert parser.results[0]["title"] == "Movie A"
        assert parser.results[2]["title"] == "Movie C"

    def test_skips_post_without_title_link(self) -> None:
        html = (
            "<html><body>"
            '<div class="post">'
            "<h1>No link here</h1>"
            '<p class="info_x">Thema: none</p>'
            "</div>"
            "</body></html>"
        )
        parser = _SearchResultParser()
        parser.feed(html)

        assert len(parser.results) == 0

    def test_empty_page(self) -> None:
        html = "<html><body><h2>Suchergebnisse</h2></body></html>"
        parser = _SearchResultParser()
        parser.feed(html)

        assert len(parser.results) == 0

    def test_default_category_is_movie(self) -> None:
        """Posts without 'Serie' category default to movie (2000)."""
        html = _search_page_html(
            results=[
                _search_result_html(
                    categories=[
                        (
                            "https://movieblog.to/category/action/",
                            "Action",
                        ),
                        (
                            "https://movieblog.to/category/hd-1080p/",
                            "HD - 1080p",
                        ),
                    ],
                )
            ]
        )
        parser = _SearchResultParser()
        parser.feed(html)

        assert parser.results[0]["category"] == 2000


# ---------------------------------------------------------------------------
# PaginationParser tests
# ---------------------------------------------------------------------------


class TestPaginationParser:
    """Tests for _PaginationParser."""

    def test_extracts_next_page_url(self) -> None:
        html = _navigation_html(
            next_url="https://movieblog.to/page/2/?s=test"
        )
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.next_page_url == (
            "https://movieblog.to/page/2/?s=test"
        )

    def test_no_navigation(self) -> None:
        html = "<html><body><p>No results</p></body></html>"
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.next_page_url == ""

    def test_page_2_has_both_directions(self) -> None:
        """Page 2 has both prev+next in alignleft, prev+next in alignright."""
        html = (
            '<div class="navigation_x">'
            '<div class="alignleft">'
            '<a href="https://movieblog.to/?s=test">'
            "&laquo; Vorherige Seite</a>"
            " &#8212; "
            '<a href="https://movieblog.to/page/3/?s=test">'
            "&laquo; vorherige Beitr\u00e4ge</a>"
            "</div>"
            '<div class="alignright">'
            '<a href="https://movieblog.to/?s=test">'
            "n\u00e4chste Beitr\u00e4ge &raquo;</a>"
            " &#8212; "
            '<a href="https://movieblog.to/page/3/?s=test">'
            "N\u00e4chste Seite &raquo;</a>"
            "</div>"
            "</div>"
        )
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.next_page_url == (
            "https://movieblog.to/page/3/?s=test"
        )

    def test_last_page_has_no_next(self) -> None:
        """On the last page, no Nächste Seite link exists."""
        html = (
            '<div class="navigation_x">'
            '<div class="alignleft">'
            '<a href="https://movieblog.to/?s=test">'
            "&laquo; vorherige Beitr\u00e4ge</a>"
            "</div>"
            '<div class="alignright">'
            '<a href="https://movieblog.to/?s=test">'
            "n\u00e4chste Beitr\u00e4ge &raquo;</a>"
            "</div>"
            "</div>"
        )
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.next_page_url == ""


# ---------------------------------------------------------------------------
# DetailPageParser tests
# ---------------------------------------------------------------------------


class TestDetailPageParser:
    """Tests for _DetailPageParser."""

    def test_parses_two_filecrypt_links(self) -> None:
        html = _detail_page_html()
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.download_links) == 2
        assert parser.download_links[0]["hoster"] == "rapidgator"
        assert "08191210B1" in parser.download_links[0]["link"]
        assert parser.download_links[1]["hoster"] == "ddownload"
        assert "AF1EE2D4AE" in parser.download_links[1]["link"]

    def test_parses_single_link(self) -> None:
        html = _detail_page_html(
            links=[
                (
                    "https://www.filecrypt.cc/Container/XYZ.html",
                    "Download",
                    "Nitroflare.com",
                )
            ]
        )
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.download_links) == 1
        assert parser.download_links[0]["hoster"] == "nitroflare"

    def test_parses_unknown_hoster(self) -> None:
        html = _detail_page_html(
            links=[
                (
                    "https://www.filecrypt.cc/Container/UNK.html",
                    "Download",
                    "SomeUnknownHoster",
                )
            ]
        )
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.download_links) == 1
        assert parser.download_links[0]["hoster"] == "filecrypt"

    def test_extracts_size_gb(self) -> None:
        html = _detail_page_html(size_text="7,72 GB")
        parser = _DetailPageParser()
        parser.feed(html)

        assert parser.extract_size() == "7,72 GB"

    def test_extracts_size_mb(self) -> None:
        html = _detail_page_html(size_text="4800 MB")
        parser = _DetailPageParser()
        parser.feed(html)

        assert parser.extract_size() == "4800 MB"

    def test_no_download_links(self) -> None:
        html = "<html><body><p>No downloads here</p></body></html>"
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.download_links) == 0
        assert parser.extract_size() == ""

    def test_deduplicates_links(self) -> None:
        html = _detail_page_html(
            links=[
                (
                    "https://www.filecrypt.cc/Container/SAME.html",
                    "Download",
                    "Rapidgator.net",
                ),
                (
                    "https://www.filecrypt.cc/Container/SAME.html",
                    "Mirror #1",
                    "Rapidgator.net",
                ),
            ]
        )
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.download_links) == 1

    def test_ignores_non_filecrypt_links(self) -> None:
        html = (
            "<html><body>"
            '<a href="https://movieblog.to/proload/?n=test">'
            "Usenet</a>"
            '<a href="https://www.filecrypt.cc/Container/OK.html">'
            "Rapidgator.net</a>"
            "</body></html>"
        )
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.download_links) == 1
        assert "OK" in parser.download_links[0]["link"]


# ---------------------------------------------------------------------------
# Plugin integration tests
# ---------------------------------------------------------------------------


class TestMovieblogPlugin:
    """Tests for MovieblogPlugin."""

    def test_plugin_attributes(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "movieblog"
        assert plugin.provides == "download"
        assert "movieblog.to" in plugin._domains

    def test_search_builds_correct_url(self) -> None:
        plugin = _make_plugin()
        search_html = _search_page_html()

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            return httpx.Response(
                200,
                text=search_html,
                request=httpx.Request("GET", url),
            )

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)

        import asyncio

        asyncio.get_event_loop().run_until_complete(
            plugin._search_page("iron man", 1)
        )
        called_url = plugin._safe_fetch.call_args[0][0]
        assert "?s=iron+man" in called_url

    def test_search_page_2_url(self) -> None:
        plugin = _make_plugin()

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            return httpx.Response(
                200,
                text=_search_page_html(),
                request=httpx.Request("GET", url),
            )

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)

        import asyncio

        asyncio.get_event_loop().run_until_complete(
            plugin._search_page("test", 2)
        )
        called_url = plugin._safe_fetch.call_args[0][0]
        assert "/page/2/?s=test" in called_url

    @pytest.mark.asyncio()
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()
        search_html = _search_page_html()
        detail_html = _detail_page_html()

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            if "?s=" in url:
                return httpx.Response(
                    200,
                    text=search_html,
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(
                200,
                text=detail_html,
                request=httpx.Request("GET", url),
            )

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)
        results = await plugin.search("iron man")

        assert len(results) == 1
        assert "Iron.Maze" in results[0].title
        assert results[0].download_link is not None
        assert len(results[0].download_links) == 2

    @pytest.mark.asyncio()
    async def test_search_empty_query(self) -> None:
        plugin = _make_plugin()
        results = await plugin.search("")
        assert results == []

    @pytest.mark.asyncio()
    async def test_search_no_results(self) -> None:
        plugin = _make_plugin()
        empty_html = _search_page_html(results=[])

        plugin._safe_fetch = AsyncMock(
            return_value=httpx.Response(
                200,
                text=empty_html,
                request=httpx.Request(
                    "GET", "https://movieblog.to/?s=xyz"
                ),
            )
        )
        results = await plugin.search("xyz")
        assert results == []

    @pytest.mark.asyncio()
    async def test_search_fetch_failure(self) -> None:
        plugin = _make_plugin()
        plugin._safe_fetch = AsyncMock(return_value=None)
        results = await plugin.search("test")
        assert results == []

    @pytest.mark.asyncio()
    async def test_category_filter_movies(self) -> None:
        plugin = _make_plugin()
        movie_result = _search_result_html(
            title="Movie A",
            url="https://movieblog.to/a/",
            categories=[
                ("https://movieblog.to/category/action/", "Action"),
            ],
        )
        series_result = _search_result_html(
            title="Series B",
            url="https://movieblog.to/b/",
            post_id=2,
            categories=[
                ("https://movieblog.to/category/serie/", "Serie"),
            ],
        )
        search_html = _search_page_html(
            results=[movie_result, series_result]
        )
        detail_html = _detail_page_html()

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            if "?s=" in url:
                return httpx.Response(
                    200,
                    text=search_html,
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(
                200,
                text=detail_html,
                request=httpx.Request("GET", url),
            )

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)
        results = await plugin.search("test", category=2000)

        assert len(results) == 1
        assert results[0].title == "Movie A"

    @pytest.mark.asyncio()
    async def test_category_filter_series(self) -> None:
        plugin = _make_plugin()
        movie_result = _search_result_html(
            title="Movie A",
            url="https://movieblog.to/a/",
            categories=[
                ("https://movieblog.to/category/action/", "Action"),
            ],
        )
        series_result = _search_result_html(
            title="Series B",
            url="https://movieblog.to/b/",
            post_id=2,
            categories=[
                ("https://movieblog.to/category/serie/", "Serie"),
            ],
        )
        search_html = _search_page_html(
            results=[movie_result, series_result]
        )
        detail_html = _detail_page_html()

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            if "?s=" in url:
                return httpx.Response(
                    200,
                    text=search_html,
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(
                200,
                text=detail_html,
                request=httpx.Request("GET", url),
            )

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)
        results = await plugin.search("test", category=5000)

        assert len(results) == 1
        assert results[0].title == "Series B"

    @pytest.mark.asyncio()
    async def test_season_filter_restricts_to_tv(self) -> None:
        plugin = _make_plugin()
        movie_result = _search_result_html(
            title="Movie A",
            url="https://movieblog.to/a/",
            categories=[
                ("https://movieblog.to/category/drama/", "Drama"),
            ],
        )
        series_result = _search_result_html(
            title="Series B",
            url="https://movieblog.to/b/",
            post_id=2,
            categories=[
                ("https://movieblog.to/category/serie/", "Serie"),
            ],
        )
        search_html = _search_page_html(
            results=[movie_result, series_result]
        )
        detail_html = _detail_page_html()

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            if "?s=" in url:
                return httpx.Response(
                    200,
                    text=search_html,
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(
                200,
                text=detail_html,
                request=httpx.Request("GET", url),
            )

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)
        results = await plugin.search("test", season=1)

        assert len(results) == 1
        assert results[0].title == "Series B"

    @pytest.mark.asyncio()
    async def test_detail_page_no_links_skips(self) -> None:
        plugin = _make_plugin()
        search_html = _search_page_html()
        empty_detail = "<html><body><p>No links</p></body></html>"

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            if "?s=" in url:
                return httpx.Response(
                    200,
                    text=search_html,
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(
                200,
                text=empty_detail,
                request=httpx.Request("GET", url),
            )

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)
        results = await plugin.search("test")

        assert results == []

    @pytest.mark.asyncio()
    async def test_detail_page_failure_skips(self) -> None:
        plugin = _make_plugin()
        search_html = _search_page_html()

        call_count = 0

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response | None:
            nonlocal call_count
            call_count += 1
            if "?s=" in url:
                return httpx.Response(
                    200,
                    text=search_html,
                    request=httpx.Request("GET", url),
                )
            return None

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)
        results = await plugin.search("test")

        assert results == []

    @pytest.mark.asyncio()
    async def test_result_has_release_name(self) -> None:
        plugin = _make_plugin()
        search_html = _search_page_html()
        detail_html = _detail_page_html()

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            if "?s=" in url:
                return httpx.Response(
                    200,
                    text=search_html,
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(
                200,
                text=detail_html,
                request=httpx.Request("GET", url),
            )

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)
        results = await plugin.search("test")

        assert results[0].release_name == results[0].title

    @pytest.mark.asyncio()
    async def test_result_has_size(self) -> None:
        plugin = _make_plugin()
        search_html = _search_page_html()
        detail_html = _detail_page_html(size_text="7,72 GB")

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            if "?s=" in url:
                return httpx.Response(
                    200,
                    text=search_html,
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(
                200,
                text=detail_html,
                request=httpx.Request("GET", url),
            )

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)
        results = await plugin.search("test")

        assert results[0].size == "7,72 GB"

    @pytest.mark.asyncio()
    async def test_result_has_source_url(self) -> None:
        plugin = _make_plugin()
        search_html = _search_page_html()
        detail_html = _detail_page_html()

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            if "?s=" in url:
                return httpx.Response(
                    200,
                    text=search_html,
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(
                200,
                text=detail_html,
                request=httpx.Request("GET", url),
            )

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)
        results = await plugin.search("test")

        assert "movieblog.to" in results[0].source_url

    @pytest.mark.asyncio()
    async def test_result_category_is_set(self) -> None:
        plugin = _make_plugin()
        search_html = _search_page_html()
        detail_html = _detail_page_html()

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            if "?s=" in url:
                return httpx.Response(
                    200,
                    text=search_html,
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(
                200,
                text=detail_html,
                request=httpx.Request("GET", url),
            )

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)
        results = await plugin.search("test")

        assert results[0].category == 2000

    @pytest.mark.asyncio()
    async def test_pagination_follows_next(self) -> None:
        """Plugin follows pagination to collect more results."""
        plugin = _make_plugin()
        nav = _navigation_html(
            next_url="https://movieblog.to/page/2/?s=test"
        )
        page1_html = _search_page_html(
            results=[
                _search_result_html(
                    title="Result A",
                    url="https://movieblog.to/a/",
                    post_id=1,
                )
            ],
            nav_html=nav,
        )
        page2_html = _search_page_html(
            results=[
                _search_result_html(
                    title="Result B",
                    url="https://movieblog.to/b/",
                    post_id=2,
                )
            ],
        )
        detail_html = _detail_page_html()

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            if "/page/2/" in url:
                return httpx.Response(
                    200,
                    text=page2_html,
                    request=httpx.Request("GET", url),
                )
            if "?s=" in url:
                return httpx.Response(
                    200,
                    text=page1_html,
                    request=httpx.Request("GET", url),
                )
            return httpx.Response(
                200,
                text=detail_html,
                request=httpx.Request("GET", url),
            )

        plugin._safe_fetch = AsyncMock(side_effect=mock_fetch)
        results = await plugin.search("test")

        assert len(results) == 2
        titles = {r.title for r in results}
        assert "Result A" in titles
        assert "Result B" in titles
