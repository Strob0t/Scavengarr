"""Tests for the aniworld.to Python plugin (httpx-based)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from structlog.testing import capture_logs

from scavengarr.domain.entities.stremio import EpisodeRef

_PLUGIN_PATH = Path(__file__).resolve().parents[3] / "plugins" / "aniworld.py"


def _load_module() -> ModuleType:
    """Load aniworld.py plugin via importlib."""
    spec = importlib.util.spec_from_file_location("aniworld_plugin", str(_PLUGIN_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load_module()
_AniworldPlugin = _mod.AniworldPlugin
_DetailPageParser = _mod._DetailPageParser
_EpisodePageParser = _mod._EpisodePageParser
_strip_html_tags = _mod._strip_html_tags


def _make_plugin() -> object:
    """Create AniworldPlugin instance."""
    return _AniworldPlugin()


# ---------------------------------------------------------------------------
# Sample HTML fragments
# ---------------------------------------------------------------------------

_DETAIL_HTML = """\
<html><body>
<div class="seriesCoverBox">
  <img data-src="/public/img/covers/naruto.jpg" alt="Naruto">
</div>

<div class="seri_des"
 data-full-description="Naruto is a young ninja who seeks recognition.">
  <p>Naruto is a young ninja...</p>
</div>

<div class="genres">
  <ul>
    <li><a href="/genre/action">Action</a></li>
    <li><a href="/genre/adventure">Adventure</a></li>
    <li><a href="/genre/comedy">Comedy</a></li>
  </ul>
</div>

<table class="seasonEpisodesList">
  <tbody>
    <tr>
      <td>
        <a href="/anime/stream/naruto/staffel-1/episode-1">Episode 1</a>
      </td>
    </tr>
    <tr>
      <td>
        <a href="/anime/stream/naruto/staffel-1/episode-2">Episode 2</a>
      </td>
    </tr>
  </tbody>
</table>
</body></html>
"""

_DETAIL_HTML_NO_DATA_ATTR = """\
<html><body>
<div class="seri_des">
  Inline description text here.
</div>
</body></html>
"""

_EPISODE_HTML = """\
<html><body>
<ul class="hosterSiteVideo">
  <li data-lang-key="1" data-link-id="100" data-link-target="/redirect/100">
    <div class="watchEpisode">
      <a href="#"><h4>VOE</h4></a>
    </div>
  </li>
  <li data-lang-key="2" data-link-id="101" data-link-target="/redirect/101">
    <div class="watchEpisode">
      <a href="#"><h4>Filemoon</h4></a>
    </div>
  </li>
  <li data-lang-key="1" data-link-id="102" data-link-target="/redirect/102">
    <div class="watchEpisode">
      <a href="#"><h4>Vidmoly</h4></a>
    </div>
  </li>
  <li data-lang-key="3" data-link-id="103" data-link-target="/redirect/103">
    <div class="watchEpisode">
      <a href="#"><h4>Doodstream</h4></a>
    </div>
  </li>
