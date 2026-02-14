"""Tests for the jjs.page plugin."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "jjs.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("jjs_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_JjsPlugin = _mod.JjsPlugin
_SearchResultParser = _mod._SearchResultParser
_PaginationParser = _mod._PaginationParser
_DetailPageParser = _mod._DetailPageParser


def _make_plugin() -> object:
    p = _JjsPlugin()
    p._domain_verified = True
    return p


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------


def _search_result_html(
    title: str = "Iron Man 2008 German DTSHD DL 2160p UHD BluRay HDR HEVC REMUX – JJ",
    url: str = "https://jjs.page/iron-man-2008-german-dtshd-dl-2160p-uhd-bluray-hdr-hevc-remux-jj/",
    date: str = "19. Februar 2023 | 20:50",
    category_text: str = "JJ Film Releases",
    category_href: str = "https://jjs.page/jjmovies/",
    subcategory_text: str = "UltraHD",
    subcategory_href: str = "https://jjs.page/jjmovies/uhd-jjmovies/",
) -> str:
    return (
        '<article class="post-type-post type-post status-publish format-standard hentry">'
        f'<h2 class="entry-title"><a href="{url}">{title}</a></h2>'
        f'<p class="post-meta">{date} | '
        f'<a href="{category_href}">{category_text}</a>, '
        f'<a href="{subcategory_href}">{subcategory_text}</a></p>'
        "</article>"
    )


def _search_page_html(
    results: list[str] | None = None,
    pagination: str = "",
) -> str:
    items = results or [_search_result_html()]
    return f"<html><body>{''.join(items)}{pagination}</body></html>"


def _pagination_html(current: int = 1, last: int = 16) -> str:
    parts = ['<div class="wp-pagenavi">']
    for i in range(1, min(6, last + 1)):
        if i == current:
            parts.append(f'<span class="current">{i}</span>')
        else:
            parts.append(f'<a href="/page/{i}/?s=test">{i}</a>')
    if last > 5:
        parts.append('<span class="extend">...</span>')
        parts.append(f'<a href="/page/{last}/?s=test">{last}</a>')
    parts.append(
        f'<a class="last" href="/page/{last}/?s=test">Letzte &raquo;</a>'
    )
    parts.append("</div>")
    return "".join(parts)


def _detail_page_html(
    links: list[tuple[str, str]] | None = None,
    size_text: str = "60.1 GB",
) -> str:
    if links is None:
        links = [
            ("https://filecrypt.cc/Container/ABC123.html", "Ddownload.com"),
            ("https://filecrypt.cc/Container/DEF456.html", "Rapidgator.net"),
        ]
    ddl_slots = []
    slot_ids = ["DDL1st", "DDL2nd", "DDL3rd"]
    for i, (href, text) in enumerate(links):
        slot_id = slot_ids[i] if i < len(slot_ids) else f"DDL{i + 1}"
        ddl_slots.append(
            f'<div id="{slot_id}">'
            f'<a href="{href}">'
            f'<img src="https://filecrypt.cc/Stat/STAT.png">'
            f"{text}</a></div>"
        )
    ddl_html = (
        '<div id="DDL"><h5 id="DDLHeading">Downloads</h5>'
        f'<div id="DDLContent">{"".join(ddl_slots)}</div></div>'
    )
    return (
        "<html><body>"
        "<h1 class='entry-title'>Test Title</h1>"
        f"<p>Total size: {size_text}</p>"
        f"{ddl_html}"
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# SearchResultParser tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    """Tests for _SearchResultParser."""

    def test_parses_single_movie_result(self) -> None:
        html = _search_page_html()
        parser = _SearchResultParser()
        parser.feed(html)

        assert len(parser.results) == 1
        r = parser.results[0]
        assert (
            r["title"]
            == "Iron Man 2008 German DTSHD DL 2160p UHD BluRay HDR HEVC REMUX – JJ"
        )
        assert (
            r["url"]
            == "https://jjs.page/iron-man-2008-german-dtshd-dl-2160p-uhd-bluray-hdr-hevc-remux-jj/"
        )
        assert r["category"] == 2000

    def test_parses_series_result(self) -> None:
        html = _search_page_html(
            results=[
                _search_result_html(
                    title="Criminal Minds S16 COMPLETE German EAC3 DL 1080p WebHD x264 – JJ",
                    url="https://jjs.page/criminal-minds-s16/",
                    category_text="JJ Serien Releases",
                    category_href="https://jjs.page/jjseries/",
                    subcategory_text="HD",
                    subcategory_href="https://jjs.page/jjseries/hd-jjseries/",
                )
            ]
        )
        parser = _SearchResultParser()
        parser.feed(html)

        assert len(parser.results) == 1
        assert parser.results[0]["category"] == 5000

    def test_parses_scene_result(self) -> None:
        html = _search_page_html(
            results=[
                _search_result_html(
                    title="Some Scene Release",
                    url="https://jjs.page/some-scene/",
                    category_text="Scene & andere Releases",
                    category_href="https://jjs.page/other/",
                    subcategory_text="HD",
                    subcategory_href="https://jjs.page/other/hd-other/",
                )
            ]
        )
        parser = _SearchResultParser()
        parser.feed(html)

        assert len(parser.results) == 1
        assert parser.results[0]["category"] == 2000

    def test_parses_multiple_results(self) -> None:
        results = [
            _search_result_html(
                title=f"Movie {i}",
                url=f"https://jjs.page/movie-{i}/",
            )
            for i in range(5)
        ]
        html = _search_page_html(results=results)
        parser = _SearchResultParser()
        parser.feed(html)

        assert len(parser.results) == 5
        for i, r in enumerate(parser.results):
            assert r["title"] == f"Movie {i}"

    def test_skips_article_without_title(self) -> None:
        html = (
            "<html><body>"
            '<article class="hentry">'
            '<h2 class="entry-title"></h2>'
            '<p class="post-meta">Date</p>'
            "</article>"
            "</body></html>"
        )
        parser = _SearchResultParser()
        parser.feed(html)

        assert len(parser.results) == 0

    def test_skips_article_without_link(self) -> None:
        html = (
            "<html><body>"
            '<article class="hentry">'
            '<h2 class="entry-title">Title only</h2>'
            '<p class="post-meta">Date</p>'
            "</article>"
            "</body></html>"
        )
        parser = _SearchResultParser()
        parser.feed(html)

        assert len(parser.results) == 0

    def test_empty_page(self) -> None:
        html = "<html><body><p>No results found</p></body></html>"
        parser = _SearchResultParser()
        parser.feed(html)

        assert len(parser.results) == 0


# ---------------------------------------------------------------------------
# PaginationParser tests
# ---------------------------------------------------------------------------


class TestPaginationParser:
    """Tests for _PaginationParser."""

    def test_parses_last_page_from_numbers(self) -> None:
        html = _pagination_html(current=1, last=16)
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.last_page == 16

    def test_parses_single_page(self) -> None:
        html = "<html><body></body></html>"
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.last_page == 1

    def test_parses_small_pagination(self) -> None:
        html = (
            '<div class="wp-pagenavi">'
            '<span class="current">1</span>'
            '<a href="/page/2/?s=test">2</a>'
            '<a href="/page/3/?s=test">3</a>'
            "</div>"
        )
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.last_page == 3

    def test_extracts_last_page_from_last_link(self) -> None:
        html = (
            '<div class="wp-pagenavi">'
            '<span class="current">1</span>'
            '<a href="/page/2/?s=test">2</a>'
            '<span class="extend">...</span>'
            '<a class="last" href="/page/456/?s=test">Letzte &raquo;</a>'
            "</div>"
        )
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.last_page == 456

    def test_ignores_next_prev_links(self) -> None:
        html = (
            '<div class="wp-pagenavi">'
            '<a class="previouspostslink" href="/page/1/?s=test">&laquo;</a>'
            '<span class="current">2</span>'
            '<a href="/page/3/?s=test">3</a>'
            '<a class="nextpostslink" href="/page/3/?s=test">&raquo;</a>'
            "</div>"
        )
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.last_page == 3


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
        assert (
            parser.download_links[0]["link"]
            == "https://filecrypt.cc/Container/ABC123.html"
        )
        assert parser.download_links[0]["hoster"] == "ddownload"
        assert (
            parser.download_links[1]["link"]
            == "https://filecrypt.cc/Container/DEF456.html"
        )
        assert parser.download_links[1]["hoster"] == "rapidgator"

    def test_parses_single_link(self) -> None:
        html = _detail_page_html(
            links=[
                ("https://filecrypt.cc/Container/XYZ789.html", "Ddownload.com")
            ]
        )
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.download_links) == 1
        assert parser.download_links[0]["hoster"] == "ddownload"

    def test_parses_unknown_hoster(self) -> None:
        html = _detail_page_html(
            links=[
                ("https://filecrypt.cc/Container/UNK001.html", "SomeHoster")
            ]
        )
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.download_links) == 1
        assert parser.download_links[0]["hoster"] == "filecrypt"

    def test_extracts_size_gb(self) -> None:
        html = _detail_page_html(size_text="60.1 GB")
        parser = _DetailPageParser()
        parser.feed(html)

        assert parser.extract_size() == "60.1 GB"

    def test_extracts_size_mb(self) -> None:
        html = _detail_page_html(size_text="1020 MB")
        parser = _DetailPageParser()
        parser.feed(html)

        assert parser.extract_size() == "1020 MB"

    def test_no_download_links(self) -> None:
        html = "<html><body><p>No downloads</p></body></html>"
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.download_links) == 0
        assert parser.extract_size() == ""

    def test_deduplicates_links(self) -> None:
        html = _detail_page_html(
            links=[
                (
                    "https://filecrypt.cc/Container/ABC123.html",
                    "Ddownload.com",
                ),
                (
                    "https://filecrypt.cc/Container/ABC123.html",
                    "Ddownload.com",
                ),
            ]
        )
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.download_links) == 1

    def test_filecrypt_links_outside_ddl_section(self) -> None:
        html = (
            "<html><body>"
            '<a href="https://filecrypt.cc/Container/OUT123.html">'
            "Ddownload.com</a>"
            "</body></html>"
        )
        parser = _DetailPageParser()
        parser.feed(html)

        assert len(parser.download_links) == 1
        assert (
            parser.download_links[0]["link"]
            == "https://filecrypt.cc/Container/OUT123.html"
        )


# ---------------------------------------------------------------------------
# Plugin integration tests
# ---------------------------------------------------------------------------


class TestJjsPlugin:
    """Tests for JjsPlugin."""

    def test_plugin_attributes(self) -> None:
        p = _JjsPlugin()
        assert p.name == "jjs"
        assert p.provides == "download"
        assert p._domains == ["jjs.page"]

    @pytest.mark.asyncio()
    async def test_search_builds_correct_url(self) -> None:
        plugin = _make_plugin()
        mock_response = httpx.Response(
            200,
            text=_search_page_html(),
            request=httpx.Request("GET", "https://jjs.page/?s=iron+man"),
        )
        plugin._safe_fetch = AsyncMock(return_value=mock_response)

        await plugin.search("iron man")

        first_call = plugin._safe_fetch.call_args_list[0]
        assert first_call[0][0] == "https://jjs.page/?s=iron+man"

    @pytest.mark.asyncio()
    async def test_search_page_2_url(self) -> None:
        plugin = _make_plugin()
        page1_html = _search_page_html(pagination=_pagination_html(1, 3))
        page2_html = _search_page_html(
            results=[
                _search_result_html(
                    title="Page 2 Result",
                    url="https://jjs.page/page2-result/",
                )
            ]
        )
        detail_html = _detail_page_html()

        call_count = 0

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if "page/2" in url:
                return httpx.Response(
                    200,
                    text=page2_html,
                    request=httpx.Request("GET", url),
                )
            if url.endswith("/?s=iron+man"):
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
        await plugin.search("iron man")

        assert call_count > 2
        urls = [c[0][0] for c in plugin._safe_fetch.call_args_list]
        assert any("page/2" in u for u in urls)

    @pytest.mark.asyncio()
    async def test_search_returns_results_with_download_links(self) -> None:
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
        assert (
            results[0].title
            == "Iron Man 2008 German DTSHD DL 2160p UHD BluRay HDR HEVC REMUX – JJ"
        )
        assert "filecrypt.cc" in results[0].download_link
        assert results[0].download_links is not None
        assert len(results[0].download_links) == 2

    @pytest.mark.asyncio()
    async def test_search_empty_query(self) -> None:
        plugin = _make_plugin()
        plugin._safe_fetch = AsyncMock()
        results = await plugin.search("")

        assert results == []
        plugin._safe_fetch.assert_not_called()

    @pytest.mark.asyncio()
    async def test_search_no_results(self) -> None:
        plugin = _make_plugin()
        empty_html = "<html><body><p>No results</p></body></html>"
        plugin._safe_fetch = AsyncMock(
            return_value=httpx.Response(
                200,
                text=empty_html,
                request=httpx.Request("GET", "https://jjs.page/?s=xyz"),
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
        movie = _search_result_html(
            title="Movie A",
            url="https://jjs.page/movie-a/",
            category_href="https://jjs.page/jjmovies/",
        )
        series = _search_result_html(
            title="Series B",
            url="https://jjs.page/series-b/",
            category_text="JJ Serien Releases",
            category_href="https://jjs.page/jjseries/",
            subcategory_href="https://jjs.page/jjseries/hd-jjseries/",
        )
        search_html = _search_page_html(results=[movie, series])
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
        movie = _search_result_html(
            title="Movie A",
            url="https://jjs.page/movie-a/",
            category_href="https://jjs.page/jjmovies/",
        )
        series = _search_result_html(
            title="Series B",
            url="https://jjs.page/series-b/",
            category_text="JJ Serien Releases",
            category_href="https://jjs.page/jjseries/",
            subcategory_href="https://jjs.page/jjseries/hd-jjseries/",
        )
        search_html = _search_page_html(results=[movie, series])
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
        movie = _search_result_html(
            title="Movie A",
            url="https://jjs.page/movie-a/",
            category_href="https://jjs.page/jjmovies/",
        )
        series = _search_result_html(
            title="Series B S01",
            url="https://jjs.page/series-b-s01/",
            category_text="JJ Serien Releases",
            category_href="https://jjs.page/jjseries/",
            subcategory_href="https://jjs.page/jjseries/hd-jjseries/",
        )
        search_html = _search_page_html(results=[movie, series])
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
        assert results[0].title == "Series B S01"

    @pytest.mark.asyncio()
    async def test_detail_page_no_links_skips_result(self) -> None:
        plugin = _make_plugin()
        search_html = _search_page_html()
        empty_detail = "<html><body><p>No downloads here</p></body></html>"

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
    async def test_detail_page_failure_skips_result(self) -> None:
        plugin = _make_plugin()
        search_html = _search_page_html()

        async def mock_fetch(
            url: str, **kwargs: object
        ) -> httpx.Response | None:
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
        results = await plugin.search("iron man")

        assert len(results) == 1
        assert results[0].release_name == results[0].title

    @pytest.mark.asyncio()
    async def test_result_has_size(self) -> None:
        plugin = _make_plugin()
        search_html = _search_page_html()
        detail_html = _detail_page_html(size_text="4.2 GB")

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

        assert len(results) == 1
        assert results[0].size == "4.2 GB"

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
        results = await plugin.search("iron man")

        assert len(results) == 1
        assert (
            results[0].source_url
            == "https://jjs.page/iron-man-2008-german-dtshd-dl-2160p-uhd-bluray-hdr-hevc-remux-jj/"
        )

    @pytest.mark.asyncio()
    async def test_result_category_is_set(self) -> None:
        plugin = _make_plugin()
        search_html = _search_page_html(
            results=[
                _search_result_html(
                    title="Series",
                    url="https://jjs.page/series/",
                    category_href="https://jjs.page/jjseries/",
                    subcategory_href="https://jjs.page/jjseries/hd-jjseries/",
                )
            ]
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
        results = await plugin.search("test")

        assert len(results) == 1
        assert results[0].category == 5000
