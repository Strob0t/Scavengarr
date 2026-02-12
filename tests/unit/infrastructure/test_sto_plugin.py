"""Tests for the s.to (SerienStream) Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "sto.py"


def _load_module() -> ModuleType:
    """Load sto.py plugin via importlib (same as plugin loader)."""
    spec = importlib.util.spec_from_file_location("sto_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once at module level for parser tests
_mod = _load_module()
_StoPlugin = _mod.StoPlugin
_SearchSeriesParser = _mod._SearchSeriesParser
_SeriesDetailParser = _mod._SeriesDetailParser
_EpisodeHosterParser = _mod._EpisodeHosterParser
_DOMAINS = _mod._DOMAINS
_GENRE_CATEGORY_MAP = _mod._GENRE_CATEGORY_MAP
_genre_to_torznab = _mod._genre_to_torznab
_is_tv_category = _mod._is_tv_category
_determine_category = _mod._determine_category


def _make_plugin() -> object:
    """Create StoPlugin instance."""
    return _StoPlugin()


def _mock_response(
    text: str = "",
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

_SEARCH_HTML = """\
<html><body>
<h2>Serien</h2>
<div class="row g-3">
  <a href="/serie/stranger-things">
    <div class="card">
      <img src="/poster.jpg" alt="poster">
      <h6>Stranger Things</h6>
    </div>
  </a>
  <a href="/serie/dark">
    <div class="card">
      <img src="/poster2.jpg" alt="poster">
      <h6>Dark</h6>
    </div>
  </a>
</div>
<h2>Episoden</h2>
<div class="row g-3">
  <a href="/serie/stranger-things/staffel-1/episode-1">
    <h6>Episode Result</h6>
  </a>
</div>
</body></html>
"""

_SERIES_DETAIL_HTML = """\
<html><body>
<h1>Stranger Things</h1>
<div class="info">
  <strong>Genre:</strong>
  <a href="/genre/drama">Drama</a>,
  <a href="/genre/science-fiction">Science Fiction</a>,
  <a href="/genre/horror">Horror</a>
</div>
<ul class="nav">
  <a href="/serie/stranger-things/staffel-1">Staffel 1</a>
  <a href="/serie/stranger-things/staffel-2">Staffel 2</a>
  <a href="/serie/stranger-things/staffel-3">Staffel 3</a>
  <a href="/serie/stranger-things/staffel-4">Staffel 4</a>
</ul>
<table>
  <tr>
    <th>1</th>
    <td>
      <strong>Die Verschwundene</strong>
      <div>The Vanishing of Will Byers</div>
    </td>
    <td><img alt="VOE"><img alt="Vidoza"></td>
    <td><img src="/flag-de.png" alt="flag"></td>
  </tr>
  <tr>
    <th>2</th>
    <td>
      <strong>Die Verrückte auf der Maple Street</strong>
    </td>
    <td><img alt="VOE"></td>
    <td><img src="/flag-de.png" alt="flag"></td>
  </tr>
</table>
</body></html>
"""

_EPISODE_HTML = """\
<html><body>
<h2>Stranger Things - Staffel 1 Episode 1</h2>
<h5>Deutsch</h5>
<button class="link-box btn btn-dark w-100 text-start gap-2"
        data-play-url="/r?t=abc123"
        data-provider-name="VOE"
        data-language-label="Deutsch"
        data-language-id="1"
        data-link-id="1001">
  <span>VOE</span>
</button>
<button class="link-box btn btn-dark w-100 text-start gap-2"
        data-play-url="/r?t=def456"
        data-provider-name="Vidoza"
        data-language-label="Deutsch"
        data-language-id="1"
        data-link-id="1002">
  <span>Vidoza</span>
</button>
<h5>Englisch</h5>
<button class="link-box btn btn-dark w-100 text-start gap-2"
        data-play-url="/r?t=ghi789"
        data-provider-name="VOE"
        data-language-label="Englisch"
        data-language-id="2"
        data-link-id="1003">
  <span>VOE</span>
</button>
</body></html>
"""

_EMPTY_SEARCH_HTML = """\
<html><body>
<h2>Serien</h2>
<div class="row g-3">
  <p>Keine Ergebnisse gefunden.</p>
