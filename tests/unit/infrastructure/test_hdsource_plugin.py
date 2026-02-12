"""Tests for the hd-source.to Python plugin."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import httpx

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "hdsource.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hdsource_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_HdSourcePlugin = _mod.HdSourcePlugin
_SearchPageParser = _mod._SearchPageParser
_PaginationParser = _mod._PaginationParser
_detect_category = _mod._detect_category
_parse_date = _mod._parse_date


def _make_plugin() -> object:
    return _HdSourcePlugin()


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

_SINGLE_ARTICLE_HTML = """
<article id="post-175188" class="post type-post status-publish hentry
 category-filme category-scene formate-1080p genres-action
 genres-animation">
  <header class="search-header">
    <h2 class="entry-title">
      <span class="blog-post-meta"> 15.01.26, 13:41 <span class="sep"> · </span> </span>
      <a href="https://hd-source.to/filme/lego-batman-2017/">
        The.LEGO.Batman.Movie.2017.German.DL.1080p.BluRay.x264-ENCOUNTERS
      </a>
    </h2>
  </header>
  <div class="wrap-collapsible">
    <input id="collapsible-175188" class="toggle" type="checkbox">
    <label for="collapsible-175188" class="lbl-toggle"></label>
    <div class="collapsible-content">
      <div class="search-content">
        <p>Description of the movie.</p>
        <p>
          <strong>Dauer:</strong> 104 Min. |
          <strong>Format:</strong> MKV |
          <strong>Größe:</strong> 6072 MB |
          <a href="https://www.imdb.com/title/tt4116284/">IMDb: 7.3</a> |
          <a href="https://www.xrel.to/search.html?q=test">xREL</a>
          <a href="https://hd-source.to/out/af.php?v=rapidgator"><img src="rg.png"/></a>
          <a class="hosterlnk" href="https://filecrypt.cc/Container/ABC123.html"><span></span></a>
          <a href="https://hd-source.to/out/af.php?v=ddlto"><img src="ddl.png"/></a>
          <a class="hosterlnk" href="https://filecrypt.cc/Container/DEF456.html"><span></span></a>
          <strong>Passwort:</strong> hd-source.to
        </p>
      </div>
    </div>
  </div>
</article>
"""

_SERIES_ARTICLE_HTML = """
<article id="post-100" class="post type-post hentry
 category-serien category-complete formate-720p">
  <header class="search-header">
    <h2 class="entry-title">
      <span class="blog-post-meta"> 03.08.24, 22:35 <span class="sep"> · </span> </span>
      <a href="https://hd-source.to/serien/batman-s01/">
        Batman.Caped.Crusader.S01.Complete.German.DL.720p.WEB.H264-GROUP
      </a>
    </h2>
  </header>
  <div class="wrap-collapsible">
    <input id="collapsible-100" class="toggle" type="checkbox">
    <label for="collapsible-100" class="lbl-toggle"></label>
    <div class="collapsible-content">
      <div class="search-content">
        <p>
          <strong>Größe:</strong> 3200 MB |
          <a href="https://hd-source.to/out/af.php?v=katfile"><img src="kf.png"/></a>
          <a class="hosterlnk" href="https://filecrypt.cc/Container/GHI789.html"><span></span></a>
        </p>
      </div>
    </div>
  </div>
</article>
"""

_MULTI_ARTICLE_HTML = f"""
<html><body>
{_SINGLE_ARTICLE_HTML}
{_SERIES_ARTICLE_HTML}
</body></html>
"""

_NO_LINKS_ARTICLE_HTML = """
<article id="post-999" class="post type-post hentry category-filme">
  <header class="search-header">
    <h2 class="entry-title">
      <span class="blog-post-meta"> 01.01.25, 10:00 </span>
      <a href="https://hd-source.to/filme/no-links/">
        Some.Movie.Without.Links
      </a>
    </h2>
  </header>
  <div class="wrap-collapsible">
    <div class="collapsible-content">
      <div class="search-content">
        <p>No download links here.</p>
      </div>
    </div>
  </div>
</article>
"""

_PAGINATION_HTML = """
<div class="nav-links">
  <span aria-current="page" class="page-numbers current">1</span>
  <a class="page-numbers" href="/page/2/?s=batman">2</a>
  <a class="page-numbers" href="/page/3/?s=batman">3</a>
  <span class="page-numbers dots">…</span>
  <a class="page-numbers" href="/page/7/?s=batman">7</a>
  <a class="next page-numbers" href="/page/2/?s=batman">
    <i class="fa fa-angle-double-right"></i></a>
