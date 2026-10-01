"""Tests for the s.to (SerienStream) Python plugin (httpx-based)."""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from scavengarr.domain.ports.browser_fetcher import ClickThrough

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
_DOMAINS = _StoPlugin._domains
_GENRE_CATEGORY_MAP = _mod._GENRE_CATEGORY_MAP
_genre_to_torznab = _mod._genre_to_torznab
_determine_category = _mod._determine_category
_relevant_series = _mod._relevant_series


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
    resp.is_redirect = status_code in (301, 302, 303, 307, 308)
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


# ---------------------------------------------------------------------------
# Sample HTML fixtures
# ---------------------------------------------------------------------------

_SERIES_CARD_2026 = """\
<div class="col-6 col-md-4 col-lg-2">
  <a href="/serie/{slug}" class="text-decoration-none">
    <div class="card cover-card h-100 border-0 shadow-sm">
      <a href="/serie/{slug}" class="d-block show-cover">
        <picture><img src="/media/images/channel/desktop/{slug}.jpg"
             alt="{title}" class="img-fluid w-100"></picture>
      </a>
      <div class="card-body py-2 p-1">
        <h6 class="show-title mb-0 small" title="{title}">{title}</h6>
      </div>
    </div>
  </a>
</div>
"""

_EPISODE_HITS_2026 = """\
<h2 class="search-section__title fw-bold">Episoden</h2>
<div class="search-section search-section--episodes">
<ul class="p-0 list-unstyled">
<li class="mb-4">
  <a href="/serie/it-s-always-sunny-in-philadelphia">
    <h6 class="small">
      It&#039;s Always Sunny in Philadelphia
      <span class="text-primary float-end">S18 · E9</span>
    </h6>
    <p class="title underline-styled mb-1">A Dark Day for Baseball</p>
  </a>
</li>
</ul>
</div>
"""


def _search_page(*cards: tuple[str, str]) -> str:
    """Search page in the live layout: series cards, then episode hits,
    the whole results block rendered twice (two layouts)."""
    results = (
        '<div class="results-group" data-group="all">\n'
        '<h2 class="h4 text-white mb-3 fw-bold">Serien</h2>\n'
        '<div class="row g-3">\n'
        + "".join(_SERIES_CARD_2026.format(slug=s, title=t) for s, t in cards)
        + "</div>\n"
        + _EPISODE_HITS_2026
        + "</div>\n"
    )
    return f"<html><body>{results}{results}</body></html>"


_SEARCH_HTML = _search_page(("stranger-things", "Stranger Things"), ("dark", "Dark"))
_SEARCH_HTML_2026 = _search_page(("dark-greece", "Dark Greece"), ("dark", "Dark"))

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
        # s.to is gone (NXDOMAIN, dead in JDownloader's SerienStreamTo)
        assert _DOMAINS == ["serienstream.to", "186.2.175.5"]


# ---------------------------------------------------------------------------
# Domain verification
# ---------------------------------------------------------------------------