</ul>
</body></html>
"""

_AJAX_SEARCH_RESPONSE = [
    {
        "title": "<em>Naruto</em>",
        "description": "A <em>ninja</em> story",
        "link": "/anime/stream/naruto",
    },
    {
        "title": "<em>Naruto</em> Shippuuden",
        "description": "Continuation of <em>Naruto</em>",
        "link": "/anime/stream/naruto-shippuuden",
    },
]


# ---------------------------------------------------------------------------
# Plugin attribute tests
# ---------------------------------------------------------------------------


class TestPluginAttributes:
    def test_name(self) -> None:
        plugin = _make_plugin()
        assert plugin.name == "aniworld"

    def test_version(self) -> None:
        plugin = _make_plugin()
        assert plugin.version == "1.0.0"

    def test_mode(self) -> None:
        plugin = _make_plugin()
        assert plugin.mode == "httpx"


# ---------------------------------------------------------------------------
# DetailPageParser tests
# ---------------------------------------------------------------------------


class TestDetailPageParser:
    def test_description_from_data_attr(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed(_DETAIL_HTML)
        assert parser.description == "Naruto is a young ninja who seeks recognition."

    def test_description_fallback_to_text(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed(_DETAIL_HTML_NO_DATA_ATTR)
        assert parser.description == "Inline description text here."

    def test_genres_extracted(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed(_DETAIL_HTML)
        assert parser.genres == ["Action", "Adventure", "Comedy"]

    def test_cover_url(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed(_DETAIL_HTML)
        assert parser.cover_url == "https://aniworld.to/public/img/covers/naruto.jpg"

    def test_first_episode_url(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed(_DETAIL_HTML)
        assert (
            parser.first_episode_url
            == "https://aniworld.to/anime/stream/naruto/staffel-1/episode-1"
        )

    def test_no_episode_table(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed("<html><body><p>No table</p></body></html>")
        assert parser.first_episode_url == ""

    def test_no_genres(self) -> None:
        parser = _DetailPageParser("https://aniworld.to")
        parser.feed("<html><body></body></html>")
        assert parser.genres == []


# ---------------------------------------------------------------------------
# EpisodePageParser tests
# ---------------------------------------------------------------------------


class TestEpisodePageParser:
    def test_hoster_links_extracted(self) -> None:
        parser = _EpisodePageParser("https://aniworld.to")
        parser.feed(_EPISODE_HTML)
        assert len(parser.hoster_links) == 4

    def test_hoster_names(self) -> None:
        parser = _EpisodePageParser("https://aniworld.to")
        parser.feed(_EPISODE_HTML)
        names = [h["hoster"] for h in parser.hoster_links]
        assert names == ["voe", "filemoon", "vidmoly", "doodstream"]

    def test_redirect_urls(self) -> None:
        parser = _EpisodePageParser("https://aniworld.to")
        parser.feed(_EPISODE_HTML)
        links = [h["link"] for h in parser.hoster_links]
        assert links == [
            "https://aniworld.to/redirect/100",
            "https://aniworld.to/redirect/101",
            "https://aniworld.to/redirect/102",
            "https://aniworld.to/redirect/103",
        ]

    def test_language_labels(self) -> None:
        parser = _EpisodePageParser("https://aniworld.to")
        parser.feed(_EPISODE_HTML)
        langs = [h["language"] for h in parser.hoster_links]
        assert langs == [
            "German Dub",
            "English Sub",
            "German Dub",
            "German Sub",
        ]

    def test_empty_page(self) -> None:
        parser = _EpisodePageParser("https://aniworld.to")
        parser.feed("<html><body></body></html>")
        assert parser.hoster_links == []

    def test_li_without_lang_key_skipped(self) -> None:
        html = """
        <ul>
          <li data-link-target="/redirect/999">
            <h4>NoLang</h4>
          </li>
        </ul>
        """
        parser = _EpisodePageParser("https://aniworld.to")
        parser.feed(html)
        assert parser.hoster_links == []


# ---------------------------------------------------------------------------
# HTML tag stripper tests
# ---------------------------------------------------------------------------


class TestStripHtmlTags:
    def test_strips_em_tags(self) -> None:
        assert _strip_html_tags("<em>Naruto</em>") == "Naruto"

    def test_strips_mixed_tags(self) -> None:
        assert _strip_html_tags("A <b>bold</b> and <em>italic</em> text") == (
            "A bold and italic text"
        )

    def test_plain_text_unchanged(self) -> None:
        assert _strip_html_tags("plain text") == "plain text"

    def test_empty_string(self) -> None:
        assert _strip_html_tags("") == ""


# ---------------------------------------------------------------------------
# Search integration tests (mocked httpx)
# ---------------------------------------------------------------------------


def _make_mock_response(
    status_code: int = 200,
    json_data: object = None,
    text: str = "",
) -> httpx.Response:
    """Create a mock httpx.Response."""
    resp = httpx.Response(
        status_code=status_code,
        request=httpx.Request("GET", "https://aniworld.to"),
    )
    if json_data is not None:
        import json

        resp._content = json.dumps(json_data).encode()
    elif text:
        resp._content = text.encode()
    else:
        resp._content = b""
    return resp


class TestSearch:
    async def test_search_returns_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_resp = _make_mock_response(json_data=_AJAX_SEARCH_RESPONSE)
        detail_resp = _make_mock_response(text=_DETAIL_HTML)
        episode_resp = _make_mock_response(text=_EPISODE_HTML)

        def _route_get(url, **_kw):
            if "/staffel-" in str(url) and "/episode-" in str(url):
                return episode_resp
            return detail_resp

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(side_effect=_route_get)
        plugin._client = mock_client

        results = await plugin.search("naruto")

        assert len(results) == 2
        assert results[0].title == "Naruto"
        assert results[0].category == 5070
        assert "redirect/100" in results[0].download_link
        assert len(results[0].download_links) == 4
        assert results[0].download_links[0]["hoster"] == "voe"
        expected_desc = "Naruto is a young ninja who seeks recognition."
        assert results[0].description == expected_desc
        # the detail page's first episode: no episode claimed
        assert "season" not in results[0].metadata
        assert "episode" not in results[0].metadata

    @pytest.mark.parametrize(
        ("season", "episode", "expected"),
        [
            (2, 3, "https://aniworld.to/anime/stream/naruto/staffel-2/episode-3"),
            # a season without episode starts at that season's first episode
            (2, None, "https://aniworld.to/anime/stream/naruto/staffel-2/episode-1"),
        ],
    )
    async def test_episode_url_keeps_stream_path(
        self, season: int, episode: int | None, expected: str
    ) -> None:
        """Episode pages live under /anime/stream/<slug>/ like the detail page."""
        plugin = _make_plugin()
        plugin._domain_verified = True
        detail_resp = _make_mock_response(text=_DETAIL_HTML)
        episode_resp = _make_mock_response(text=_EPISODE_HTML)

        def _route_get(url, **_kw):
            return episode_resp if "/episode-" in str(url) else detail_resp

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(
            return_value=_make_mock_response(json_data=_AJAX_SEARCH_RESPONSE)
        )
        mock_client.get = AsyncMock(side_effect=_route_get)
        plugin._client = mock_client

        results = await plugin.search("naruto", season=season, episode=episode)

        requested = [str(c.args[0]) for c in mock_client.get.call_args_list]
        assert expected in requested
        # the episode fetched, for the Stremio episode filter
        assert results[0].metadata["season"] == season
        assert results[0].metadata["episode"] == (episode or 1)

    async def test_search_empty_query_returns_empty(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        results = await plugin.search("")
        assert results == []

    async def test_search_non_anime_category_returns_empty(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        results = await plugin.search("naruto", category=2000)
        assert results == []

    async def test_search_anime_category_allowed(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_resp = _make_mock_response(json_data=_AJAX_SEARCH_RESPONSE[:1])
        detail_resp = _make_mock_response(text=_DETAIL_HTML)
        episode_resp = _make_mock_response(text=_EPISODE_HTML)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(
            side_effect=lambda url, **kw: (
                episode_resp
                if "/staffel-" in str(url) and "/episode-" in str(url)
                else detail_resp
            )
        )
        plugin._client = mock_client

        results = await plugin.search("naruto", category=5070)
        assert len(results) == 1

    async def test_search_parent_tv_category_allowed(self) -> None:
        """Parent category 5000 (any TV) must include anime (5070)."""
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_resp = _make_mock_response(json_data=_AJAX_SEARCH_RESPONSE[:1])
        detail_resp = _make_mock_response(text=_DETAIL_HTML)
        episode_resp = _make_mock_response(text=_EPISODE_HTML)

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(
            side_effect=lambda url, **kw: (
                episode_resp
                if "/staffel-" in str(url) and "/episode-" in str(url)
                else detail_resp
            )
        )
        plugin._client = mock_client

        results = await plugin.search("naruto", category=5000)
        assert len(results) == 1

    async def test_search_no_results(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_resp = _make_mock_response(json_data=[])
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        plugin._client = mock_client

        results = await plugin.search("nonexistent_anime_xyz")
        assert results == []

    async def test_search_detail_without_hosters_skipped(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        search_resp = _make_mock_response(json_data=_AJAX_SEARCH_RESPONSE[:1])
        # Detail page with no episode table → no first_episode_url
        detail_resp = _make_mock_response(text="<html><body>No table</body></html>")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=search_resp)
        mock_client.get = AsyncMock(return_value=detail_resp)
        plugin._client = mock_client

        results = await plugin.search("naruto")
        assert results == []

    @staticmethod
    def _plugin_answering(
        answer: list[dict[str, str]],
    ) -> tuple[Any, AsyncMock]:
        """Plugin whose ajax search returns *answer*, and its mock client."""
        plugin: Any = _make_plugin()
        plugin._domain_verified = True
        detail_resp = _make_mock_response(text=_DETAIL_HTML)
        episode_resp = _make_mock_response(text=_EPISODE_HTML)
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=_make_mock_response(json_data=answer))
        mock_client.get = AsyncMock(
            side_effect=lambda url, **_kw: (
                episode_resp if "/episode-" in str(url) else detail_resp
            )
        )
        plugin._client = mock_client
        return plugin, mock_client

    async def test_skips_hits_that_are_no_series(self) -> None:
        # The ajax search also lists FAQ pages; each cost a detail page and a
        # made-up episode page
        answer = [
            *_AJAX_SEARCH_RESPONSE,
            {
                "title": "wieso ist <em>naruto</em> nicht auf ger sub",
                "description": "Wir helfen dir bei Problemen",
                "link": "/support/frage/wieso-ist-naruto-nicht-auf-ger-sub",
            },
        ]
        plugin, client = self._plugin_answering(answer)

        await plugin.search("naruto", season=1, episode=1)

        requested = [str(c.args[0]) for c in client.get.call_args_list]
        assert requested
        assert not [url for url in requested if "/support/" in url]

    async def test_scrapes_only_matching_series(self) -> None:
        # Loose matches cost pages and link-outs; the sister site s.to gates
        # its link-outs after such bursts
        plugin, client = self._plugin_answering(_AJAX_SEARCH_RESPONSE)

        await plugin.search("Naruto Shippuuden", season=1, episode=1)

        requested = [str(c.args[0]) for c in client.get.call_args_list]
        assert requested
        assert all("naruto-shippuuden" in url for url in requested)

    async def test_empty_answer_means_no_hits_not_invalid_json(self) -> None:
        # The site answers a search without hits with an empty body
        # ("Breaking Bad"), which was logged as invalid JSON on every request
        plugin = _make_plugin()
        plugin._domain_verified = True
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=_make_mock_response(text=""))
        plugin._client = mock_client

        with capture_logs() as logs:
            results = await plugin.search("Breaking Bad")

        assert results == []
        assert not [e for e in logs if e["event"] == "aniworld_invalid_json"]

    async def test_search_ajax_error_returns_empty(self) -> None:
        plugin = _make_plugin()
        plugin._domain_verified = True

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        plugin._client = mock_client

        results = await plugin.search("naruto")
        assert results == []


# ---------------------------------------------------------------------------
# Domain verification tests
# ---------------------------------------------------------------------------


class TestDomainVerification:
    async def test_first_domain_works(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        resp = MagicMock(spec=httpx.Response, history=[])
        resp.status_code = 200
        resp.url = httpx.URL("https://aniworld.to/")
        mock_client.head = AsyncMock(return_value=resp)
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        assert "aniworld.to" in plugin.base_url

    def test_no_scam_copy(self) -> None:
        # aniworld.info imitates the site with ad pages (JDownloader
        # SerienStreamTo, 2026-09-09)
        assert "aniworld.info" not in _AniworldPlugin._domains

    async def test_a_single_domain_is_not_checked(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.head = AsyncMock(
            side_effect=httpx.ConnectError("Connection failed")
        )
        plugin._client = mock_client

        await plugin._verify_domain()

        assert plugin._domain_verified is True
        mock_client.head.assert_not_called()

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
# Cleanup tests
# ---------------------------------------------------------------------------


class TestCleanup:
    async def test_cleanup_closes_client(self) -> None:
        plugin = _make_plugin()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        plugin._client = mock_client

        await plugin.cleanup()

        mock_client.aclose.assert_awaited_once()
        assert plugin._client is None

    async def test_cleanup_no_client(self) -> None:
        plugin = _make_plugin()
        await plugin.cleanup()
        assert plugin._client is None


# ---------------------------------------------------------------------------
# Episode location (a Stremio request's reference)
# ---------------------------------------------------------------------------


def _season_table(season: int, rows: list[tuple[str, str]]) -> str:
    """A season page's episode table as the site renders it."""
    body = "".join(
        f'<tr data-episode-id="{season}{n}" itemprop="episode">'
        f'<td class="season{season}EpisodeID">'
        f'<meta itemprop="episodeNumber" content="{n}" />'
        f'<a href="/anime/stream/one-piece/staffel-{season}/episode-{n}">'
        f"Folge {n}</a></td>"
        '<td class="seasonEpisodeTitle">'
        f'<a href="/anime/stream/one-piece/staffel-{season}/episode-{n}">'
        f"<strong>{german}</strong> - <span>{english}</span></a></td></tr>"
        for n, (german, english) in enumerate(rows, start=1)
    )
    return f'<table class="seasonEpisodesList"><tbody>{body}</tbody></table>'