</div>
</body></html>
"""


# ---------------------------------------------------------------------------
# Plugin attributes
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name_attribute(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "sto"

    def test_version_attribute(self) -> None:
        plugin = _make_plugin()
        assert plugin.version == "1.0.0"

    def test_mode_attribute(self) -> None:
        plugin = _make_plugin()
        assert plugin.mode == "httpx"

    def test_domains_list(self) -> None:
        assert "s.to" in _DOMAINS
        assert "serienstream.to" in _DOMAINS
        assert "186.2.175.5" in _DOMAINS
        assert _DOMAINS[0] == "s.to"


# ---------------------------------------------------------------------------
# Domain verification
# ---------------------------------------------------------------------------


class TestDomainVerification:
    @pytest.mark.asyncio
    async def test_first_domain_reachable(self) -> None:
        plugin = _make_plugin()

        head_resp = MagicMock()
        head_resp.status_code = 200

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=head_resp)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "s.to" in plugin.base_url

    @pytest.mark.asyncio
    async def test_fallback_to_second_domain(self) -> None:
        plugin = _make_plugin()

        fail_resp = MagicMock()
        fail_resp.status_code = 503

        ok_resp = MagicMock()
        ok_resp.status_code = 200

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=[fail_resp, ok_resp])
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "serienstream.to" in plugin.base_url

    @pytest.mark.asyncio
    async def test_all_domains_fail(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "s.to" in plugin.base_url  # Fallback to primary

    @pytest.mark.asyncio
    async def test_skips_if_already_verified(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://custom.domain"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin._verify_domain()

        mock_client.head.assert_not_called()
        assert plugin.base_url == "https://custom.domain"


# ---------------------------------------------------------------------------
# _SearchSeriesParser
# ---------------------------------------------------------------------------


class TestSearchSeriesParser:
    def test_parses_series_results(self) -> None:
        parser = _SearchSeriesParser("https://s.to")
        parser.feed(_SEARCH_HTML)

        # Should find 2 series + 1 episode link (episode also has /serie/ in href)
        series = [r for r in parser.results if "staffel" not in r["url"]]
        assert len(series) == 2

        assert series[0]["title"] == "Stranger Things"
        assert series[0]["slug"] == "stranger-things"
        assert "s.to/serie/stranger-things" in series[0]["url"]

        assert series[1]["title"] == "Dark"
        assert series[1]["slug"] == "dark"

    def test_empty_search(self) -> None:
        parser = _SearchSeriesParser("https://s.to")
        parser.feed(_EMPTY_SEARCH_HTML)

        assert len(parser.results) == 0

    def test_no_html(self) -> None:
        parser = _SearchSeriesParser("https://s.to")
        parser.feed("")

        assert len(parser.results) == 0

    def test_base_url_in_results(self) -> None:
        parser = _SearchSeriesParser("https://serienstream.to")
        parser.feed(_SEARCH_HTML)

        for r in parser.results:
            assert r["url"].startswith("https://serienstream.to")


# ---------------------------------------------------------------------------
# _SeriesDetailParser
# ---------------------------------------------------------------------------


class TestSeriesDetailParser:
    def test_parses_title(self) -> None:
        parser = _SeriesDetailParser("https://s.to")
        parser.feed(_SERIES_DETAIL_HTML)

        assert parser.title == "Stranger Things"

    def test_parses_genres(self) -> None:
        parser = _SeriesDetailParser("https://s.to")
        parser.feed(_SERIES_DETAIL_HTML)

        assert "Drama" in parser.genres
        assert "Science Fiction" in parser.genres
        assert "Horror" in parser.genres

    def test_parses_seasons(self) -> None:
        parser = _SeriesDetailParser("https://s.to")
        parser.feed(_SERIES_DETAIL_HTML)

        assert parser.seasons == [1, 2, 3, 4]

    def test_parses_episodes(self) -> None:
        parser = _SeriesDetailParser("https://s.to")
        parser.feed(_SERIES_DETAIL_HTML)

        assert len(parser.episodes) == 2

        ep1 = parser.episodes[0]
        assert ep1["number"] == "1"
        assert ep1["de_title"] == "Die Verschwundene"
        assert "VOE" in ep1["hosters"]
        assert "Vidoza" in ep1["hosters"]

        ep2 = parser.episodes[1]
        assert ep2["number"] == "2"
        assert "Die Verrückte" in ep2["de_title"]

    def test_empty_html(self) -> None:
        parser = _SeriesDetailParser("https://s.to")
        parser.feed("<html><body></body></html>")

        assert parser.title == ""
        assert parser.genres == []
        assert parser.seasons == []
        assert parser.episodes == []


# ---------------------------------------------------------------------------
# _EpisodeHosterParser
# ---------------------------------------------------------------------------


class TestEpisodeHosterParser:
    def test_parses_hoster_buttons(self) -> None:
        parser = _EpisodeHosterParser()
        parser.feed(_EPISODE_HTML)

        assert len(parser.hosters) == 3

        voe_de = parser.hosters[0]
        assert voe_de["play_url"] == "/r?t=abc123"
        assert voe_de["provider"] == "VOE"
        assert voe_de["language"] == "Deutsch"

        vidoza = parser.hosters[1]
        assert vidoza["play_url"] == "/r?t=def456"
        assert vidoza["provider"] == "Vidoza"
        assert vidoza["language"] == "Deutsch"

        voe_en = parser.hosters[2]
        assert voe_en["play_url"] == "/r?t=ghi789"
        assert voe_en["provider"] == "VOE"
        assert voe_en["language"] == "Englisch"

    def test_empty_html(self) -> None:
        parser = _EpisodeHosterParser()
        parser.feed("<html><body></body></html>")

        assert len(parser.hosters) == 0

    def test_buttons_without_data_attrs(self) -> None:
        html = '<button class="btn">Click me</button>'
        parser = _EpisodeHosterParser()
        parser.feed(html)

        assert len(parser.hosters) == 0


# ---------------------------------------------------------------------------
# Genre → Torznab category mapping
# ---------------------------------------------------------------------------


class TestCategoryMapping:
    def test_anime_mapping(self) -> None:
        assert _genre_to_torznab("Anime") == 5070
        assert _genre_to_torznab("anime") == 5070

    def test_drama_mapping(self) -> None:
        assert _genre_to_torznab("Drama") == 5030

    def test_horror_mapping(self) -> None:
        assert _genre_to_torznab("Horror") == 5040

    def test_science_fiction_mapping(self) -> None:
        assert _genre_to_torznab("Science Fiction") == 5030
        assert _genre_to_torznab("science-fiction") == 5030

    def test_documentary_mapping(self) -> None:
        assert _genre_to_torznab("Dokumentation") == 5080

    def test_fantasy_mapping(self) -> None:
        assert _genre_to_torznab("Fantasy") == 5050

    def test_unknown_genre_defaults_to_5000(self) -> None:
        assert _genre_to_torznab("Krimi") == 5000
        assert _genre_to_torznab("Western") == 5000
        assert _genre_to_torznab("Romantik") == 5000

    def test_zeichentrick_mapping(self) -> None:
        assert _genre_to_torznab("Zeichentrick") == 5070


# ---------------------------------------------------------------------------
# _is_tv_category & _determine_category
# ---------------------------------------------------------------------------


class TestIsTvCategory:
    def test_tv_base_category(self) -> None:
        assert _is_tv_category(5000) is True

    def test_tv_sub_categories(self) -> None:
        assert _is_tv_category(5030) is True
        assert _is_tv_category(5040) is True
        assert _is_tv_category(5070) is True
        assert _is_tv_category(5080) is True
        assert _is_tv_category(5999) is True

    def test_movie_category_rejected(self) -> None:
        assert _is_tv_category(2000) is False
        assert _is_tv_category(2030) is False
        assert _is_tv_category(2040) is False

    def test_other_categories_rejected(self) -> None:
        assert _is_tv_category(3000) is False  # Music
        assert _is_tv_category(1000) is False  # Console
        assert _is_tv_category(7000) is False  # Books


class TestDetermineCategory:
    def test_tv_category_passed_through(self) -> None:
        assert _determine_category(["Drama"], category=5070) == 5070

    def test_movie_category_ignored(self) -> None:
        """Non-TV caller category is ignored; genre mapping used instead."""
        result = _determine_category(["Drama"], category=2000)
        assert result == 5030  # Drama → 5030

    def test_movie_category_no_genre_defaults_5000(self) -> None:
        """Non-TV category with unknown genres defaults to 5000."""
        result = _determine_category(["Krimi"], category=2000)
        assert result == 5000

    def test_no_category_uses_genres(self) -> None:
        assert _determine_category(["Horror", "Drama"], category=None) == 5040

    def test_no_category_no_mapped_genre_defaults(self) -> None:
        assert _determine_category(["Romantik"], category=None) == 5000


# ---------------------------------------------------------------------------
# Hoster URL resolution
# ---------------------------------------------------------------------------


class TestHosterResolution:
    @pytest.mark.asyncio
    async def test_resolves_redirect(self) -> None:
        plugin = _make_plugin()

        redirect_resp = MagicMock()
        redirect_resp.headers = {"location": "https://voe.sx/e/abc123"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=redirect_resp)
        plugin._client = mock_client
        plugin.base_url = "https://s.to"

        result = await plugin._resolve_hoster_url("/r?t=token123")

        assert result == "https://voe.sx/e/abc123"

    @pytest.mark.asyncio
    async def test_returns_original_on_failure(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=httpx.ConnectError("fail"))
        plugin._client = mock_client
        plugin.base_url = "https://s.to"

        result = await plugin._resolve_hoster_url("/r?t=token123")

        assert result == "https://s.to/r?t=token123"

    @pytest.mark.asyncio
    async def test_returns_original_when_no_location(self) -> None:
        plugin = _make_plugin()

        no_redirect_resp = MagicMock()
        no_redirect_resp.headers = {}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=no_redirect_resp)
        plugin._client = mock_client
        plugin.base_url = "https://s.to"

        result = await plugin._resolve_hoster_url("/r?t=token123")

        assert result == "https://s.to/r?t=token123"


# ---------------------------------------------------------------------------
# search() integration
# ---------------------------------------------------------------------------


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://s.to"

        # Search returns 3 links (2 series + 1 episode link with /serie/ prefix)
        # Each triggers a detail fetch; only "stranger-things" has season data.
        search_resp = _mock_response(text=_SEARCH_HTML)
        detail_resp = _mock_response(text=_SERIES_DETAIL_HTML)
        empty_detail = _mock_response(text="<html><body></body></html>")
        episode_resp = _mock_response(text=_EPISODE_HTML)
        redirect_resp = MagicMock()
        redirect_resp.headers = {"location": "https://voe.sx/e/resolved"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)

        # search (1) → detail×3 → episode×2 (for stranger-things season 1)
        mock_client.get = AsyncMock(
            side_effect=[
                search_resp,  # search page
                detail_resp,  # stranger-things detail
                empty_detail,  # dark detail (no seasons → skipped)
                empty_detail,  # episode link detail (no seasons → skipped)
                episode_resp,  # stranger-things S01E01
                episode_resp,  # stranger-things S01E02
            ]
        )
        mock_client.head = AsyncMock(return_value=redirect_resp)
        plugin._client = mock_client

        results = await plugin.search("stranger things")

        assert len(results) > 0
        for r in results:
            assert "Stranger Things" in r.title
            assert r.category >= 5000
            assert r.download_link
            assert r.download_links

    @pytest.mark.asyncio
    async def test_search_empty_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://s.to"

        search_resp = _mock_response(text=_EMPTY_SEARCH_HTML)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=search_resp)
        plugin._client = mock_client

        results = await plugin.search("nonexistent_show_xyz")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_with_category(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://s.to"

        search_resp = _mock_response(text=_SEARCH_HTML)
        detail_resp = _mock_response(text=_SERIES_DETAIL_HTML)
        empty_detail = _mock_response(text="<html><body></body></html>")
        episode_resp = _mock_response(text=_EPISODE_HTML)
        redirect_resp = MagicMock()
        redirect_resp.headers = {"location": "https://voe.sx/e/resolved"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            side_effect=[
                search_resp,
                detail_resp,
                empty_detail,
                empty_detail,
                episode_resp,
                episode_resp,
            ]
        )
        mock_client.head = AsyncMock(return_value=redirect_resp)
        plugin._client = mock_client

        results = await plugin.search("stranger things", category=5070)

        assert len(results) > 0
        for r in results:
            assert r.category == 5070

    @pytest.mark.asyncio
    async def test_search_rejects_movie_category(self) -> None:
        """s.to is TV-only: movie category (2000) should return empty."""
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://s.to"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        results = await plugin.search("batman", category=2000)

        assert results == []
        # Should never even perform a search request
        mock_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_network_failure(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://s.to"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        plugin._client = mock_client

        results = await plugin.search("test")

        assert results == []


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_closes_client(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert plugin._client is None

    @pytest.mark.asyncio
    async def test_cleanup_noop_when_no_client(self) -> None:
        plugin = _make_plugin()
        assert plugin._client is None

        await plugin.cleanup()  # Should not raise