class TestDomainVerification:
    @pytest.mark.asyncio
    async def test_first_domain_reachable(self) -> None:
        plugin = _make_plugin()

        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.url = httpx.URL("https://serienstream.to/")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(return_value=head_resp)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "serienstream.to" in plugin.base_url

    @pytest.mark.asyncio
    async def test_fallback_to_second_domain(self) -> None:
        plugin = _make_plugin()

        fail_resp = MagicMock()
        fail_resp.status_code = 503

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.url = httpx.URL("https://186.2.175.5/")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=[fail_resp, ok_resp])
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "186.2.175.5" in plugin.base_url

    @pytest.mark.asyncio
    async def test_all_domains_fail(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "serienstream.to" in plugin.base_url  # Fallback to primary

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
    def test_parses_the_current_series_cards(self) -> None:
        # Live markup (2026-10-01): a card nests one /serie/ anchor in
        # another, the title follows the inner one; the page renders its
        # results twice (two layouts). The parser found no card at all and
        # took the episode hits below ("Dark" -> It's Always Sunny ...)
        parser = _SearchSeriesParser("https://serienstream.to")
        parser.feed(_SEARCH_HTML_2026)

        assert [(r["title"], r["slug"]) for r in parser.results] == [
            ("Dark Greece", "dark-greece"),
            ("Dark", "dark"),
        ]
        assert parser.results[1]["url"] == "https://serienstream.to/serie/dark"

    def test_parses_series_results(self) -> None:
        parser = _SearchSeriesParser("https://s.to")
        parser.feed(_SEARCH_HTML)

        series = parser.results
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

    def test_skips_the_link_to_the_official_provider(self) -> None:
        # "Anbieter" links to the streaming service that owns the series
        # (no embed; its link-out answered 410 for Breaking Bad), no hoster
        html = """\
<button class="link-box" data-link-id="1" data-play-url="/r?t=voe"
        data-auto-embed="1" data-provider-name="VOE"
        data-language-label="Englisch" data-language-id="2">VOE</button>
<button class="link-box" data-link-id="2" data-play-url="/r?t=official"
        data-auto-embed="0" data-provider-name="Provider"
        data-language-label="Englisch" data-language-id="2">
  <span class="text-white ms-1">Anbieter</span>
</button>
"""
        parser = _EpisodeHosterParser()
        parser.feed(html)

        assert [h["provider"] for h in parser.hosters] == ["VOE"]

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
    """Genres only tell anime and documentaries apart; they used to be mapped
    to quality subcategories (drama 5030 TV/SD, horror 5040 TV/HD)."""

    def test_anime_mapping(self) -> None:
        assert _genre_to_torznab("Anime") == 5070
        assert _genre_to_torznab("anime") == 5070

    def test_documentary_mapping(self) -> None:
        assert _genre_to_torznab("Dokumentation") == 5080

    def test_other_genres_are_tv(self) -> None:
        for genre in ("Drama", "Horror", "Science Fiction", "Fantasy", "Krimi"):
            assert _genre_to_torznab(genre) == 5000, genre


# ---------------------------------------------------------------------------
# _determine_category
# ---------------------------------------------------------------------------


class TestDetermineCategory:
    def test_from_genres(self) -> None:
        assert _determine_category(["Drama", "Anime"]) == 5070
        assert _determine_category(["Dokumentation"]) == 5080
        assert _determine_category(["Horror", "Drama"]) == 5000
        assert _determine_category([]) == 5000


# ---------------------------------------------------------------------------
# Hoster URL resolution
# ---------------------------------------------------------------------------


class TestHosterResolution:
    @pytest.mark.asyncio
    async def test_resolves_redirect(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(
            return_value=_mock_response(
                status_code=302, headers={"location": "https://voe.sx/e/abc123"}
            )
        )
        plugin._client = mock_client
        plugin.base_url = "https://s.to"

        result = await plugin._resolve_hoster_url(
            "/r?t=token123", referer="https://s.to/serie/x/staffel-1/episode-2"
        )

        assert result == "https://voe.sx/e/abc123"
        assert mock_client.get.await_args.kwargs["follow_redirects"] is False

    @pytest.mark.asyncio
    async def test_sends_episode_page_as_referer(self) -> None:
        # Without Referer (or session cookie) s.to answers /r?t= with a page
        # that only works inside its player iframe, not with the redirect
        plugin = _make_plugin()
        episode = "https://s.to/serie/x/staffel-1/episode-2"

        async def _get(url: str, **kw: object) -> object:
            headers = kw.get("headers") or {}
            if headers.get("Referer") == episode:  # type: ignore[union-attr]
                return _mock_response(
                    status_code=302, headers={"location": "https://voe.sx/e/abc"}
                )
            return _mock_response(text="<script>window.parent.postMessage</script>")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=_get)
        plugin._client = mock_client
        plugin.base_url = "https://s.to"

        result = await plugin._resolve_hoster_url("/r?t=token123", referer=episode)

        assert result == "https://voe.sx/e/abc"

    @pytest.mark.asyncio
    async def test_returns_original_on_failure(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("fail"))
        plugin._client = mock_client
        plugin.base_url = "https://s.to"

        result = await plugin._resolve_hoster_url("/r?t=token123", referer="")

        assert result == "https://s.to/r?t=token123"

    @pytest.mark.asyncio
    async def test_returns_original_when_no_location(self) -> None:
        plugin = _make_plugin()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=_mock_response(text="<html></html>"))
        plugin._client = mock_client
        plugin.base_url = "https://s.to"

        result = await plugin._resolve_hoster_url("/r?t=token123", referer="")

        assert result == "https://s.to/r?t=token123"