def _series_page(seasons: int) -> str:
    """A series page: the navigation's season links and staffel-1's table."""
    nav = "".join(
        f'<a href="/anime/stream/one-piece/staffel-{n}">Staffel {n}</a>'
        for n in range(1, seasons + 1)
    )
    return (
        "<html><body>"
        '<div class="genres"><ul><li><a href="/genre/action">Action</a></li></ul></div>'
        f'<div class="hosterSiteDirectNav">{nav}'
        '<a href="/anime/stream/one-piece/filme">Filme</a>'
        '<a href="/anime/stream/one-piece/staffel-1/episode-1">1</a></div>'
        + _season_table(1, [("Hier kommt Ruffy", f"{_LUFFY} [Episode 001]")])
        + "</body></html>"
    )


_LUFFY = "I'm Luffy! The Man Who Will Become the Pirate King!"
_LABOON = "The First Line of Defense? The Giant Whale Laboon Appears!"
_STAFFEL_2 = "<html><body>" + _season_table(
    2,
    [
        ("Ein Bad in Magensäure", f"{_LABOON} [Episode 062]"),
        ("Das Versprechen", "A Promise Between Men! [Episode 063]"),
    ],
)
_ONE_PIECE_HIT = [
    {"title": "One Piece", "description": "Piraten", "link": "/anime/stream/one-piece"}
]


