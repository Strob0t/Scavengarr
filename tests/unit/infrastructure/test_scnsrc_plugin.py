"""Tests for the scnsrc.me (SceneSource) Python plugin."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import pytest

from scavengarr.domain.plugins.base import PluginUnreachableError

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "scnsrc.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("scnsrc_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_ScnSrcPlugin = _mod.ScnSrcPlugin
_PostParser = _mod._PostParser
_DOMAINS = _ScnSrcPlugin._domains
_SEARCH_PATHS = _mod._SEARCH_PATHS
_CATEGORY_NAME_MAP = _mod._CATEGORY_NAME_MAP
_category_to_torznab = _mod._category_to_torznab
_clean_wayback_url = _mod._clean_wayback_url


def _make_plugin() -> object:
    return _ScnSrcPlugin()


def _make_mock_page(content: str = "<html></html>") -> AsyncMock:
    page = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status = 200
    page.goto = AsyncMock(return_value=mock_response)
    page.wait_for_function = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.content = AsyncMock(return_value=content)
    page.close = AsyncMock()
    page.is_closed = MagicMock(return_value=False)
    return page


def _make_mock_context(
    pages: list[AsyncMock] | None = None,
) -> AsyncMock:
    context = AsyncMock()
    if pages:
        context.new_page = AsyncMock(side_effect=pages)
    else:
        context.new_page = AsyncMock(return_value=_make_mock_page())
    context.close = AsyncMock()
    return context


def _make_mock_browser(
    context: AsyncMock | None = None,
) -> AsyncMock:
    browser = AsyncMock()
    browser.is_connected = MagicMock(return_value=True)
    browser.new_context = AsyncMock(return_value=context or _make_mock_context())
    browser.close = AsyncMock()
    return browser


def _make_mock_playwright(
    browser: AsyncMock | None = None,
) -> AsyncMock:
    pw = AsyncMock()
    pw.chromium = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser or _make_mock_browser())
    pw.stop = AsyncMock()
    return pw


_SAMPLE_POST_HTML = """
<div class="content">
<div class="post" id="post-12345">
  <h2>
    <a href="/some-release-1080p-web/"
       rel="bookmark"
       title="Goto Some.Release.1080p">
      Some.Release.1080p.WEB-DL.H264-GROUP
    </a>
  </h2>
  <div class="cat meta">
    <span class="left">
      Posted by <span class="author">Author</span>
      on <span class="date">Jan 1st, 2025</span>
      in <a href="/category/tv/" rel="category tag">TV</a>.
    </span>
  </div>
  <div class="storycontent">
    <div class="tvshow_info">
      <p>
        <strong>Some.Release.S01E01.1080p.WEB-DL.H264-GROUP</strong>
        <br>
        <strong>Download:</strong>
        <a href="https://www.limetorrents.cc/search/all/Some.Release">
          Torrent
        </a>,
        <a href="https://nzbindex.nl/search?q=Some.Release">
          Usenet
        </a>
      </p>
      <p>
        <strong>Info:</strong>
        <a class="info_link" href="https://example.com">Homepage</a>
      </p>
    </div>
  </div>
</div>
</div>
"""

_MULTI_POST_HTML = """
<div class="content">
<div class="post" id="post-100">
  <h2><a href="/movie-2025/" rel="bookmark">Movie 2025 Title</a></h2>
  <div class="cat meta">
    <span class="left">in
      <a href="/category/films/" rel="category tag">Movies</a>.
    </span>
  </div>
  <div class="storycontent">
    <div class="tvshow_info">
      <p>
        <strong>Movie.2025.1080p.BluRay.x264-GRP</strong><br>
        <strong>Download:</strong>
        <a href="https://limetorrents.cc/search/all/Movie.2025">
          Torrent
        </a>
      </p>
    </div>
  </div>
</div>
<div class="post" id="post-101">
  <h2><a href="/game-2025/" rel="bookmark">Game.2025-CODEX</a></h2>
  <div class="cat meta">
    <span class="left">in
      <a href="/category/games/" rel="category tag">Games</a>.
    </span>
  </div>
  <div class="storycontent">
    <div class="tvshow_info">
      <p>
        <strong>Game.2025.ISO-CODEX</strong><br>
        <strong>Download:</strong>
        <a href="https://limetorrents.cc/search/all/Game.2025">
          Torrent
        </a>,
        <a href="https://nzbindex.nl/search?q=Game.2025">Usenet</a>
      </p>
    </div>
  </div>
