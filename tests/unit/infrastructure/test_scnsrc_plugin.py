"""Tests for the scnsrc.me (SceneSource) Python plugin."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

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
_CATEGORY_PATH_MAP = _mod._CATEGORY_PATH_MAP
_category_to_torznab = _mod._category_to_torznab
_clean_wayback_url = _mod._clean_wayback_url


def _make_plugin() -> object:
    return _ScnSrcPlugin()


def _make_mock_page(content: str = "<html></html>") -> AsyncMock:
    page = AsyncMock()
    page.goto = AsyncMock()
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
        assert _category_to_torznab("Games") == 4000

    def test_music_maps_correctly(self) -> None:
        assert _category_to_torznab("Music") == 3000

    def test_ebooks_maps_correctly(self) -> None:
        assert _category_to_torznab("ebooks") == 7000

    def test_unknown_defaults_to_movies(self) -> None:
        assert _category_to_torznab("unknown") == 2000

    def test_path_map_categories(self) -> None:
        assert _CATEGORY_PATH_MAP[2000] == "category/films"
        assert _CATEGORY_PATH_MAP[5000] == "category/tv"
        assert _CATEGORY_PATH_MAP[4000] == "category/games"


# ---------------------------------------------------------------------------
# Plugin attributes
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name(self) -> None:
        assert _make_plugin().name == "scnsrc"

    def test_version(self) -> None:
        assert _make_plugin().version == "1.0.0"

    def test_mode(self) -> None:
        assert _make_plugin().mode == "playwright"


# ---------------------------------------------------------------------------
# Plugin search integration (mocked)
# ---------------------------------------------------------------------------


class TestPluginSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()

        search_page = _make_mock_page(_SAMPLE_POST_HTML)
        context = _make_mock_context(pages=[search_page])

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

        page = _make_mock_page("<html><body>No results</body></html>")
        context = _make_mock_context(pages=[page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("nonexistent")
        assert results == []

    async def test_search_with_category(self) -> None:
        plugin = _make_plugin()

        page = _make_mock_page(_MULTI_POST_HTML)
        context = _make_mock_context(pages=[page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("movie", category=2000)

        # Verify URL includes category path
        call_args = page.goto.call_args
        url_called = call_args[0][0]
        assert "category/films" in url_called
        assert "s=movie" in url_called

        # All results should have the requested category
        for r in results:
            assert r.category == 2000

    async def test_search_multiple_results(self) -> None:
        plugin = _make_plugin()

        page = _make_mock_page(_MULTI_POST_HTML)
        context = _make_mock_context(pages=[page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("test")

        assert len(results) == 2
        assert "Movie.2025" in results[0].title
        assert "Game.2025" in results[1].title

    async def test_posts_without_links_skipped(self) -> None:
        plugin = _make_plugin()

        html = """
        <div class="post" id="post-1">
          <h2><a href="/no-dl/">Title</a></h2>
          <div class="storycontent">
            <div class="tvshow_info"><p>No links</p></div>
          </div>
        </div>
        """
        page = _make_mock_page(html)
        context = _make_mock_context(pages=[page])

        plugin._browser = _make_mock_browser(context)
        plugin._context = context

        results = await plugin.search("test")
        assert results == []


class TestCloudflareWait:
    async def test_no_challenge_passes(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page()
        await plugin._wait_for_cloudflare(page)
        page.wait_for_function.assert_awaited_once()

    async def test_timeout_does_not_raise(self) -> None:
        plugin = _make_plugin()
        page = _make_mock_page()
        page.wait_for_function = AsyncMock(side_effect=TimeoutError("timeout"))
        await plugin._wait_for_cloudflare(page)


class TestCleanup:
    async def test_cleanup_closes_resources(self) -> None:
        plugin = _make_plugin()

        context = _make_mock_context()
        browser = _make_mock_browser(context)
        pw = _make_mock_playwright(browser)

        plugin._playwright = pw
        plugin._browser = browser
        plugin._context = context

        await plugin.cleanup()

        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()
        pw.stop.assert_awaited_once()
        assert plugin._context is None
        assert plugin._browser is None
        assert plugin._playwright is None

    async def test_cleanup_when_nothing_to_close(self) -> None:
        plugin = _make_plugin()
        await plugin.cleanup()