# ---------------------------------------------------------------------------
# search() integration
# ---------------------------------------------------------------------------


def _routed_client(active: dict[str, int] | None = None) -> AsyncMock:
    """Mock client answering by URL (the plugin fetches in parallel)."""

    async def _get(url: str, **kw: object) -> object:
        url = str(url)
        if "/r?t=" in url:
            referer = (kw.get("headers") or {}).get("Referer", "")  # type: ignore[union-attr]
            if "/episode-" not in referer:
                # s.to only redirects link-outs opened from their episode page
                return _mock_response(text="<script>window.parent</script>")
            if active is not None:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
                await asyncio.sleep(0.01)
                active["now"] -= 1
            return _mock_response(
                status_code=302, headers={"location": "https://voe.sx/e/resolved"}
            )
        if "/suche" in url:
            params = kw.get("params") or {}
            first = int(params.get("page", 1)) == 1  # type: ignore[union-attr]
            return _mock_response(text=_SEARCH_HTML if first else _EMPTY_SEARCH_HTML)
        if "/episode-" in url:
            return _mock_response(text=_EPISODE_HTML)
        if url.rstrip("/").endswith("/serie/stranger-things") or "/staffel-" in url:
            return _mock_response(text=_SERIES_DETAIL_HTML)
        return _mock_response(text="<html><body></body></html>")

    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=_get)
    return client


class TestSeasonsAndParallelism:
    @pytest.mark.asyncio
    async def test_no_season_covers_all_seasons(self) -> None:
        """A full-series search must not stop after the first season."""
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://s.to"
        plugin._client = _routed_client()

        results = await plugin.search("stranger things")

        seasons = {r.metadata["season"] for r in results}
        assert seasons == {"1", "2", "3", "4"}

    @pytest.mark.asyncio
    async def test_season_scrape_resolves_hosters_in_parallel(self) -> None:
        """The hosters of one episode resolve together, not one by one."""
        plugin = _make_plugin()
        plugin.base_url = "https://s.to"
        active = {"now": 0, "peak": 0}
        plugin._client = _routed_client(active)
        detail = _SeriesDetailParser("https://s.to")
        detail.feed(_SERIES_DETAIL_HTML)
        detail.episodes = detail.episodes[:1]

        episodes = await plugin._scrape_season_episodes("stranger-things", 1, detail)

        assert len(episodes[0]["links"]) > 1
        assert active["peak"] > 1

    @pytest.mark.asyncio
    async def test_series_are_processed_in_parallel(self) -> None:
        plugin = _make_plugin()
        active = {"now": 0, "peak": 0}

        async def _process(*_args: object, **_kw: object) -> list:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
            await asyncio.sleep(0.01)
            active["now"] -= 1
            return []

        plugin._ensure_client = AsyncMock()
        plugin._verify_domain = AsyncMock()
        plugin._paginate_search = AsyncMock(return_value=[{"slug": "a"}] * 3)
        plugin._fetch_all_details = AsyncMock(
            return_value=[({"slug": s}, MagicMock()) for s in "abc"]
        )
        plugin._process_series = _process

        await plugin.search("x", season=1, episode=1)

        assert active["peak"] > 1


_EPISODE_URL = "https://s.to/serie/stranger-things/staffel-1/episode-1"
_LINK_BOX = "button.link-box[data-play-url]"
_GATE_PAGE = "<script>window.parent.postMessage({type: 'frameBridge'})</script>"


