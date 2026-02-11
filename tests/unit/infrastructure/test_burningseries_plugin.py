"""Unit tests for the burningseries (bs.to) plugin."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = (
    Path(__file__).resolve().parents[3] / "plugins" / "burningseries.py"
)


@pytest.fixture()
def bs_mod():
    """Import burningseries plugin module."""
    spec = importlib.util.spec_from_file_location("burningseries", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["burningseries"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("burningseries", None)


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

LISTING_HTML = """\
<div class="genre">
  <span><strong>Drama</strong></span>
  <ul>
    <li><a href="serie/Breaking-Bad" title="Breaking Bad">Breaking Bad</a></li>
    <li><a href="serie/Better-Call-Saul" title="Better Call Saul">\
Better Call Saul</a></li>
  </ul>
</div>
<div class="genre">
  <span><strong>Anime</strong></span>
  <ul>
    <li><a href="serie/Naruto" title="Naruto">Naruto</a></li>
    <li><a href="serie/Attack-on-Titan" \
title="Attack on Titan | Shingeki no Kyojin">\
Attack on Titan | Shingeki no Kyojin</a></li>
  </ul>
</div>
<div class="genre">
  <span><strong>Krimi</strong></span>
  <ul>
    <li><a href="serie/Breaking-Bad" title="Breaking Bad">Breaking Bad</a></li>
  </ul>
</div>
"""

DETAIL_HTML = """\
<section class="serie">
  <div id="sp_left">
    <h2>
      Breaking Bad
      <small>Staffel 1</small>
    </h2>
    <p>Walter White, a chemistry teacher, discovers he has cancer and teams up \
with former student Jesse Pinkman to cook methamphetamine.</p>
    <div class="infos">
      <div>
        <span>Genres</span>
        <p>
          <span style="font-weight: bold;">Drama</span>
          <span>Krimi</span>
          <span>Thriller</span>
        </p>
      </div>
      <div>
        <span>Produktionsjahre</span>
        <p>
          <em>2008 - 2013</em>
        </p>
      </div>
      <div>
        <span>Hauptdarsteller</span>
        <p>
          <span>Bryan Cranston,</span>
          <span>Aaron Paul</span>
        </p>
      </div>
    </div>
  </div>
  <div id="sp_right">
    <img src="/public/images/cover/29.jpg" alt="Cover">
  </div>
  <div class="selectors">
    <div class="seasons">
      <strong>Staffeln</strong>
      <div id="seasons">
        <ul>
          <li class="s0"><a href="serie/Breaking-Bad/0/en">Specials</a></li>
          <li class="s1 active"><a href="serie/Breaking-Bad/1/en">1</a></li>
          <li class="s2"><a href="serie/Breaking-Bad/2/en">2</a></li>
          <li class="s3"><a href="serie/Breaking-Bad/3/en">3</a></li>
          <li class="s4"><a href="serie/Breaking-Bad/4/en">4</a></li>
          <li class="s5"><a href="serie/Breaking-Bad/5/en">5</a></li>
        </ul>
      </div>
    </div>
  </div>
  <table class="episodes">
    <tbody>
      <tr>
        <td><a href="serie/Breaking-Bad/1/1-Pilot/en" title="Pilot">1</a></td>
        <td>
          <a href="serie/Breaking-Bad/1/1-Pilot/en" title="Pilot">
            <strong>Pilot</strong>
          </a>
        </td>
        <td>
          <a href="serie/Breaking-Bad/1/1-Pilot/en/VOE">VOE</a>
        </td>
      </tr>
      <tr>
        <td><a href="serie/Breaking-Bad/1/2-Cats-in-the-Bag/en">2</a></td>
        <td>
          <a href="serie/Breaking-Bad/1/2-Cats-in-the-Bag/en">
            <strong>Cat's in the Bag</strong>
          </a>
        </td>
        <td>
          <a href="serie/Breaking-Bad/1/2-Cats-in-the-Bag/en/VOE">VOE</a>
        </td>
      </tr>
      <tr>
        <td><a href="serie/Breaking-Bad/1/3-Bag-in-the-River/en">3</a></td>
        <td>
          <a href="serie/Breaking-Bad/1/3-Bag-in-the-River/en">
            <strong>...and the Bag's in the River</strong>
          </a>
        </td>
        <td>
          <a href="serie/Breaking-Bad/1/3-Bag-in-the-River/en/VOE">VOE</a>
        </td>
      </tr>
    </tbody>
  </table>