</div>
"""

_SPIELE_ARTICLE_HTML = """
<article id="post-500" class="post type-post hentry category-spiele">
  <header class="search-header">
    <h2 class="entry-title">
      <span class="blog-post-meta"> 20.05.24, 15:30 </span>
      <a href="https://hd-source.to/spiele/some-game/">
        Some.Game.2024-CODEX
      </a>
    </h2>
  </header>
  <div class="wrap-collapsible">
    <div class="collapsible-content">
      <div class="search-content">
        <p>
          <strong>Größe:</strong> 15000 MB |
          <a href="https://hd-source.to/out/af.php?v=rapidgator"><img/></a>
          <a class="hosterlnk" href="https://filecrypt.cc/Container/GAME01.html"><span></span></a>
        </p>
      </div>
    </div>
  </div>
</article>
"""


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestSearchPageParser:
    def test_single_article_parsed(self) -> None:
        parser = _SearchPageParser()
        parser.feed(_SINGLE_ARTICLE_HTML)

        assert len(parser.results) == 1
        r = parser.results[0]
        assert "LEGO.Batman" in r["title"]
        assert r["url"] == "https://hd-source.to/filme/lego-batman-2017/"
        assert r["category"] == 2000

    def test_download_links_extracted(self) -> None:
        parser = _SearchPageParser()
        parser.feed(_SINGLE_ARTICLE_HTML)

        links = parser.results[0]["download_links"]
        assert len(links) == 2
        assert links[0]["link"] == "https://filecrypt.cc/Container/ABC123.html"
        assert links[0]["hoster"] == "rapidgator"
        assert links[1]["link"] == "https://filecrypt.cc/Container/DEF456.html"
        assert links[1]["hoster"] == "ddl.to"

    def test_size_extracted(self) -> None:
        parser = _SearchPageParser()
        parser.feed(_SINGLE_ARTICLE_HTML)

        assert parser.results[0]["size"] == "6072 MB"

    def test_imdb_rating_extracted(self) -> None:
        parser = _SearchPageParser()
        parser.feed(_SINGLE_ARTICLE_HTML)

        assert parser.results[0]["imdb_rating"] == "7.3"

    def test_imdb_id_extracted(self) -> None:
        parser = _SearchPageParser()
        parser.feed(_SINGLE_ARTICLE_HTML)

        assert parser.results[0]["imdb_id"] == "tt4116284"

    def test_date_extracted(self) -> None:
        parser = _SearchPageParser()
        parser.feed(_SINGLE_ARTICLE_HTML)

        assert parser.results[0]["published_date"] == "2026-01-15 13:41"

    def test_series_category_detected(self) -> None:
        parser = _SearchPageParser()
        parser.feed(_SERIES_ARTICLE_HTML)

        assert len(parser.results) == 1
        assert parser.results[0]["category"] == 5000

    def test_multiple_articles(self) -> None:
        parser = _SearchPageParser()
        parser.feed(_MULTI_ARTICLE_HTML)

        assert len(parser.results) == 2
        assert parser.results[0]["category"] == 2000
        assert parser.results[1]["category"] == 5000

    def test_article_without_links_not_emitted(self) -> None:
        parser = _SearchPageParser()
        parser.feed(_NO_LINKS_ARTICLE_HTML)

        assert len(parser.results) == 0

    def test_spiele_category(self) -> None:
        parser = _SearchPageParser()
        parser.feed(_SPIELE_ARTICLE_HTML)

        assert len(parser.results) == 1
        assert parser.results[0]["category"] == 4000

    def test_series_article_hoster(self) -> None:
        parser = _SearchPageParser()
        parser.feed(_SERIES_ARTICLE_HTML)

        links = parser.results[0]["download_links"]
        assert len(links) == 1
        assert links[0]["hoster"] == "katfile"


class TestPaginationParser:
    def test_last_page_extracted(self) -> None:
        parser = _PaginationParser()
        parser.feed(_PAGINATION_HTML)

        assert parser.last_page == 7

    def test_no_pagination(self) -> None:
        parser = _PaginationParser()
        parser.feed("<html><body>No pagination</body></html>")

        assert parser.last_page == 1

    def test_single_page(self) -> None:
        html = """
        <div class="nav-links">
            <span class="page-numbers current">1</span>
        </div>
        """
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.last_page == 1

    def test_dots_ignored(self) -> None:
        html = """
        <div class="nav-links">
            <span class="page-numbers current">1</span>
            <span class="page-numbers dots">…</span>
            <a class="page-numbers" href="/page/10/">10</a>
        </div>
        """
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.last_page == 10

    def test_next_link_ignored(self) -> None:
        html = """
        <div class="nav-links">
            <span class="page-numbers current">1</span>
            <a class="page-numbers" href="/page/2/">2</a>
            <a class="next page-numbers" href="/page/2/"><i></i></a>
        </div>
        """
        parser = _PaginationParser()
        parser.feed(html)

        assert parser.last_page == 2


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestDetectCategory:
    def test_filme(self) -> None:
        assert _detect_category("category-filme category-scene formate-1080p") == 2000

    def test_serien(self) -> None:
        assert _detect_category("category-serien category-complete") == 5000

    def test_spiele(self) -> None:
        assert _detect_category("category-spiele") == 4000

    def test_serien_priority_over_filme(self) -> None:
        assert _detect_category("category-filme category-serien") == 5000

    def test_laufend(self) -> None:
        assert _detect_category("category-serien category-laufend") == 5000

    def test_default_is_movie(self) -> None:
        assert _detect_category("category-unknown") == 2000


class TestParseDate:
    def test_standard_format(self) -> None:
        assert _parse_date("15.01.26, 13:41") == "2026-01-15 13:41"

    def test_with_surrounding_text(self) -> None:
        assert _parse_date(" 03.08.24, 22:35 · ") == "2024-08-03 22:35"

    def test_no_match(self) -> None:
        assert _parse_date("no date here") == ""

    def test_old_year(self) -> None:
        assert _parse_date("01.01.99, 00:00") == "1999-01-01 00:00"


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name(self) -> None:
        assert _make_plugin().name == "hdsource"

    def test_provides(self) -> None:
        assert _make_plugin().provides == "download"

    def test_mode(self) -> None:
        assert _make_plugin().mode == "httpx"

    def test_base_url(self) -> None:
        assert _make_plugin().base_url == "https://hd-source.to"

    def test_domain_not_verified(self) -> None:
        assert _make_plugin()._domain_verified is False


# ---------------------------------------------------------------------------
# Plugin search tests (mocked httpx)
# ---------------------------------------------------------------------------


def _mock_response(html: str, status: int = 200) -> httpx.Response:
    """Create a mock httpx.Response with given HTML content."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.text = html
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error",
            request=MagicMock(),
            response=resp,
        )
    return resp


class TestPluginSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_MULTI_ARTICLE_HTML),
                _mock_response("<html></html>"),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert len(results) == 2
        assert "LEGO.Batman" in results[0].title
        assert results[0].category == 2000
        assert results[0].download_link == "https://filecrypt.cc/Container/ABC123.html"
        assert len(results[0].download_links) == 2
        assert results[0].size == "6072 MB"
        assert results[0].metadata["imdb_rating"] == "7.3"
        assert results[0].metadata["imdb_id"] == "tt4116284"

        assert "Caped.Crusader" in results[1].title
        assert results[1].category == 5000

    async def test_search_empty_query(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin._client = AsyncMock()

        results = await plugin.search("")
        assert results == []

    async def test_search_no_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_mock_response("<html><body>Keine Ergebnisse</body></html>"),
        )
        plugin._client = mock_client

        results = await plugin.search("nonexistent")
        assert results == []

    async def test_category_filter_movies(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_MULTI_ARTICLE_HTML),
                _mock_response("<html></html>"),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman", category=2000)

        # Only movie result should remain
        assert len(results) == 1
        assert results[0].category == 2000

    async def test_category_filter_tv(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_MULTI_ARTICLE_HTML),
                _mock_response("<html></html>"),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman", category=5000)

        # Only series result should remain
        assert len(results) == 1
        assert results[0].category == 5000

    async def test_season_filter(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_MULTI_ARTICLE_HTML),
                _mock_response("<html></html>"),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman", season=1)

        # When season is set and no category, restrict to TV
        assert all(r.category >= 5000 for r in results)

    async def test_articles_without_links_skipped(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_NO_LINKS_ARTICLE_HTML),
                _mock_response("<html></html>"),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("test")
        assert results == []

    async def test_pagination_respects_last_page(self) -> None:
        """Plugin should not request pages beyond the last page."""
        plugin = _make_plugin()
        plugin._domain_verified = True

        page1_html = f"""
        <html><body>
        {_SINGLE_ARTICLE_HTML}
        <div class="nav-links">
            <span class="page-numbers current">1</span>
            <a class="page-numbers" href="/page/2/?s=test">2</a>
        </div>
        </body></html>
        """
        page2_html = f"""
        <html><body>
        {_SERIES_ARTICLE_HTML}
        </body></html>
        """

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(page1_html),
                _mock_response(page2_html),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("test")

        assert len(results) == 2
        assert mock_client.get.call_count == 2

    async def test_published_date_set(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response(_SINGLE_ARTICLE_HTML),
                _mock_response("<html></html>"),
            ]
        )
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert results[0].published_date == "2026-01-15 13:41"

    async def test_http_error_returns_empty(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        plugin._client = mock_client

        results = await plugin.search("test")
        assert results == []


class TestCleanup:
    async def test_cleanup_closes_client(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock()
        plugin._client = mock_client

        await plugin.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert plugin._client is None