def _gated_site(router: respx.MockRouter, *, trusted: str | None) -> None:
    """Episode page whose link-outs redirect only for a trusted session.

    *trusted* is the cookie that marks a session past the gate; None makes
    every link-out redirect (no gate).
    """

    def _link_out(request: httpx.Request) -> httpx.Response:
        if trusted is None or trusted in request.headers.get("cookie", ""):
            target = "https://voe.sx/e/" + request.url.params["t"]
            return httpx.Response(302, headers={"location": target})
        return httpx.Response(200, text=_GATE_PAGE)

    router.get(_EPISODE_URL).respond(200, text=_EPISODE_HTML)
    router.get(url__startswith="https://s.to/r").mock(side_effect=_link_out)


def _gate_fetcher(result: ClickThrough | None) -> AsyncMock:
    fetcher = AsyncMock()
    fetcher.click_through = AsyncMock(return_value=result)
    return fetcher


_PASSED = ClickThrough(
    url="https://voe.sx/e/abc123", cookies={"laravel_session": "passed"}
)


class TestLinkOutGate:
    """After bursts of link-outs s.to asks every new session for Turnstile;
    a session that passed it once in the browser gets redirects again."""

    @pytest.fixture(autouse=True)
    def _reset_fetcher(self) -> Iterator[None]:
        yield
        _StoPlugin.set_browser_fetcher(None)

    async def _plugin(self, client: httpx.AsyncClient) -> object:
        plugin = _make_plugin()
        plugin._client = client
        plugin.base_url = "https://s.to"
        return plugin

    @respx.mock
    async def test_passes_gate_once_and_reads_the_page_again(self) -> None:
        _gated_site(respx.mock, trusted="laravel_session=passed")
        fetcher = _gate_fetcher(_PASSED)
        _StoPlugin.set_browser_fetcher(fetcher)

        async with httpx.AsyncClient() as client:
            plugin = await self._plugin(client)
            links = await plugin._episode_links(_EPISODE_URL)

        assert [link["link"] for link in links] == [
            "https://voe.sx/e/abc123",
            "https://voe.sx/e/def456",
            "https://voe.sx/e/ghi789",
        ]
        fetcher.click_through.assert_awaited_once()
        assert fetcher.click_through.await_args.args == (_EPISODE_URL, _LINK_BOX)

    @respx.mock
    async def test_no_browser_when_link_outs_redirect(self) -> None:
        _gated_site(respx.mock, trusted=None)
        fetcher = _gate_fetcher(_PASSED)
        _StoPlugin.set_browser_fetcher(fetcher)

        async with httpx.AsyncClient() as client:
            plugin = await self._plugin(client)
            links = await plugin._episode_links(_EPISODE_URL)

        assert links[0]["link"] == "https://voe.sx/e/abc123"
        fetcher.click_through.assert_not_awaited()

    @respx.mock
    async def test_failed_pass_is_not_retried_at_once(self) -> None:
        _gated_site(respx.mock, trusted="laravel_session=passed")
        fetcher = _gate_fetcher(None)
        _StoPlugin.set_browser_fetcher(fetcher)

        async with httpx.AsyncClient() as client:
            plugin = await self._plugin(client)
            first = await plugin._episode_links(_EPISODE_URL)
            await plugin._episode_links(_EPISODE_URL)

        # Unresolved link-outs stay (JDownloader can still follow them)
        assert first[0]["link"].startswith("https://s.to/r?t=")
        fetcher.click_through.assert_awaited_once()

    @respx.mock
    async def test_pass_outlives_a_cancelled_search(self) -> None:
        # Stremio cuts the search at its deadline; the next request profits
        _gated_site(respx.mock, trusted="laravel_session=passed")
        release = asyncio.Event()
        started = asyncio.Event()

        async def _slow_pass(*_args: object, **_kw: object) -> ClickThrough:
            started.set()
            await release.wait()
            return _PASSED

        fetcher = AsyncMock()
        fetcher.click_through = AsyncMock(side_effect=_slow_pass)
        _StoPlugin.set_browser_fetcher(fetcher)

        async with httpx.AsyncClient() as client:
            plugin = await self._plugin(client)
            search = asyncio.create_task(plugin._episode_links(_EPISODE_URL))
            await started.wait()
            search.cancel()
            release.set()
            assert await plugin._gate_task is True

            links = await plugin._episode_links(_EPISODE_URL)

        assert links[0]["link"] == "https://voe.sx/e/abc123"
        fetcher.click_through.assert_awaited_once()