</div>
</div>
"""


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestPostParser:
    def test_single_post_extracted(self) -> None:
        parser = _PostParser("https://www.scnsrc.me")
        parser.feed(_SAMPLE_POST_HTML)

        assert len(parser.results) == 1
        post = parser.results[0]
        assert post["title"] == ("Some.Release.S01E01.1080p.WEB-DL.H264-GROUP")
        assert post["category"] == "TV"
        assert post["url"] == ("https://www.scnsrc.me/some-release-1080p-web/")

    def test_download_links_extracted(self) -> None:
        parser = _PostParser("https://www.scnsrc.me")
        parser.feed(_SAMPLE_POST_HTML)

        links = parser.results[0]["links"]
        assert len(links) == 2
        assert "limetorrents" in links[0]["link"]
        assert links[0]["hoster"] == "torrent"
        assert "nzbindex" in links[1]["link"]
        assert links[1]["hoster"] == "usenet"

    def test_multiple_posts(self) -> None:
        parser = _PostParser("https://www.scnsrc.me")
        parser.feed(_MULTI_POST_HTML)

        assert len(parser.results) == 2
        assert parser.results[0]["title"] == ("Movie.2025.1080p.BluRay.x264-GRP")
        assert parser.results[0]["category"] == "Movies"
        assert parser.results[1]["title"] == "Game.2025.ISO-CODEX"
        assert parser.results[1]["category"] == "Games"

    def test_release_name_preferred_over_h2(self) -> None:
        """Release name from <strong> is used over h2 title."""
        html = """
        <div class="post" id="post-50">
          <h2><a href="/short/">Friendly Name</a></h2>
          <div class="storycontent">
            <div class="tvshow_info">
              <p>
                <strong>Full.Release.Name.1080p.WEB-H264</strong>
                <br>
                <strong>Download:</strong>
                <a href="https://limetorrents.cc/x">Torrent</a>
              </p>
            </div>
          </div>
        </div>
        """
        parser = _PostParser("https://www.scnsrc.me")
        parser.feed(html)

        assert len(parser.results) == 1
        assert parser.results[0]["title"] == ("Full.Release.Name.1080p.WEB-H264")

    def test_post_without_download_links_still_emitted(self) -> None:
        html = """
        <div class="post" id="post-99">
          <h2><a href="/no-links/">No Links Post</a></h2>
          <div class="storycontent">
            <div class="tvshow_info">
              <p>Just text, no links.</p>
            </div>
          </div>
        </div>
        """
        parser = _PostParser("https://www.scnsrc.me")
        parser.feed(html)

        # Should still emit because it has a URL
        assert len(parser.results) == 1
        assert parser.results[0]["links"] == []

    def test_nested_divs_handled(self) -> None:
        html = """
        <div class="post" id="post-77">
          <h2><a href="/nested/">Nested Test</a></h2>
          <div class="storycontent">
            <div>
              <div class="tvshow_info">
                <p>
                  <strong>Nested.Release.2025.WEB-DL</strong><br>
                  <strong>Download:</strong>
                  <a href="https://limetorrents.cc/x">Torrent</a>
                </p>
              </div>
            </div>
          </div>
        </div>
        """
        parser = _PostParser("https://www.scnsrc.me")
        parser.feed(html)

        assert len(parser.results) == 1
        assert "Nested.Release" in str(parser.results[0]["title"])

    def test_wayback_urls_cleaned(self) -> None:
        html = """
        <div class="post" id="post-10">
          <h2>
            <a href="https://web.archive.org/web/20250308/https://www.scnsrc.me/test/">
              Test
            </a>
          </h2>
          <div class="storycontent">
            <div class="tvshow_info">
              <p>
                <strong>Test.Release.2025.720p</strong><br>
                <strong>Download:</strong>
                <a href="https://web.archive.org/web/20250308/https://limetorrents.cc/x">
                  Torrent
                </a>
              </p>
            </div>
          </div>
        </div>
        """
        parser = _PostParser("https://www.scnsrc.me")
        parser.feed(html)

        assert len(parser.results) == 1
        post = parser.results[0]
        assert post["url"] == "https://www.scnsrc.me/test/"
        assert post["links"][0]["link"] == "https://limetorrents.cc/x"


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestCleanWaybackUrl:
    def test_strips_wayback_prefix(self) -> None:
        url = "https://web.archive.org/web/20250308153230/https://www.scnsrc.me/test/"
        assert _clean_wayback_url(url) == ("https://www.scnsrc.me/test/")

    def test_leaves_normal_url_untouched(self) -> None:
        url = "https://www.scnsrc.me/test/"
        assert _clean_wayback_url(url) == url


class TestCategoryMapping:
    def test_tv_maps_correctly(self) -> None:
        assert _category_to_torznab("TV") == 5000

    def test_movies_maps_correctly(self) -> None:
        assert _category_to_torznab("Movies") == 2000
        assert _category_to_torznab("Films") == 2000

    def test_games_maps_correctly(self) -> None:
        assert _category_to_torznab("Games") == 4050  # PC/Games

    def test_music_maps_correctly(self) -> None:
        assert _category_to_torznab("Music") == 3000

    def test_ebooks_maps_correctly(self) -> None:
        assert _category_to_torznab("ebooks") == 7000

    def test_unknown_is_other(self) -> None:
        # Used to be labelled a film
        assert _category_to_torznab("unknown") == 8000

    def test_search_paths(self) -> None:
        assert _SEARCH_PATHS[2000] == ("category/films",)
        assert _SEARCH_PATHS[5000] == ("category/tv",)
        assert _SEARCH_PATHS[1000] == ("category/games",)
        # PC covers games and applications
        assert _SEARCH_PATHS[4000] == ("category/applications", "category/games")

    def test_film_subcategories(self) -> None:
        for sub in (
            "hd",
            "bluray",
            "bdrip",
            "bdscr",
            "uhd",
            "dvdrip",
            "dvdscr",
            "cam",
            "r5",
            "scr",
            "telecine",
            "telesync",
            "workprint",
            "3d",
        ):
            assert _CATEGORY_NAME_MAP[sub] == 2000, f"{sub} should map to 2000"

    def test_tv_subcategories(self) -> None:
        for sub in ("miniseries", "ppv", "preair", "uhd-tv", "dvd"):
            assert _CATEGORY_NAME_MAP[sub] == 5000, f"{sub} should map to 5000"
        assert _CATEGORY_NAME_MAP["sports-tv"] == 5060

    def test_pc_game_subcategories(self) -> None:
        for sub in ("iso", "rip", "clone", "dox"):
            assert _CATEGORY_NAME_MAP[sub] == 4050, f"{sub} should map to 4050"

    def test_console_subcategories(self) -> None:
        # Consoles used to be labelled PC games (4000)
        assert {
            sub: _CATEGORY_NAME_MAP[sub]
            for sub in ("nds", "psp", "wii", "xbox360", "ps3", "wiiu", "ps4")
        } == {
            "nds": 1010,
            "psp": 1020,
            "wii": 1030,
            "xbox360": 1050,
            "ps3": 1080,
            "wiiu": 1130,
            "ps4": 1180,
        }

    def test_app_subcategories(self) -> None:
        # Applications used to be 5020 (TV/Foreign)
        for sub in ("applications", "windows-applications", "linux"):
            assert _CATEGORY_NAME_MAP[sub] == 4000, f"{sub} should map to 4000"
        assert _CATEGORY_NAME_MAP["macosx"] == 4030
        assert _CATEGORY_NAME_MAP["iphone"] == 4060

    def test_music_subcategories(self) -> None:
        assert _CATEGORY_NAME_MAP["flac"] == 3040
        assert _CATEGORY_NAME_MAP["new-music"] == 3000
        for sub in ("concert", "music-videos"):
            assert _CATEGORY_NAME_MAP[sub] == 3020, f"{sub} should map to 3020"


# ---------------------------------------------------------------------------
# Plugin attributes
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name(self) -> None:
        assert _make_plugin().name == "scnsrc"

    def test_version(self) -> None:
        assert _make_plugin().version == "1.1.0"

    def test_mode(self) -> None:
        assert _make_plugin().mode == "playwright"

    def test_language_is_english(self) -> None:
        # The registry reads ``languages``; a shadowing
        # ``default_language = "en"`` left the site registered as German
        assert _make_plugin().languages == ["en"]

    def test_domain_not_verified_initially(self) -> None:
        assert _make_plugin()._domain_verified is False


class TestDomains:
    def test_domains_list_not_empty(self) -> None:
        assert len(_DOMAINS) >= 3

    def test_primary_domain_first(self) -> None:
        assert _DOMAINS[0] == "www.scnsrc.me"

    def test_all_known_domains_present(self) -> None:
        domains_set = set(_DOMAINS)
        assert "www.scnsrc.me" in domains_set
        assert "scenesource.me" in domains_set
        assert "scnsrc.net" in domains_set


# ---------------------------------------------------------------------------
# Plugin search integration (mocked)
# ---------------------------------------------------------------------------


_FILM_POST_HTML = """
<div class="post" id="post-9"><h2><a href="/iron-cross/">Iron Cross</a></h2>
<div class="storycontent">
  <p>Group <strong>CMRG </strong>has released 1080p WEB-DL.</p>
  <table><tr><td><p>
    <strong>Genre:</strong> War <a href="https://www.imdb.com/title/tt1/">IMDB</a>
  </p><p>
    <strong>Iron.Cross.The.Road.to.Normandy.2022.1080p.WEB-DL.DD5.1.H.264-CMRG</strong>
    <a href="https://nfomation.net/info/1.nfo">NFO </a>|
    <a href="https://www.limetorrents.cc/search/all/Iron.Cross.2022">Torrent</a> |
    <a href="https://nzbindex.nl/search?q=Iron.Cross.2022">Usenet</a>
  </p></td></tr></table>
