"""Tests for the nima4k.org Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "nima4k.py"


def _load_nima4k_module() -> ModuleType:
    """Load nima4k.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("nima4k_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once at module level for parser tests
_nima4k = _load_nima4k_module()
_Nima4kPlugin = _nima4k.Nima4kPlugin
_ListingParser = _nima4k._ListingParser
_extract_release_id = _nima4k._extract_release_id
_build_download_links = _nima4k._build_download_links
_category_to_torznab = _nima4k._category_to_torznab
_looks_like_size = _nima4k._looks_like_size
_CATEGORY_PATH_MAP = _nima4k._CATEGORY_PATH_MAP
_CATEGORY_NAME_MAP = _nima4k._CATEGORY_NAME_MAP


def _make_plugin() -> object:
    """Create Nima4kPlugin instance."""
    return _Nima4kPlugin()


# Sample listing HTML matching nima4k.org structure
_LISTING_HTML = """
<div class="article">
  <h2><a class="release-details" href="/release/4296/batman-begins-2005-uhd-bluray">
    Batman Begins
  </a></h2>
  <span class="subtitle">Batman.Begins.2005.UHD.BluRay.2160p.DTS-HD.MA.5.1</span>
  <ul class="release-infos">
    <li>45.2 GB</li>
    <li>DDL</li>
  </ul>
  <ul class="genre-pills">
    <li><a href="/movies">Movies</a></li>
  </ul>
  <p class="meta"><span>2024-01-15</span></p>
</div>
<div class="article">
  <h2><a class="release-details" href="/release/4300/breaking-bad-s01-uhd">
    Breaking Bad S01
  </a></h2>
  <span class="subtitle">Breaking.Bad.S01.UHD.BluRay.2160p</span>
  <ul class="release-infos">
    <li>120 GB</li>
  </ul>
  <ul class="genre-pills">
    <li><a href="/serien">Serien</a></li>
  </ul>
  <p class="meta"><span>2024-02-20</span></p>
</div>
"""

_PAGINATION_HTML = """
<div class="article">
  <h2><a class="release-details" href="/release/100/test-release">
    Test Release
  </a></h2>
  <span class="subtitle">Test.Release.2024</span>
  <ul class="release-infos">
    <li>10 GB</li>
  </ul>
  <ul class="genre-pills">
    <li><a href="/movies">Movies</a></li>
  </ul>
</div>
<ul class="uk-pagination">
  <li><a href="/movies/page-2">2</a></li>
  <li><a href="/movies/page-3">3</a></li>
</ul>
"""

_EMPTY_HTML = "<html><body><p>No results found</p></body></html>"


class TestListingParser:
    def test_parses_two_articles(self) -> None:
        parser = _ListingParser("https://nima4k.org")
        parser.feed(_LISTING_HTML)

        assert len(parser.results) == 2

    def test_title_extracted(self) -> None:
        parser = _ListingParser("https://nima4k.org")
        parser.feed(_LISTING_HTML)

        assert parser.results[0]["title"] == "Batman Begins"

    def test_url_extracted(self) -> None:
        parser = _ListingParser("https://nima4k.org")
        parser.feed(_LISTING_HTML)

        assert (
            parser.results[0]["url"]
            == "https://nima4k.org/release/4296/batman-begins-2005-uhd-bluray"
        )

    def test_release_name_extracted(self) -> None:
        parser = _ListingParser("https://nima4k.org")
        parser.feed(_LISTING_HTML)

        assert (
            parser.results[0]["release_name"]
            == "Batman.Begins.2005.UHD.BluRay.2160p.DTS-HD.MA.5.1"
        )

    def test_size_extracted(self) -> None:
        parser = _ListingParser("https://nima4k.org")
        parser.feed(_LISTING_HTML)

        assert parser.results[0]["size"] == "45.2 GB"

    def test_categories_extracted(self) -> None:
        parser = _ListingParser("https://nima4k.org")
        parser.feed(_LISTING_HTML)

        assert parser.results[0]["categories"] == ["Movies"]
        assert parser.results[1]["categories"] == ["Serien"]

    def test_date_extracted(self) -> None:
        parser = _ListingParser("https://nima4k.org")
        parser.feed(_LISTING_HTML)

        assert parser.results[0]["date"] == "2024-01-15"

    def test_pagination_detected(self) -> None:
        parser = _ListingParser("https://nima4k.org")
        parser.feed(_PAGINATION_HTML)

        assert parser.has_next_page is True

    def test_no_pagination_without_links(self) -> None:
        parser = _ListingParser("https://nima4k.org")
        parser.feed(_LISTING_HTML)

        assert parser.has_next_page is False

    def test_empty_html_returns_no_results(self) -> None:
        parser = _ListingParser("https://nima4k.org")
        parser.feed(_EMPTY_HTML)

        assert parser.results == []

    def test_skips_imdb_genre_pills(self) -> None:
        html = """
        <div class="article">
          <h2><a class="release-details" href="/release/99/test">Title</a></h2>
          <span class="subtitle">Release.Name</span>
          <ul class="release-infos"><li>10 GB</li></ul>
          <ul class="genre-pills">
            <li><a href="/movies">Movies</a></li>
            <li><a href="https://imdb.com/title/tt123">IMDb</a></li>
          </ul>
        </div>
        """
        parser = _ListingParser("https://nima4k.org")
        parser.feed(html)

        assert parser.results[0]["categories"] == ["Movies"]

    def test_skips_xrel_genre_pills(self) -> None:
        html = """
        <div class="article">
          <h2><a class="release-details" href="/release/99/test">Title</a></h2>
          <span class="subtitle">Release.Name</span>
          <ul class="release-infos"><li>10 GB</li></ul>
          <ul class="genre-pills">
            <li><a href="/serien">Serien</a></li>
            <li><a href="https://xrel.to/some/link">xREL</a></li>
          </ul>
        </div>
        """
        parser = _ListingParser("https://nima4k.org")
        parser.feed(html)

        assert parser.results[0]["categories"] == ["Serien"]

    def test_article_without_url_skipped(self) -> None:
        html = """
        <div class="article">
          <h2>No link here</h2>
          <span class="subtitle">Release.Name</span>
        </div>
        """
        parser = _ListingParser("https://nima4k.org")
        parser.feed(html)

        assert parser.results == []

    def test_nested_divs_do_not_exit_article_early(self) -> None:
        html = """
        <div class="article">
          <div class="inner">
            <h2><a class="release-details" href="/release/50/test">Title</a></h2>
          </div>
          <span class="subtitle">Release</span>
          <ul class="release-infos"><li>5 GB</li></ul>
          <ul class="genre-pills">
            <li><a href="/movies">Movies</a></li>
          </ul>
        </div>
        """
        parser = _ListingParser("https://nima4k.org")
        parser.feed(html)

        assert len(parser.results) == 1
        assert parser.results[0]["title"] == "Title"


class TestExtractReleaseId:
    def test_extracts_id_from_url(self) -> None:
        assert (
            _extract_release_id(
                "https://nima4k.org/release/4296/batman-begins-2005-uhd"
            )
            == "4296"
        )

    def test_extracts_id_from_relative_url(self) -> None:
        assert _extract_release_id("/release/100/some-slug") == "100"

    def test_returns_none_for_invalid_url(self) -> None:
        assert _extract_release_id("https://nima4k.org/movies") is None

    def test_returns_none_for_empty_string(self) -> None:
        assert _extract_release_id("") is None


class TestBuildDownloadLinks:
    def test_builds_two_links(self) -> None:
        links = _build_download_links("4296", "https://nima4k.org")

        assert len(links) == 2

    def test_ddl_link(self) -> None:
        links = _build_download_links("4296", "https://nima4k.org")

        assert links[0] == {
            "hoster": "ddl.to",
            "link": "https://nima4k.org/go/4296/ddl.to",
        }

    def test_rapidgator_link(self) -> None:
        links = _build_download_links("4296", "https://nima4k.org")

        assert links[1] == {
            "hoster": "rapidgator",
            "link": "https://nima4k.org/go/4296/rapidgator",
        }


class TestCategoryMapping:
    def test_movies_mapped(self) -> None:
        assert _category_to_torznab(["Movies"]) == 2000

    def test_serien_mapped(self) -> None:
        assert _category_to_torznab(["Serien"]) == 5000

    def test_dokumentationen_mapped(self) -> None:
        assert _category_to_torznab(["Dokumentationen"]) == 5070

    def test_sports_mapped(self) -> None:
        assert _category_to_torznab(["Sports"]) == 5060

    def test_music_mapped(self) -> None:
        assert _category_to_torznab(["Music"]) == 3000

    def test_default_fallback(self) -> None:
        assert _category_to_torznab(["Unknown"]) == 2000

    def test_empty_list_defaults(self) -> None:
        assert _category_to_torznab([]) == 2000

    def test_path_map_movies(self) -> None:
        assert _CATEGORY_PATH_MAP[2000] == "movies"

    def test_path_map_serien(self) -> None:
        assert _CATEGORY_PATH_MAP[5000] == "serien"

    def test_path_map_docs(self) -> None:
        assert _CATEGORY_PATH_MAP[5070] == "dokumentationen"


class TestLooksLikeSize:
    def test_gb_size(self) -> None:
        assert _looks_like_size("45.2 GB") is True

    def test_mb_size(self) -> None:
        assert _looks_like_size("800 MB") is True

    def test_tb_size(self) -> None:
        assert _looks_like_size("1.5 TB") is True

    def test_not_size(self) -> None:
        assert _looks_like_size("DDL") is False

    def test_text_not_size(self) -> None:
        assert _looks_like_size("Some random text") is False


class TestPluginAttributes:
    def test_name(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "nima4k"

    def test_version(self) -> None:
        plugin = _make_plugin()
        assert plugin.version == "1.0.0"

    def test_mode(self) -> None:
        plugin = _make_plugin()
        assert plugin.mode == "httpx"


class TestSearchPost:
    async def test_post_search_returns_results(self) -> None:
        plugin = _make_plugin()

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = _LISTING_HTML
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.get = AsyncMock(return_value=mock_response)
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert len(results) == 2
        assert results[0].title == "Batman Begins"
        assert "go/4296/ddl.to" in results[0].download_link
        assert len(results[0].download_links) == 2
        mock_client.post.assert_awaited_once()

    async def test_post_search_sends_correct_data(self) -> None:
        plugin = _make_plugin()

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = _EMPTY_HTML
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        plugin._client = mock_client

        await plugin.search("batman")

        mock_client.post.assert_awaited_once_with(
            "https://nima4k.org/search",
            data={"search": "batman"},
        )

    async def test_post_search_empty_results(self) -> None:
        plugin = _make_plugin()

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = _EMPTY_HTML
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        plugin._client = mock_client

        results = await plugin.search("nonexistent")

        assert results == []

    async def test_post_search_error_returns_empty(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("unreachable"))
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert results == []


class TestCategoryBrowsing:
    async def test_browse_category_returns_results(self) -> None:
        plugin = _make_plugin()

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = _LISTING_HTML  # no pagination
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.post = AsyncMock()
        plugin._client = mock_client

        results = await plugin.search("", category=2000)

        assert len(results) == 2
        # Should use GET, not POST
        mock_client.get.assert_awaited_once()
        mock_client.post.assert_not_awaited()

    async def test_browse_fetches_correct_url(self) -> None:
        plugin = _make_plugin()

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = _EMPTY_HTML
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)
        plugin._client = mock_client

        await plugin.search("", category=5000)

        mock_client.get.assert_awaited_once_with("https://nima4k.org/serien")

    async def test_browse_paginates(self) -> None:
        plugin = _make_plugin()

        page1_response = AsyncMock(spec=httpx.Response)
        page1_response.status_code = 200
        page1_response.text = _PAGINATION_HTML
        page1_response.raise_for_status = lambda: None

        page2_response = AsyncMock(spec=httpx.Response)
        page2_response.status_code = 200
        page2_response.text = _LISTING_HTML  # no more pagination
        page2_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=[page1_response, page2_response])
        plugin._client = mock_client

        results = await plugin.search("", category=2000)

        # page 1 has 1 article, page 2 has 2 articles
        assert len(results) == 3
        assert mock_client.get.await_count == 2

    async def test_browse_stops_on_empty_page(self) -> None:
        plugin = _make_plugin()

        page1_response = AsyncMock(spec=httpx.Response)
        page1_response.status_code = 200
        page1_response.text = _PAGINATION_HTML
        page1_response.raise_for_status = lambda: None

        empty_response = AsyncMock(spec=httpx.Response)
        empty_response.status_code = 200
        empty_response.text = _EMPTY_HTML
        empty_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=[page1_response, empty_response])
        plugin._client = mock_client

        results = await plugin.search("", category=2000)

        assert len(results) == 1
        assert mock_client.get.await_count == 2

    async def test_no_query_no_category_returns_empty(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        results = await plugin.search("")

        assert results == []


class TestSearchResultConstruction:
    async def test_result_has_download_links(self) -> None:
        plugin = _make_plugin()

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = _LISTING_HTML
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert results[0].download_links[0]["hoster"] == "ddl.to"
        assert results[0].download_links[1]["hoster"] == "rapidgator"

    async def test_result_has_metadata(self) -> None:
        plugin = _make_plugin()

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = _LISTING_HTML
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        plugin._client = mock_client

        results = await plugin.search("batman")

        assert results[0].release_name == (
            "Batman.Begins.2005.UHD.BluRay.2160p.DTS-HD.MA.5.1"
        )
        assert results[0].size == "45.2 GB"
        assert results[0].published_date == "2024-01-15"
        assert results[0].source_url == (
            "https://nima4k.org/release/4296/batman-begins-2005-uhd-bluray"
        )

    async def test_result_category_from_genre_pills(self) -> None:
        plugin = _make_plugin()

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = _LISTING_HTML
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        plugin._client = mock_client

        results = await plugin.search("batman")

        # First result: Movies → 2000
        assert results[0].category == 2000
        # Second result: Serien → 5000
        assert results[1].category == 5000

    async def test_forced_category_overrides_genre(self) -> None:
        plugin = _make_plugin()

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.text = _LISTING_HTML
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        plugin._client = mock_client

        results = await plugin.search("batman", category=5070)

        # Should use forced category, not genre pill
        assert results[0].category == 5070


class TestCleanup:
    async def test_cleanup_closes_client(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert plugin._client is None

    async def test_cleanup_noop_without_client(self) -> None:
        plugin = _make_plugin()

        await plugin.cleanup()

        assert plugin._client is None