class TestRelevantSeries:
    """The site's search also lists unrelated series ("Breaking Bad" finds
    "Better Call Saul"); scraping all of them cost seconds and dozens of
    link-out requests, which made the site gate every link-out."""

    def test_keeps_series_containing_every_query_word(self) -> None:
        series = [
            {"title": "Better Call Saul"},
            {"title": "Breaking Bad"},
            {"title": "El Camino: Ein Breaking Bad Film"},
        ]
        titles = [s["title"] for s in _relevant_series(series, "Breaking Bad")]
        assert titles == ["Breaking Bad", "El Camino: Ein Breaking Bad Film"]

    def test_each_series_once(self) -> None:
        # The search page links a series from its card and its episode hits
        series = [
            {"title": "Breaking Bad", "slug": "breaking-bad"},
            {"title": "Breaking Bad", "slug": "breaking-bad"},
        ]
        assert _relevant_series(series, "Breaking Bad") == series[:1]

    def test_folds_case_accents_and_punctuation(self) -> None:
        series = [{"title": "Pokémon: Die Serie"}]
        assert _relevant_series(series, "pokemon die serie") == series

    def test_falls_back_to_the_sites_top_hits(self) -> None:
        # Other-language titles: "Money Heist" is "Haus des Geldes" there
        series = [{"title": f"Serie {i}"} for i in range(6)]
        assert _relevant_series(series, "Money Heist") == series[:3]

    def test_episode_requests_take_the_closest_series(self) -> None:
        series = [{"title": f"Dark {i}", "slug": f"d{i}"} for i in range(5)]
        series.append({"title": "Dark", "slug": "dark"})
        result = _relevant_series(series, "Dark", limit=3)
        assert [s["slug"] for s in result] == ["dark", "d0", "d1"]

    @pytest.mark.asyncio
    async def test_search_skips_unrelated_series(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://s.to"
        plugin._client = _routed_client()

        await plugin.search("stranger things", season=1, episode=1)

        fetched = [str(c.args[0]) for c in plugin._client.get.await_args_list]
        assert any("/serie/stranger-things" in url for url in fetched)
        assert not any("/serie/dark" in url for url in fetched)


class TestSearch:
    @pytest.mark.asyncio
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True
        plugin.base_url = "https://s.to"

        # Search returns 3 links (2 series + 1 episode link with /serie/ prefix)
        # Each triggers a detail fetch; only "stranger-things" has season data.
        plugin._client = _routed_client()

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

        plugin._client = _routed_client()

        # Stranger Things is no anime (it used to come back labelled 5070)
        assert await plugin.search("stranger things", category=5070) == []

        # TV/HD is not told apart: all series, labelled by their genres
        results = await plugin.search("stranger things", category=5040)
        assert results
        assert {r.category for r in results} == {5000}

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


# ---------------------------------------------------------------------------
# Cloudflare fallback
# ---------------------------------------------------------------------------


class TestCloudflareFallback:
    """Pages go through the base helpers (plugin timeout, UA, browser)."""

    @pytest.mark.asyncio
    async def test_challenged_search_is_loaded_through_the_browser(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

        challenge = _mock_response(
            text="<title>Just a moment...</title>", status_code=403
        )
        challenge.url = httpx.URL("https://s.to/suche?term=stranger")
        fetcher = AsyncMock()
        fetcher.fetch_text = AsyncMock(return_value=_SEARCH_HTML)
        monkeypatch.setattr(HttpxPluginBase, "_browser_fetcher", fetcher)
        monkeypatch.setattr(HttpxPluginBase, "_cf_blocked_until", {})
        plugin = _make_plugin()
        plugin.base_url = "https://s.to"
        plugin._client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client.get = AsyncMock(return_value=challenge)

        results = await plugin._search_series("stranger")

        assert results
        fetcher.fetch_text.assert_awaited_once()