</div></div>
<div class="sidebar"><a href="https://rapidgator.net/ad">Download</a></div>
"""


class TestPostPageParser:
    """Post pages: release name + Torrent/Usenet links from storycontent."""

    def test_film_layout(self) -> None:
        parser = _mod._PostPageParser()
        parser.feed(_FILM_POST_HTML)

        assert parser.release_name == (
            "Iron.Cross.The.Road.to.Normandy.2022.1080p.WEB-DL.DD5.1.H.264-CMRG"
        )
        assert parser.links == [
            {
                "hoster": "torrent",
                "link": "https://www.limetorrents.cc/search/all/Iron.Cross.2022",
            },
            {
                "hoster": "usenet",
                "link": "https://nzbindex.nl/search?q=Iron.Cross.2022",
            },
        ]

    def test_tv_layout(self) -> None:
        html = """
        <div class="storycontent"><div class="tvshow_info">
          <p><strong>Iron.Man.2011.S01E12.720p.HDTV.x264-MOMENTUM</strong></p>
          <p><strong>Download:</strong>
            <a href="https://www.limetorrents.cc/search/all/Iron.Man">Torrent</a>
            <a href="https://nzbindex.nl/search?q=Iron.Man">Usenet</a></p>
          <p><strong>Info:</strong> <a href="https://www.imdb.com/x">IMDB</a></p>
        </div></div>
        """
        parser = _mod._PostPageParser()
        parser.feed(html)

        assert parser.release_name == "Iron.Man.2011.S01E12.720p.HDTV.x264-MOMENTUM"
        assert [link["hoster"] for link in parser.links] == ["torrent", "usenet"]

    def test_release_name_is_no_awards_line(self) -> None:
        """Film posts (9 of 11 live Oppenheimer posts) bold an awards line with a
        period before the release name; a scene name has no spaces."""
        html = """
        <div class="storycontent">
          <p>The group <strong>GAZPROM</strong> released 2160p UHD BluRay REMUX of
            2023's movie <strong>Oppenheimer</strong>.</p>
          <blockquote><p><strong>Top rated movie #47; Awards: 2 wins &amp; 2
            nominations.</strong></p></blockquote>
          <p><strong>Genre:</strong> Biography, Drama<br>
             <strong>Ratings:</strong> <a href="https://www.imdb.com/x">IMDB</a>:
             <strong>8.5</strong></p>
          <p><strong>Oppenheimer.2023.2160p.UHD.BluRay.H265-GAZPROM</strong><br>
            1 Disc | 82 GiB</p>
        </div>
        """
        parser = _mod._PostPageParser()
        parser.feed(html)

        assert parser.release_name == "Oppenheimer.2023.2160p.UHD.BluRay.H265-GAZPROM"

    def test_ignores_links_outside_storycontent(self) -> None:
        parser = _mod._PostPageParser()
        parser.feed('<div class="sidebar"><a href="https://x/t">Torrent</a></div>')
        assert parser.links == []
        assert parser.release_name == ""


class TestPluginSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_page = _make_mock_page(_SAMPLE_POST_HTML)
        empty_page = _make_mock_page("<html></html>")
        context = _make_mock_context(pages=[search_page, empty_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("Some Release")

        assert len(results) == 1
        assert "Some.Release" in results[0].title
        assert "limetorrents" in results[0].download_link
        assert len(results[0].download_links) == 2
        assert results[0].category == 5000  # TV

    async def test_search_no_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        page = _make_mock_page("<html><body>No results</body></html>")
        context = _make_mock_context(pages=[page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("nonexistent")
        assert results == []

    async def test_search_with_category(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_page = _make_mock_page(_MULTI_POST_HTML)
        empty_page = _make_mock_page("<html></html>")
        context = _make_mock_context(pages=[search_page, empty_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("movie", category=2000)

        # Verify URL includes category path
        call_args = search_page.goto.call_args
        url_called = call_args[0][0]
        assert "category/films" in url_called
        assert "s=movie" in url_called

        # The game in the listing used to come back labelled 2000
        assert [r.category for r in results] == [2000]

    async def test_pc_request_searches_applications_and_games(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        pages = [_make_mock_page("<html></html>") for _ in range(2)]
        context = _make_mock_context(pages=pages)
        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        await plugin.search("tool", category=4000)

        urls = [page.goto.call_args[0][0] for page in pages]
        assert ["category/applications" in u for u in urls] == [True, False]
        assert ["category/games" in u for u in urls] == [False, True]

    async def test_category_the_site_does_not_serve(self) -> None:
        plugin = _make_plugin()
        plugin._ensure_browser = AsyncMock()

        assert await plugin.search("movie", category=6000) == []
        plugin._ensure_browser.assert_not_awaited()

    async def test_search_query_is_url_encoded(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_page = _make_mock_page("<html></html>")
        empty_page = _make_mock_page("<html></html>")
        context = _make_mock_context(pages=[search_page, empty_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        await plugin.search("Fast & Furious")

        url_called = search_page.goto.call_args[0][0]
        assert url_called.endswith("/?s=Fast+%26+Furious")

    async def test_search_multiple_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_page = _make_mock_page(_MULTI_POST_HTML)
        empty_page = _make_mock_page("<html></html>")
        context = _make_mock_context(pages=[search_page, empty_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("test")

        assert len(results) == 2
        assert "Movie.2025" in results[0].title
        assert "Game.2025" in results[1].title

    async def test_posts_without_links_skipped(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        html = """
        <div class="post" id="post-1">
          <h2><a href="/no-dl/">Title</a></h2>
          <div class="storycontent">
            <div class="tvshow_info"><p>No links</p></div>
          </div>
        </div>
        """
        search_page = _make_mock_page(html)
        empty_page = _make_mock_page("<html></html>")
        post_page = _make_mock_page(html)  # post page has no links either
        context = _make_mock_context(pages=[search_page, empty_page, post_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("test")
        assert results == []

    async def test_links_loaded_from_post_page(self) -> None:
        """Search pages list only title/category; links come from the post."""
        plugin = _make_plugin()
        plugin._domain_verified = True

        listing = """
        <div class="post" id="post-7">
          <h2><a href="https://www.scnsrc.me/iron-man-s01e12/">Iron Man S01E12</a></h2>
          <div class="cat meta"><a href="/category/tv/" rel="category tag">TV</a></div>
        </div>
        """
        post = """
        <div class="post" id="post-7">
          <h2><a href="https://www.scnsrc.me/iron-man-s01e12/">Iron Man S01E12</a></h2>
          <div class="storycontent"><div class="tvshow_info">
            <p><strong>Iron.Man.2011.S01E12.720p.HDTV.x264-MOMENTUM</strong></p>
            <p><strong>Download:</strong>
              <a href="https://www.limetorrents.cc/search/all/Iron.Man">Torrent</a>
              <a href="https://nzbindex.nl/search?q=Iron.Man">Usenet</a></p>
          </div></div>
        </div>
        """
        context = _make_mock_context(
            pages=[
                _make_mock_page(listing),
                _make_mock_page("<html></html>"),
                _make_mock_page(post),
            ]
        )
        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("iron man")

        assert len(results) == 1
        assert results[0].title == "Iron.Man.2011.S01E12.720p.HDTV.x264-MOMENTUM"
        assert results[0].download_link.startswith("https://www.limetorrents.cc/")
        assert len(results[0].download_links) == 2
        assert results[0].category == 5000

    async def test_search_paginates(self) -> None:
        """Verify pagination fetches multiple pages."""
        plugin = _make_plugin()
        plugin._domain_verified = True

        page1 = _make_mock_page(_MULTI_POST_HTML)
        page2 = _make_mock_page(_SAMPLE_POST_HTML)
        empty_page = _make_mock_page("<html></html>")
        context = _make_mock_context(pages=[page1, page2, empty_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("test")

        # 2 from page1 + 1 from page2
        assert len(results) == 3

        # Check page 2 URL has /page/2/
        page2_url = page2.goto.call_args[0][0]
        assert "/page/2/" in page2_url


class TestCloudflareWait:
    async def test_no_challenge_passes(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page()
        assert await plugin._wait_for_cloudflare(page) is True

    async def test_timeout_does_not_raise(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page()
        page.wait_for_function = AsyncMock(side_effect=TimeoutError("timeout"))
        await plugin._wait_for_cloudflare(page)


class TestVerifyDomain:
    async def test_first_domain_succeeds(self) -> None:
        plugin = _make_plugin()

        ok_page = _make_mock_page()
        ok_page.title = AsyncMock(return_value="SceneSource - Your source for Games")
        context = _make_mock_context(pages=[ok_page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert plugin.base_url == "https://www.scnsrc.me"

    async def test_fallback_to_second_domain(self) -> None:
        plugin = _make_plugin()

        # Base class _verify_domain uses a single persistent page.
        # First domain: goto succeeds but the CF challenge is not solved.
        # Second domain: goto succeeds and the CF check passes.
        page = _make_mock_page()
        plugin._wait_for_cloudflare = AsyncMock(side_effect=[False, True])
        context = _make_mock_context(pages=[page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert plugin.base_url == f"https://{_DOMAINS[1]}"

    async def test_all_domains_fail_uses_primary(self) -> None:
        plugin = _make_plugin()

        # Single page, the CF challenge is never solved on any domain.
        page = _make_mock_page()
        plugin._wait_for_cloudflare = AsyncMock(return_value=False)
        context = _make_mock_context(pages=[page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        with pytest.raises(PluginUnreachableError):
            await plugin._verify_domain()

        assert plugin._domain_verified is False

    async def test_domain_error_skips_to_next(self) -> None:
        plugin = _make_plugin()

        # Single page: first goto raises, second goto succeeds.
        page = _make_mock_page()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        page.goto = AsyncMock(
            side_effect=[Exception("net::ERR_NAME_NOT_RESOLVED"), mock_resp],
        )
        context = _make_mock_context(pages=[page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert plugin.base_url == f"https://{_DOMAINS[1]}"

    async def test_verify_domain_cached(self) -> None:
        """Once verified, subsequent calls are no-ops."""
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://scenesource.me"

        # Should not attempt any navigation
        await plugin._verify_domain()

        assert plugin.base_url == "https://scenesource.me"


class TestCleanup:
    async def test_cleanup_closes_resources(self) -> None:
        plugin = _make_plugin()

        context = _make_mock_context()
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        plugin._pw = pw
        plugin._browser = browser
        plugin._context = context

        await plugin.cleanup()

        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        pw.stop.assert_awaited_once()
        assert plugin._context is None
        assert plugin._browser is None
        assert plugin._pw is None

    async def test_cleanup_when_nothing_to_close(self) -> None:
        plugin = _make_plugin()
        await plugin.cleanup()