</section>
"""

EMPTY_LISTING_HTML = """\
<div class="genre">
  <span><strong>Abenteuer</strong></span>
  <ul></ul>
</div>
"""

MINIMAL_DETAIL_HTML = """\
<section class="serie">
  <div id="sp_left">
    <h2>Naruto</h2>
    <p>A young ninja seeks recognition.</p>
  </div>
</section>
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_response(text: str) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.text = text
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# _SeriesListParser tests
# ---------------------------------------------------------------------------


class TestSeriesListParser:
    """Tests for _SeriesListParser."""

    def test_parses_series_from_multiple_genres(self, bs_mod):
        parser = bs_mod._SeriesListParser()
        parser.feed(LISTING_HTML)

        # 5 total entries (Breaking Bad appears twice)
        assert len(parser.series) == 5

    def test_first_result_fields(self, bs_mod):
        parser = bs_mod._SeriesListParser()
        parser.feed(LISTING_HTML)

        r = parser.series[0]
        assert r["title"] == "Breaking Bad"
        assert r["slug"] == "Breaking-Bad"
        assert r["genre"] == "Drama"

    def test_anime_genre(self, bs_mod):
        parser = bs_mod._SeriesListParser()
        parser.feed(LISTING_HTML)

        r = parser.series[2]
        assert r["title"] == "Naruto"
        assert r["slug"] == "Naruto"
        assert r["genre"] == "Anime"

    def test_pipe_separated_title(self, bs_mod):
        parser = bs_mod._SeriesListParser()
        parser.feed(LISTING_HTML)

        r = parser.series[3]
        assert r["title"] == "Attack on Titan | Shingeki no Kyojin"
        assert r["slug"] == "Attack-on-Titan"

    def test_empty_listing(self, bs_mod):
        parser = bs_mod._SeriesListParser()
        parser.feed(EMPTY_LISTING_HTML)

        assert len(parser.series) == 0

    def test_no_html(self, bs_mod):
        parser = bs_mod._SeriesListParser()
        parser.feed("")

        assert len(parser.series) == 0

    def test_duplicate_across_genres(self, bs_mod):
        parser = bs_mod._SeriesListParser()
        parser.feed(LISTING_HTML)

        slugs = [s["slug"] for s in parser.series]
        assert slugs.count("Breaking-Bad") == 2  # Appears in Drama and Krimi


# ---------------------------------------------------------------------------
# _SeriesDetailParser tests
# ---------------------------------------------------------------------------


