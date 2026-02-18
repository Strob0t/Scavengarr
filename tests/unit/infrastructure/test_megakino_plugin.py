"""Tests for the megakino.me Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "megakino.py"


def _load_module() -> ModuleType:
    """Load megakino.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location("megakino_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_MegakinoPlugin = _mod.MegakinoPlugin
_SearchResultParser = _mod._SearchResultParser
_DetailPageParser = _mod._DetailPageParser
_clean_title = _mod._clean_title
_detect_series = _mod._detect_series
_detect_category = _mod._detect_category
_domain_from_url = _mod._domain_from_url
_parse_genres = _mod._parse_genres
_parse_year = _mod._parse_year
_parse_runtime = _mod._parse_runtime


def _make_plugin() -> object:
    """Create MegakinoPlugin instance with domain verification skipped."""
    plug = _MegakinoPlugin()
    plug._domain_verified = True
    return plug


# ---------------------------------------------------------------------------
# Sample HTML fragments
# ---------------------------------------------------------------------------

_SEARCH_HTML = """\
<html><body>
<a class="poster grid-item d-flex fd-column has-overlay" \
href="/adventure/4804-the-lego-movie-2.html">
  <div class="poster__img img-responsive">
    <img data-src="/uploads/posts/2024/lego.webp" alt="The LEGO Movie 2">
    <div class="poster__label">HD</div>
  </div>
  <div class="poster__desc">
    <h3 class="poster__title ws-nowrap">The LEGO Movie 2</h3>
    <ul class="poster__subtitle ws-nowrap">
      <li>Canada, Denmark, 2019</li>
      <li>Adventure / Animation / Filme</li>
    </ul>
    <div class="poster__text line-clamp">DUPLO-Invasoren zerstoert.</div>
  </div>
</a>

<a class="poster grid-item d-flex fd-column has-overlay" \
href="/crime/4692-the-penguin-staffel-1.html">
  <div class="poster__img img-responsive">
    <img data-src="/uploads/posts/2024/penguin.webp" alt="The Penguin - Staffel 1">
    <div class="poster__label">Komplett</div>
  </div>
  <div class="poster__desc">
    <h3 class="poster__title ws-nowrap">The Penguin - Staffel 1</h3>
    <ul class="poster__subtitle ws-nowrap">
      <li>Ireland, United, 2024</li>
      <li>Crime / Drama / Serien</li>
    </ul>
    <div class="poster__text line-clamp">Oz Cobb kaempft um die Macht in Gotham.</div>
  </div>
</a>
</body></html>
"""

_FILM_DETAIL_HTML = """\
<html><body>
<h1>The LEGO Movie 2</h1>
<div class="pmovie__original-title">The Lego Movie 2: The Second Part</div>
<div class="pmovie__year">
  <span>Canada, Denmark</span>, <span><a href="/year/2019/">2019</a></span>, 107 min
</div>
<div class="pmovie__genres">Adventure / Animation / Filme</div>
<div class="pmovie__poster img-fit-cover">
  <img data-src="/uploads/posts/2024/lego.webp" alt="The LEGO Movie 2">
  <div class="poster__label">HD</div>
</div>
<div class="pmovie__subrating pmovie__subrating--kp">6.681</div>
<div class="pmovie__subrating pmovie__subrating--site rating-9"><div>8.5</div></div>
<div class="page__text full-text clearfix">DUPLO-Invasoren haben die heile LEGO-Welt \
zerstoert. Emmet muss seine Freunde retten.</div>
<div class="pmovie__player tabs-block">
  <div class="pmovie__player-controls d-flex">
    <div class="tabs-block__select d-flex flex-grow-1">
      <span class="is-active">Voe</span>
      <span>Doodstream</span>
    </div>
  </div>
  <div class="tabs-block__content video-inside video-responsive">
    <iframe id="film_main" data-src="https://voe.sx/e/w2a1rxa6c3en" \
scrolling="no" frameborder="0" allowfullscreen></iframe>
  </div>
  <div class="tabs-block__content d-none video-inside video-responsive">
    <iframe id="film_main" data-src="https://d0000d.com/e/xgd6geij4okm" \
scrolling="no" frameborder="0" allowfullscreen></iframe>
  </div>
</div>
</body></html>
"""

_SERIES_DETAIL_HTML = """\
<html><body>
<h1>The Penguin - Staffel 1</h1>
<div class="pmovie__original-title">The Penguin</div>
<div class="pmovie__year">
  <span>Ireland, United States</span>, , min
</div>
<div class="pmovie__genres">Crime / Drama / Serien</div>
<div class="pmovie__poster img-fit-cover">
  <img data-src="/uploads/posts/2024/penguin.webp" alt="The Penguin">
</div>
<div class="pmovie__subrating pmovie__subrating--kp">7,8</div>
<div class="pmovie__subrating pmovie__subrating--site rating-9"><div>9.2</div></div>
<div class="page__text full-text clearfix">Oz Cobb kaempft um Macht in Gothams \
Unterwelt nach dem Tod von Carmine Falcone.</div>
<div class="pmovie__player tabs-block">
  <div class="pmovie__series-select d-flex ai-center flex-grow-1">
    <select class="flex-grow-1 se-select">
      <option value="ep1">Episode 1</option>
      <option value="ep2">Episode 2</option>
      <option value="ep3">Episode 3</option>
    </select>
    <select class="flex-grow-1 mr-select" id="ep1">
      <option value="https://voe.sx/e/g5sv7hvi8cfw">Voe</option>
      <option value="https://dood.wf/e/abc123">Doodstream</option>
    </select>
    <select class="flex-grow-1 mr-select" style="display: none;" id="ep2">
      <option value="https://voe.sx/e/ep2abc">Voe</option>
    </select>
    <select class="flex-grow-1 mr-select" style="display: none;" id="ep3">
      <option value="https://voe.sx/e/ep3abc">Voe</option>
    </select>
  </div>
</div>
</body></html>
"""

_EMPTY_DETAIL_HTML = """\
<html><body>
<h1>No Streams Available</h1>
<div class="full-text">Some description.</div>
</body></html>
"""

_ANIMATION_SEARCH_HTML = """\
<html><body>
<a class="poster grid-item d-flex" href="/multfilm/1234-one-piece-film.html">
  <div class="poster__img">
    <img data-src="/poster.jpg" alt="One Piece Film: Red">
    <div class="poster__label">HD</div>
  </div>
  <div class="poster__desc">
    <h3 class="poster__title">One Piece Film: Red</h3>
    <ul class="poster__subtitle">
      <li>Japan, 2022</li>
      <li>Animation / Action / Filme</li>
    </ul>
    <div class="poster__text">A music festival reveals a secret.</div>
  </div>
</a>
</body></html>
"""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _mock_response(text: str, status_code: int = 200) -> httpx.Response:
    """Create a mock httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("POST", "https://megakino1.biz/"),
    )


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------


class TestCleanTitle:
    def test_strips_film_suffix(self) -> None:
        assert _clean_title("Batman Film") == "Batman"

    def test_strips_serie_suffix(self) -> None:
        assert _clean_title("Stranger Things Serie") == "Stranger Things"

    def test_strips_trailing_year(self) -> None:
        assert _clean_title("Batman (2005)") == "Batman"

    def test_normal_title_unchanged(self) -> None:
        assert _clean_title("The LEGO Movie 2") == "The LEGO Movie 2"

    def test_strips_whitespace(self) -> None:
        assert _clean_title("  Batman  ") == "Batman"


class TestDetectSeries:
    def test_detects_from_serien_genre(self) -> None:
        assert _detect_series("Crime / Drama / Serien", "The Penguin") is True

    def test_detects_from_staffel_title(self) -> None:
        assert _detect_series("Crime / Drama / Filme", "Show - Staffel 1") is True

    def test_not_series(self) -> None:
        assert _detect_series("Action / Drama / Filme", "Batman Begins") is False

    def test_empty_text(self) -> None:
        assert _detect_series("", "Batman") is False


class TestDetectCategory:
    def test_movie_default(self) -> None:
        assert _detect_category(["Action", "Drama"], is_series=False) == 2000

    def test_series(self) -> None:
        assert _detect_category(["Crime", "Drama"], is_series=True) == 5000

    def test_animation_movie(self) -> None:
        assert _detect_category(["Animation", "Action"], is_series=False) == 5070

    def test_animation_series(self) -> None:
        assert _detect_category(["Animation", "Action"], is_series=True) == 5070


class TestDomainFromUrl:
    def test_extracts_domain(self) -> None:
        assert _domain_from_url("https://voe.sx/e/abc") == "voe"

    def test_strips_www(self) -> None:
        assert _domain_from_url("https://www.example.com/page") == "example"

    def test_invalid_url(self) -> None:
        assert _domain_from_url("not-a-url") == "unknown"


class TestParseGenres:
    def test_filters_site_categories(self) -> None:
        assert _parse_genres("Adventure / Animation / Filme") == [
            "Adventure",
            "Animation",
        ]

    def test_filters_serien(self) -> None:
        assert _parse_genres("Crime / Drama / Serien") == ["Crime", "Drama"]

    def test_filters_dokumentationen(self) -> None:
        assert _parse_genres("Dokumentationen / History") == ["History"]

    def test_empty_string(self) -> None:
        assert _parse_genres("") == []

    def test_single_genre(self) -> None:
        assert _parse_genres("Action") == ["Action"]


class TestParseYear:
    def test_extracts_year(self) -> None:
        assert _parse_year("Canada, 2019, 107 min") == "2019"

    def test_no_year(self) -> None:
        assert _parse_year("Ireland, United, , min") == ""

    def test_extracts_from_mixed_text(self) -> None:
        assert _parse_year("Released 2024 worldwide") == "2024"


class TestParseRuntime:
    def test_extracts_runtime(self) -> None:
        assert _parse_runtime("Canada, 2019, 107 min") == "107"

    def test_no_runtime(self) -> None:
        assert _parse_runtime("Ireland, United, , min") == ""

    def test_extracts_runtime_no_space(self) -> None:
        assert _parse_runtime("120min") == "120"


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestSearchResultParser:
    def test_parses_search_results(self) -> None:
        parser = _SearchResultParser("https://megakino.me")
        parser.feed(_SEARCH_HTML)

        assert len(parser.results) == 2

        first = parser.results[0]
        assert first["title"] == "The LEGO Movie 2"
        assert (
            first["url"] == "https://megakino.me/adventure/4804-the-lego-movie-2.html"
        )
        assert first["quality"] == "HD"
        assert first["is_series"] is False
        assert "Adventure" in first["genres"]
        assert "Animation" in first["genres"]
        assert first["year"] == "2019"
        assert "DUPLO" in first["description"]

        second = parser.results[1]
        assert second["title"] == "The Penguin - Staffel 1"
        assert second["is_series"] is True
        assert "Crime" in second["genres"]
        assert "Drama" in second["genres"]

    def test_empty_page(self) -> None:
        parser = _SearchResultParser("https://megakino.me")
        parser.feed("<html><body>No results</body></html>")
        assert len(parser.results) == 0

    def test_card_without_title_skipped(self) -> None:
        html = """\
        <a class="poster grid-item" href="/films/999-test.html">
          <div class="poster__img"><img data-src="/img.jpg"></div>
        </a>
        """
        parser = _SearchResultParser("https://megakino.me")
        parser.feed(html)
        assert len(parser.results) == 0

    def test_animation_search(self) -> None:
        parser = _SearchResultParser("https://megakino.me")
        parser.feed(_ANIMATION_SEARCH_HTML)

        assert len(parser.results) == 1
        result = parser.results[0]
        assert result["title"] == "One Piece Film: Red"
        assert "Animation" in result["genres"]
        assert result["is_series"] is False

    def test_poster_url_extracted(self) -> None:
        parser = _SearchResultParser("https://megakino.me")
        parser.feed(_SEARCH_HTML)
        assert (
            parser.results[0]["poster_url"]
            == "https://megakino.me/uploads/posts/2024/lego.webp"
        )

    def test_komplett_label_not_quality(self) -> None:
        parser = _SearchResultParser("https://megakino.me")
        parser.feed(_SEARCH_HTML)
        second = parser.results[1]
        assert second["label"] == "Komplett"
        assert second["quality"] == ""


class TestDetailPageParser:
    def test_parses_film_stream_tabs(self) -> None:
        parser = _DetailPageParser("https://megakino.me")
        parser.feed(_FILM_DETAIL_HTML)
        parser.finalize()

        assert len(parser.stream_links) == 2

        first = parser.stream_links[0]
        assert first["hoster"] == "voe"
        assert first["link"] == "https://voe.sx/e/w2a1rxa6c3en"
        assert first["label"] == "Voe"

        second = parser.stream_links[1]
        assert second["hoster"] == "doodstream"
        assert second["link"] == "https://d0000d.com/e/xgd6geij4okm"
        assert second["label"] == "Doodstream"

    def test_parses_film_metadata(self) -> None:
        parser = _DetailPageParser("https://megakino.me")
        parser.feed(_FILM_DETAIL_HTML)
        parser.finalize()

        assert parser.title == "The LEGO Movie 2"
        assert parser.year == "2019"
        assert parser.runtime == "107"
        assert "Adventure" in parser.genres
        assert "Animation" in parser.genres
        assert parser.kp_rating == "6.681"
        assert parser.site_rating == "8.5"
        assert parser.is_series is False

    def test_parses_film_description(self) -> None:
        parser = _DetailPageParser("https://megakino.me")
        parser.feed(_FILM_DETAIL_HTML)
        parser.finalize()

        assert "DUPLO" in parser.description
        assert "Emmet" in parser.description

    def test_parses_film_poster(self) -> None:
        parser = _DetailPageParser("https://megakino.me")
        parser.feed(_FILM_DETAIL_HTML)
        assert parser.poster_url == "https://megakino.me/uploads/posts/2024/lego.webp"

    def test_parses_series_hosters(self) -> None:
        parser = _DetailPageParser("https://megakino.me")
        parser.feed(_SERIES_DETAIL_HTML)
        parser.finalize()

        # All mr-select elements are collected (ep1=2 hosters, ep2=1, ep3=1)
        assert len(parser.stream_links) == 4

        first = parser.stream_links[0]
        assert first["hoster"] == "voe"
        assert first["link"] == "https://voe.sx/e/g5sv7hvi8cfw"
        assert first["label"] == "1x1 Voe"

        second = parser.stream_links[1]
        assert second["hoster"] == "doodstream"
        assert second["link"] == "https://dood.wf/e/abc123"
        assert second["label"] == "1x1 Doodstream"

        third = parser.stream_links[2]
        assert third["hoster"] == "voe"
        assert third["link"] == "https://voe.sx/e/ep2abc"
        assert third["label"] == "1x2 Voe"

        fourth = parser.stream_links[3]
        assert fourth["hoster"] == "voe"
        assert fourth["link"] == "https://voe.sx/e/ep3abc"
        assert fourth["label"] == "1x3 Voe"

    def test_series_detection(self) -> None:
        parser = _DetailPageParser("https://megakino.me")
        parser.feed(_SERIES_DETAIL_HTML)
        parser.finalize()

        assert parser.is_series is True
        assert parser.title == "The Penguin - Staffel 1"

    def test_series_metadata(self) -> None:
        parser = _DetailPageParser("https://megakino.me")
        parser.feed(_SERIES_DETAIL_HTML)
        parser.finalize()

        assert "Crime" in parser.genres
        assert "Drama" in parser.genres
        assert parser.kp_rating == "7.8"
        assert parser.site_rating == "9.2"

    def test_empty_detail(self) -> None:
        parser = _DetailPageParser("https://megakino.me")
        parser.feed(_EMPTY_DETAIL_HTML)
        parser.finalize()

        assert parser.title == "No Streams Available"
        assert len(parser.stream_links) == 0
        assert parser.is_series is False

    def test_genres_filter_site_categories(self) -> None:
        parser = _DetailPageParser("https://megakino.me")
        parser.feed(_FILM_DETAIL_HTML)
        parser.finalize()

        # "Filme" should be filtered out
        assert "Filme" not in parser.genres
        assert "Adventure" in parser.genres


# ---------------------------------------------------------------------------
# Plugin integration tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestMegakinoPluginAttributes:
    def test_plugin_name(self) -> None:
        plug = _make_plugin()
        assert plug.name == "megakino"

    def test_plugin_version(self) -> None:
        plug = _make_plugin()
        assert plug.version == "1.0.0"

    def test_plugin_mode(self) -> None:
        plug = _make_plugin()
        assert plug.mode == "httpx"

    def test_plugin_provides(self) -> None:
        plug = _make_plugin()
        assert plug.provides == "stream"


class TestMegakinoPluginSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.post = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),  # search page 1
                _mock_response(""),  # search page 2 (empty → stop)
            ]
        )
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response("", 204),  # yg_token
                _mock_response(_FILM_DETAIL_HTML),  # LEGO detail
                _mock_response(_SERIES_DETAIL_HTML),  # Penguin detail
            ]
        )

        plug._client = mock_client
        results = await plug.search("batman")

        assert len(results) == 2
        titles = {r.title for r in results}
        assert "The LEGO Movie 2" in titles
        assert "The Penguin - Staffel 1" in titles

    @pytest.mark.asyncio
    async def test_search_film_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <a class="poster grid-item" href="/films/123-batman.html">
          <div class="poster__img"><img data-src="/img.jpg">
            <div class="poster__label">HD</div>
          </div>
          <div class="poster__desc">
            <h3 class="poster__title">Batman Begins</h3>
            <ul class="poster__subtitle">
              <li>USA, 2005</li>
              <li>Action / Filme</li>
            </ul>
            <div class="poster__text">Bruce Wayne becomes Batman.</div>
          </div>
        </a>
        """

        mock_client.post = AsyncMock(
            side_effect=[
                _mock_response(search_html),
                _mock_response(""),
            ]
        )
        mock_client.get = AsyncMock(return_value=_mock_response(_FILM_DETAIL_HTML))

        plug._client = mock_client
        results = await plug.search("batman")

        assert len(results) == 1
        first = results[0]
        assert first.title == "The LEGO Movie 2"
        assert first.category == 5070  # Animation genre → 5070
        assert first.download_link.startswith("https://")
        assert first.download_links is not None
        assert len(first.download_links) == 2

    @pytest.mark.asyncio
    async def test_search_series_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <a class="poster grid-item" href="/crime/4692-penguin.html">
          <div class="poster__img"><img data-src="/img.jpg">
            <div class="poster__label">Komplett</div>
          </div>
          <div class="poster__desc">
            <h3 class="poster__title">The Penguin - Staffel 1</h3>
            <ul class="poster__subtitle">
              <li>USA, 2024</li>
              <li>Crime / Drama / Serien</li>
            </ul>
            <div class="poster__text">Oz fights for power.</div>
          </div>
        </a>
        """

        mock_client.post = AsyncMock(
            side_effect=[
                _mock_response(search_html),
                _mock_response(""),
            ]
        )
        mock_client.get = AsyncMock(return_value=_mock_response(_SERIES_DETAIL_HTML))

        plug._client = mock_client
        results = await plug.search("penguin")

        assert len(results) == 1
        first = results[0]
        assert first.title == "The Penguin - Staffel 1"
        assert first.category == 5000

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        plug._client = mock_client

        results = await plug.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_no_results(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.post = AsyncMock(
            return_value=_mock_response("<html><body>No results</body></html>")
        )

        plug._client = mock_client
        results = await plug.search("xyznonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.ConnectError("connection failed")
        )

        plug._client = mock_client
        results = await plug.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_detail_page_error_skips_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <a class="poster grid-item" href="/films/123-test.html">
          <div class="poster__desc">
            <h3 class="poster__title">Test Film</h3>
            <ul class="poster__subtitle">
              <li>USA, 2024</li>
              <li>Action / Filme</li>
            </ul>
          </div>
        </a>
        """

        mock_client.post = AsyncMock(
            side_effect=[
                _mock_response(search_html),
                _mock_response(""),
            ]
        )
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("detail failed"))

        plug._client = mock_client
        results = await plug.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_detail_without_streams_skips_result(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <a class="poster grid-item" href="/films/123-test.html">
          <div class="poster__desc">
            <h3 class="poster__title">Test Film</h3>
            <ul class="poster__subtitle">
              <li>USA, 2024</li>
              <li>Action / Filme</li>
            </ul>
          </div>
        </a>
        """

        mock_client.post = AsyncMock(
            side_effect=[
                _mock_response(search_html),
                _mock_response(""),
            ]
        )
        mock_client.get = AsyncMock(return_value=_mock_response(_EMPTY_DETAIL_HTML))

        plug._client = mock_client
        results = await plug.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_result_metadata(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        search_html = """\
        <a class="poster grid-item" href="/films/123-lego.html">
          <div class="poster__img"><img data-src="/img.jpg">
            <div class="poster__label">HD</div>
          </div>
          <div class="poster__desc">
            <h3 class="poster__title">The LEGO Movie 2</h3>
            <ul class="poster__subtitle">
              <li>USA, 2019</li>
              <li>Adventure / Animation / Filme</li>
            </ul>
          </div>
        </a>
        """

        mock_client.post = AsyncMock(
            side_effect=[
                _mock_response(search_html),
                _mock_response(""),
            ]
        )
        mock_client.get = AsyncMock(return_value=_mock_response(_FILM_DETAIL_HTML))

        plug._client = mock_client
        results = await plug.search("lego")

        first = results[0]
        assert first.metadata.get("kp_rating") == "6.681"
        assert first.metadata.get("site_rating") == "8.5"
        assert first.metadata.get("year") == "2019"
        assert "Adventure" in first.metadata.get("genres", "")


class TestMegakinoCategoryFiltering:
    @pytest.mark.asyncio
    async def test_filter_movies_only(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.post = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),
                _mock_response(""),
            ]
        )
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response("", 204),  # yg_token
                _mock_response(_FILM_DETAIL_HTML),  # Animation → 5070
                _mock_response(_SERIES_DETAIL_HTML),  # Series → 5000
            ]
        )

        plug._client = mock_client
        results = await plug.search("test", category=2000)

        # Animation (5070) and Series (5000) both have category >= 5000
        # Movie filter (2000) only keeps category < 5000
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_filter_series_only(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.post = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),
                _mock_response(""),
            ]
        )
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response("", 204),  # yg_token
                _mock_response(_FILM_DETAIL_HTML),  # Animation → 5070
                _mock_response(_SERIES_DETAIL_HTML),  # Series → 5000
            ]
        )

        plug._client = mock_client
        results = await plug.search("test", category=5000)

        # Both Animation (5070) and Series (5000) have category >= 5000
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_no_category_returns_all(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()

        mock_client.post = AsyncMock(
            side_effect=[
                _mock_response(_SEARCH_HTML),
                _mock_response(""),
            ]
        )
        mock_client.get = AsyncMock(
            side_effect=[
                _mock_response("", 204),  # yg_token
                _mock_response(_FILM_DETAIL_HTML),
                _mock_response(_SERIES_DETAIL_HTML),
            ]
        )

        plug._client = mock_client
        results = await plug.search("test")

        assert len(results) == 2


class TestMegakinoDomainFallback:
    @pytest.mark.asyncio
    async def test_uses_first_working_domain(self) -> None:
        plug = _MegakinoPlugin()
        mock_client = AsyncMock()

        mock_client.head = AsyncMock(return_value=_mock_response("", 200))

        plug._client = mock_client
        await plug._verify_domain()

        assert plug.base_url == "https://megakino1.biz"
        assert plug._domain_verified is True

    @pytest.mark.asyncio
    async def test_falls_back_to_first_domain(self) -> None:
        plug = _MegakinoPlugin()
        mock_client = AsyncMock()

        mock_client.head = AsyncMock(side_effect=httpx.ConnectError("all down"))

        plug._client = mock_client
        await plug._verify_domain()

        assert plug.base_url == "https://megakino1.biz"
        assert plug._domain_verified is True

    @pytest.mark.asyncio
    async def test_skips_verification_if_done(self) -> None:
        plug = _MegakinoPlugin()
        plug._domain_verified = True
        plug.base_url = "https://custom.domain"

        mock_client = AsyncMock()
        plug._client = mock_client
        await plug._verify_domain()

        mock_client.head.assert_not_called()
        assert plug.base_url == "https://custom.domain"


class TestMegakinoCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self) -> None:
        plug = _make_plugin()
        mock_client = AsyncMock()
        plug._client = mock_client

        await plug.cleanup()

        mock_client.aclose.assert_called_once()
        assert plug._client is None

    @pytest.mark.asyncio
    async def test_cleanup_noop_without_client(self) -> None:
        plug = _make_plugin()
        plug._client = None

        await plug.cleanup()
        assert plug._client is None