def _locating_plugin(
    cache: AsyncMock | None = None, *, series_page: str | None = None
) -> tuple[Any, list[str]]:
    """A plugin whose site lists One Piece with two seasons, and the paths it
    fetches; a page the site lacks answers 404."""
    plugin: Any = _make_plugin()
    plugin._domain_verified = True
    plugin._cache = cache
    paths: list[str] = []
    pages = {
        "/anime/stream/one-piece": series_page or _series_page(2),
        "/anime/stream/one-piece/staffel-1": _series_page(2),
        "/anime/stream/one-piece/staffel-2": _STAFFEL_2,
    }

    async def _get(url: str, **_kw: Any) -> httpx.Response:
        path = str(url).removeprefix("https://aniworld.to")
        paths.append(path)
        if "/episode-" in path:
            return _make_mock_response(text=_EPISODE_HTML)
        page = pages.get(path)
        return (
            _make_mock_response(text=page)
            if page
            else _make_mock_response(status_code=404)
        )

    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=_make_mock_response(json_data=_ONE_PIECE_HIT))
    client.get = AsyncMock(side_effect=_get)
    plugin._client = client
    return plugin, paths


class TestLocatesEpisodes:
    _LABOON_REF = EpisodeRef(5, 2, _LABOON, "2001-03-21", absolute=62)

    def test_declares_the_capability(self) -> None:
        assert _make_plugin().locates_episodes is True  # type: ignore[attr-defined]

    def test_the_series_page_names_its_seasons(self) -> None:
        parser = _load_module()._DetailPageParser("https://aniworld.to")
        parser.feed(_series_page(3))

        # the "Filme" link and the episode links are no seasons
        assert parser.seasons == [1, 2, 3]

    async def test_a_located_episode_comes_from_the_sites_page(self) -> None:
        plugin, paths = _locating_plugin()

        with capture_logs() as logs:
            results = await plugin.search(
                "one piece", season=5, episode=2, episode_ref=self._LABOON_REF
            )

        assert len(results) == 1
        assert results[0].metadata == {
            "genres": "Action",
            "cover_url": "",
            "season": 5,
            "episode": 2,
            "site_season": 2,
            "site_episode": 1,
            "episode_located_by": "number",
        }
        assert "/anime/stream/one-piece/staffel-2/episode-1" in paths
        assert not any("/staffel-5/" in path for path in paths)
        located = next(e for e in logs if e["event"] == "aniworld_episode_located")
        assert (
            located.items()
            >= {
                "slug": "one-piece",
                "season": 5,
                "episode": 2,
                "absolute": 62,
                "site_season": 2,
                "site_episode": 1,
                "located_by": "number",
            }.items()
        )

    async def test_the_runners_entry_reaches_the_reference(self) -> None:
        """The runner calls ``isolated_search``: the reference arrives there too
        (the probe of 2026-10-09 found it did not)."""
        plugin, paths = _locating_plugin()

        results = await plugin.isolated_search(
            "one piece", 5000, season=5, episode=2, episode_ref=self._LABOON_REF
        )

        assert len(results) == 1
        assert results[0].metadata["site_episode"] == 1
        assert "/anime/stream/one-piece/staffel-2/episode-1" in paths

    async def test_an_episode_not_located_gives_no_result(self) -> None:
        plugin, paths = _locating_plugin()
        ref = EpisodeRef(9, 9, "Nothing Like It", absolute=999)

        with capture_logs() as logs:
            results = await plugin.search(
                "one piece", season=9, episode=9, episode_ref=ref
            )

        # never the request's numbers on the site's seasons
        assert results == []
        assert not any("/episode-" in path for path in paths)
        missed = next(e for e in logs if e["event"] == "aniworld_episode_not_located")
        assert (
            missed.items()
            >= {
                "slug": "one-piece",
                "season": 9,
                "episode": 9,
                "absolute": 999,
                "rows": 3,
            }.items()
        )

    async def test_without_a_reference_the_page_comes_from_the_numbers(self) -> None:
        plugin, paths = _locating_plugin()

        results = await plugin.search("one piece", season=5, episode=2)

        assert len(results) == 1
        assert results[0].metadata == {
            "genres": "Action",
            "cover_url": "",
            "season": 5,
            "episode": 2,
        }
        assert "/anime/stream/one-piece/staffel-5/episode-2" in paths
        assert not any(path.endswith(("/staffel-1", "/staffel-2")) for path in paths)

    async def test_the_index_is_kept_for_a_week(self) -> None:
        cache = AsyncMock()
        cache.get.return_value = None
        plugin, paths = _locating_plugin(cache)

        await plugin.search(
            "one piece", season=5, episode=2, episode_ref=self._LABOON_REF
        )

        cache.set.assert_awaited_once()
        key, rows = cache.set.await_args.args
        assert key == "aniworld:episodes:v1:one-piece"
        assert cache.set.await_args.kwargs == {"ttl": 7 * 24 * 3600}
        assert rows == [
            [1, 1, "Hier kommt Ruffy", _LUFFY, 1],
            [2, 1, "Ein Bad in Magensäure", _LABOON, 62],
            [2, 2, "Das Versprechen", "A Promise Between Men!", 63],
        ]
        assert paths.count("/anime/stream/one-piece/staffel-2") == 1

        # the next request reads no season page
        cache.get.return_value = rows
        again, paths = _locating_plugin(cache)

        results = await again.search(
            "one piece", season=5, episode=2, episode_ref=self._LABOON_REF
        )

        assert len(results) == 1
        assert paths == [
            "/anime/stream/one-piece",
            "/anime/stream/one-piece/staffel-2/episode-1",
        ]

    async def test_a_series_page_without_season_links_is_one_season(self) -> None:
        plugin, paths = _locating_plugin(series_page=_series_page(0))
        ref = EpisodeRef(
            1, 1, "I'm Luffy! The Man Who's Gonna Be King of the Pirates!", absolute=1
        )

        results = await plugin.search("one piece", season=1, episode=1, episode_ref=ref)

        assert len(results) == 1
        assert results[0].metadata["site_episode"] == 1
        assert "/anime/stream/one-piece/staffel-1" in paths