class TestSeriesDetailParser:
    """Tests for _SeriesDetailParser."""

    def test_title(self, bs_mod):
        parser = bs_mod._SeriesDetailParser()
        parser.feed(DETAIL_HTML)

        assert parser.title == "Breaking Bad"

    def test_description(self, bs_mod):
        parser = bs_mod._SeriesDetailParser()
        parser.feed(DETAIL_HTML)

        assert "Walter White" in parser.description
        assert "methamphetamine" in parser.description

    def test_genres(self, bs_mod):
        parser = bs_mod._SeriesDetailParser()
        parser.feed(DETAIL_HTML)

        assert parser.genres == ["Drama", "Krimi", "Thriller"]

    def test_year(self, bs_mod):
        parser = bs_mod._SeriesDetailParser()
        parser.feed(DETAIL_HTML)

        assert parser.year == "2008 - 2013"

    def test_season_count(self, bs_mod):
        parser = bs_mod._SeriesDetailParser()
        parser.feed(DETAIL_HTML)

        assert parser.season_count == 6  # Specials + 5 seasons

    def test_episode_count(self, bs_mod):
        parser = bs_mod._SeriesDetailParser()
        parser.feed(DETAIL_HTML)

        assert parser.episode_count == 3

    def test_minimal_detail(self, bs_mod):
        parser = bs_mod._SeriesDetailParser()
        parser.feed(MINIMAL_DETAIL_HTML)

        assert parser.title == "Naruto"
        assert "young ninja" in parser.description
        assert parser.genres == []
        assert parser.year == ""
        assert parser.season_count == 0
        assert parser.episode_count == 0

    def test_empty_page(self, bs_mod):
        parser = bs_mod._SeriesDetailParser()
        parser.feed("")

        assert parser.title == ""
        assert parser.description == ""
        assert parser.genres == []
        assert parser.year == ""
        assert parser.season_count == 0
        assert parser.episode_count == 0


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for helper functions."""

    def test_genre_to_category_anime(self, bs_mod):
        assert bs_mod._genre_to_category("Anime") == 5070

    def test_genre_to_category_anime_subgenre(self, bs_mod):
        assert bs_mod._genre_to_category("Anime-Isekai") == 5070

    def test_genre_to_category_documentary(self, bs_mod):
        assert bs_mod._genre_to_category("Dokumentation") == 5080

    def test_genre_to_category_sport(self, bs_mod):
        assert bs_mod._genre_to_category("Sport") == 5060

    def test_genre_to_category_default(self, bs_mod):
        assert bs_mod._genre_to_category("Drama") == 5000

    def test_genre_to_category_unknown(self, bs_mod):
        assert bs_mod._genre_to_category("SomeNewGenre") == 5000

    def test_match_query_single_word(self, bs_mod):
        assert bs_mod._match_query("breaking", "Breaking Bad") is True

    def test_match_query_multiple_words(self, bs_mod):
        assert bs_mod._match_query("breaking bad", "Breaking Bad") is True

    def test_match_query_case_insensitive(self, bs_mod):
        assert bs_mod._match_query("BREAKING BAD", "Breaking Bad") is True

    def test_match_query_no_match(self, bs_mod):
        assert bs_mod._match_query("naruto", "Breaking Bad") is False

    def test_match_query_partial_word(self, bs_mod):
        assert bs_mod._match_query("break", "Breaking Bad") is True

    def test_match_query_pipe_title(self, bs_mod):
        assert (
            bs_mod._match_query(
                "attack titan", "Attack on Titan | Shingeki no Kyojin"
            )
            is True
        )


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    """Tests for plugin class attributes."""

    def test_name(self, bs_mod):
        assert bs_mod.plugin.name == "burningseries"

    def test_version(self, bs_mod):
        assert bs_mod.plugin.version == "1.0.0"

    def test_mode(self, bs_mod):
        assert bs_mod.plugin.mode == "httpx"


# ---------------------------------------------------------------------------
# Plugin search tests (mocked HTTP)
# ---------------------------------------------------------------------------


class TestPluginSearch:
    """Tests for BurningSeriesPlugin.search() with mocked HTTP."""

    @pytest.fixture()
    def mock_client(self):
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture()
    def _plugin(self, bs_mod, mock_client):
        p = bs_mod.BurningSeriesPlugin()
        p._client = mock_client
        p._domain_verified = True
        p.base_url = "https://bs.to"
        return p

    @pytest.mark.asyncio
    async def test_search_returns_results(self, _plugin, mock_client):
        listing_resp = _make_response(LISTING_HTML)
        detail_resp = _make_response(DETAIL_HTML)

        async def mock_get(url, **kwargs):
            if "andere-serien" in str(url):
                return listing_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await _plugin.search("breaking bad")

        assert len(results) == 1  # Deduplicated
        assert "Breaking Bad" in results[0].title
        assert "2008" in results[0].title
        assert results[0].download_link == "https://bs.to/serie/Breaking-Bad"
        assert results[0].category == 5000

    @pytest.mark.asyncio
    async def test_search_empty_query(self, _plugin):
        results = await _plugin.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_rejected_category_movies(self, _plugin):
        # Movies category (2000) not supported
        results = await _plugin.search("test", category=2000)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_rejected_category_music(self, _plugin):
        # Music category (3000) not supported
        results = await _plugin.search("test", category=3000)
        assert results == []

    @pytest.mark.asyncio
    async def test_search_tv_category_accepted(self, _plugin, mock_client):
        listing_resp = _make_response(LISTING_HTML)
        detail_resp = _make_response(DETAIL_HTML)

        async def mock_get(url, **kwargs):
            if "andere-serien" in str(url):
                return listing_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await _plugin.search("breaking", category=5000)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_anime_filter(self, _plugin, mock_client):
        listing_resp = _make_response(LISTING_HTML)
        detail_resp = _make_response(MINIMAL_DETAIL_HTML)

        async def mock_get(url, **kwargs):
            if "andere-serien" in str(url):
                return listing_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        # Search for Naruto with anime category filter
        results = await _plugin.search("naruto", category=5070)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_no_results(self, _plugin, mock_client):
        listing_resp = _make_response(LISTING_HTML)
        mock_client.get = AsyncMock(return_value=listing_resp)

        results = await _plugin.search("xyznonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_http_error_listing(self, _plugin, mock_client):
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )

        results = await _plugin.search("test")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_caches_listing(self, _plugin, mock_client):
        listing_resp = _make_response(LISTING_HTML)
        detail_resp = _make_response(DETAIL_HTML)

        async def mock_get(url, **kwargs):
            if "andere-serien" in str(url):
                return listing_resp
            return detail_resp

        mock_client.get = AsyncMock(side_effect=mock_get)

        # First search loads listing
        await _plugin.search("breaking bad")
        # Second search should use cache
        await _plugin.search("better call")

        # The listing endpoint should only be called once
        listing_calls = [
            c
            for c in mock_client.get.call_args_list
            if "andere-serien" in str(c)
        ]
        assert len(listing_calls) == 1

    @pytest.mark.asyncio
    async def test_detail_page_failure(self, _plugin, mock_client):
        listing_resp = _make_response(LISTING_HTML)
        empty_detail = _make_response("")

        async def mock_get(url, **kwargs):
            if "andere-serien" in str(url):
                return listing_resp
            return empty_detail

        mock_client.get = AsyncMock(side_effect=mock_get)

        results = await _plugin.search("breaking bad")

        assert len(results) == 1
        # Fallback to listing title (no year from detail)
        assert results[0].title == "Breaking Bad"
        assert results[0].published_date is None


# ---------------------------------------------------------------------------
# Domain verification tests
# ---------------------------------------------------------------------------


class TestDomainVerification:
    """Tests for domain fallback logic."""

    @pytest.mark.asyncio
    async def test_first_domain_works(self, bs_mod):
        p = bs_mod.BurningSeriesPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        mock_client.head = AsyncMock(return_value=resp)
        p._client = mock_client

        await p._verify_domain()

        assert p._domain_verified is True
        assert "bs.to" in p.base_url

    @pytest.mark.asyncio
    async def test_fallback_on_connect_error(self, bs_mod):
        p = bs_mod.BurningSeriesPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        call_count = 0

        async def mock_head(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("Connection failed")
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            return resp

        mock_client.head = AsyncMock(side_effect=mock_head)
        p._client = mock_client

        await p._verify_domain()

        assert p._domain_verified is True
        assert call_count == 2
        assert "burning-series.io" in p.base_url

    @pytest.mark.asyncio
    async def test_all_domains_fail(self, bs_mod):
        p = bs_mod.BurningSeriesPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(
            side_effect=httpx.ConnectError("Connection failed")
        )
        p._client = mock_client

        await p._verify_domain()

        assert p._domain_verified is True
        assert bs_mod._DOMAINS[0] in p.base_url

    @pytest.mark.asyncio
    async def test_skips_if_already_verified(self, bs_mod):
        p = bs_mod.BurningSeriesPlugin()
        p._domain_verified = True
        p.base_url = "https://custom.domain"
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        p._client = mock_client

        await p._verify_domain()

        mock_client.head.assert_not_called()
        assert p.base_url == "https://custom.domain"


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------


class TestCleanup:
    """Tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self, bs_mod):
        p = bs_mod.BurningSeriesPlugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        p._client = mock_client

        await p.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert p._client is None

    @pytest.mark.asyncio
    async def test_cleanup_without_client(self, bs_mod):
        p = bs_mod.BurningSeriesPlugin()

        await p.cleanup()  # Should not raise
