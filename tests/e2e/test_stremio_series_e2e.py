"""E2E tests for Stremio series stream resolution.

Exercises the full pipeline from HTTP request through to JSON response
for series queries (season + episode). Verifies that:

  1. The stream endpoint correctly parses series IDs (tt...:S:E).
  2. Plugins receive correct season/episode params.
  3. The episode filter removes non-matching episodes BEFORE validation.
  4. Each plugin produces only a handful of streams (different hosters),
     not hundreds of duplicate results.
  5. Title-match filtering rejects unrelated results.
  6. Streams are sorted by language/quality/hoster.
  7. Proxy play URLs are generated when a stream_link_repo is configured.

All real components are used except external I/O:
  - Real: StremioStreamUseCase, StreamSorter, stream_converter,
          title_matcher, _filter_by_episode.
  - Mocked: TmdbClientPort, PluginRegistryPort, SearchEnginePort,
            StreamLinkRepository.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase
from scavengarr.domain.entities.stremio import (
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.config.schema import StremioConfig
from scavengarr.infrastructure.plugins.constants import (
    DEFAULT_USER_AGENT,
    search_max_results,
)
from scavengarr.infrastructure.stremio.stream_converter import convert_search_results
from scavengarr.infrastructure.stremio.stream_sorter import StreamSorter
from scavengarr.infrastructure.stremio.title_matcher import filter_by_title_match
from scavengarr.interfaces.api.stremio.router import router

_PREFIX = "/api/v1"


# ---------------------------------------------------------------------------
# Fake plugins that simulate real site behaviour
# ---------------------------------------------------------------------------


class _FakeSeriesPlugin:
    """Configurable fake streaming plugin for series tests.

    Accepts a list of SearchResults to return and records calls.
    """

    def __init__(
        self,
        name: str,
        results: list[SearchResult],
        *,
        default_language: str = "de",
    ) -> None:
        self.name = name
        self.provides = "stream"
        self.default_language = default_language
        self._results = results
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        self.calls.append(
            {"query": query, "category": category, "season": season, "episode": episode}
        )
        return list(self._results)


# ---------------------------------------------------------------------------
# SearchResult factories (realistic series data)
# ---------------------------------------------------------------------------


def _naruto_ep(
    season: int,
    episode: int,
    hosters: list[dict[str, str]],
    *,
    plugin_name: str = "aniworld",
) -> SearchResult:
    """Build a SearchResult for a Naruto Shippuden episode."""
    title = f"Naruto Shippuden S{season:02d}E{episode:02d}"
    first_link = hosters[0]["link"] if hosters else "https://example.com/v"
    return SearchResult(
        title=title,
        download_link=first_link,
        download_links=hosters,
        category=5070,
        metadata={"source_plugin": plugin_name},
    )


def _make_hosters(
    *names: str,
    quality: str = "720p",
    language: str = "German Dub",
) -> list[dict[str, str]]:
    """Build a download_links list with the given hoster names."""
    return [
        {
            "hoster": h,
            "link": f"https://{h.lower()}.sx/e/{h.lower()}_abc",
            "quality": quality,
            "language": language,
        }
        for h in names
    ]


# ---------------------------------------------------------------------------
# App factory (real use case, mocked ports)
# ---------------------------------------------------------------------------


def _make_series_app(
    *,
    title_info: TitleMatchInfo | None,
    plugins: dict[str, _FakeSeriesPlugin],
    config: StremioConfig | None = None,
    validate_passthrough: bool = True,
) -> tuple[FastAPI, dict[str, _FakeSeriesPlugin]]:
    """Build a FastAPI app with a real StremioStreamUseCase.

    Args:
        title_info: TMDB response (title + year).
        plugins: Mapping of plugin name to fake plugin.
        config: Optional StremioConfig overrides.
        validate_passthrough: If True, validate_results passes input through.

    Returns:
        (app, plugins_dict) so tests can inspect plugin call records.
    """
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(return_value=title_info)
    if title_info:
        tmdb.get_title_by_tmdb_id = AsyncMock(return_value=title_info.title)
    else:
        tmdb.get_title_by_tmdb_id = AsyncMock(return_value=None)

    plugin_names = sorted(plugins.keys())
    registry = MagicMock()
    registry.get_by_provides.side_effect = lambda p: (
        plugin_names if p == "stream" else []
    )
    registry.get.side_effect = lambda name: plugins[name]

    engine = AsyncMock()
    if validate_passthrough:
        engine.validate_results = AsyncMock(side_effect=lambda r: r)
    else:
        engine.validate_results = AsyncMock(return_value=[])

    stream_link_repo = AsyncMock()
    cfg = config or StremioConfig()

    use_case = StremioStreamUseCase(
        tmdb=tmdb,
        plugins=registry,
        search_engine=engine,
        config=cfg,
        sorter=StreamSorter(cfg),
        convert_fn=convert_search_results,
        filter_fn=filter_by_title_match,
        user_agent=DEFAULT_USER_AGENT,
        max_results_var=search_max_results,
        stream_link_repo=stream_link_repo,
    )

    app = FastAPI()
    app.include_router(router, prefix=_PREFIX)

    app_config = MagicMock()
    app_config.environment = "dev"
    app_config.app_name = "Scavengarr"

    app.state.config = app_config
    app.state.plugins = registry
    app.state.stremio_catalog_uc = None
    app.state.stremio_stream_uc = use_case
    app.state.stream_link_repo = stream_link_repo
    app.state.hoster_resolver_registry = None

    return app, plugins


# ---------------------------------------------------------------------------
# Naruto Shippuden - episode filter + deduplication
# ---------------------------------------------------------------------------


class TestNarutoShippudenE2E:
    """Naruto Shippuden series stream E2E tests.

    IMDb: tt0988824, Year: 2007.
    """

    _IMDB = "tt0988824"
    _TITLE = TitleMatchInfo(title="Naruto Shippuden", year=2007)

    def test_single_plugin_correct_episode(self) -> None:
        """One plugin returns the correct episode with 3 hosters -> 3 streams."""
        aniworld = _FakeSeriesPlugin(
            "aniworld",
            results=[
                _naruto_ep(
                    1,
                    5,
                    _make_hosters("VOE", "Filemoon", "Streamtape"),
                    plugin_name="aniworld",
                ),
            ],
        )
        app, _ = _make_series_app(
            title_info=self._TITLE, plugins={"aniworld": aniworld}
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:5.json")

        assert resp.status_code == 200
        streams = resp.json()["streams"]
        assert len(streams) == 3

        # Each stream should reference a different hoster in description
        descs = [s["description"] for s in streams]
        hoster_names = {"VOE", "FILEMOON", "STREAMTAPE"}
        for h in hoster_names:
            assert any(h in d for d in descs), f"Missing hoster {h} in streams"

        # All stream names should contain S01E05
        for s in streams:
            assert "S01E05" in s["name"]

    def test_episode_filter_drops_wrong_episodes(self) -> None:
        """Plugin returns 10 episodes, only the requested one survives."""
        all_episodes = [
            _naruto_ep(
                1,
                ep,
                _make_hosters("VOE", "Filemoon"),
                plugin_name="kinoger",
            )
            for ep in range(1, 11)
        ]
        kinoger = _FakeSeriesPlugin("kinoger", results=all_episodes)

        app, _ = _make_series_app(title_info=self._TITLE, plugins={"kinoger": kinoger})
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:5.json")

        assert resp.status_code == 200
        streams = resp.json()["streams"]
        # Only S01E05 hosters should survive (2 hosters)
        assert len(streams) == 2
        for s in streams:
            assert "S01E05" in s["name"]

    def test_episode_filter_drops_wrong_season(self) -> None:
        """Plugin returns episodes from multiple seasons; only S02 passes."""
        mixed = [
            _naruto_ep(1, 3, _make_hosters("VOE"), plugin_name="sto"),
            _naruto_ep(2, 3, _make_hosters("VOE", "Filemoon"), plugin_name="sto"),
            _naruto_ep(3, 3, _make_hosters("Streamtape"), plugin_name="sto"),
        ]
        sto = _FakeSeriesPlugin("sto", results=mixed)

        app, _ = _make_series_app(title_info=self._TITLE, plugins={"sto": sto})
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:2:3.json")

        streams = resp.json()["streams"]
        assert len(streams) == 2
        for s in streams:
            assert "S02E03" in s["name"]

    def test_multi_plugin_aggregation(self) -> None:
        """3 plugins each return the correct episode with different hosters."""
        aniworld = _FakeSeriesPlugin(
            "aniworld",
            results=[
                _naruto_ep(
                    1, 5, _make_hosters("VOE", "Filemoon"), plugin_name="aniworld"
                ),
            ],
        )
        kinoking = _FakeSeriesPlugin(
            "kinoking",
            results=[
                _naruto_ep(
                    1,
                    5,
                    _make_hosters("Streamtape", "DoodStream"),
                    plugin_name="kinoking",
                ),
            ],
        )
        sto = _FakeSeriesPlugin(
            "sto",
            results=[
                _naruto_ep(1, 5, _make_hosters("SuperVideo"), plugin_name="sto"),
            ],
        )

        app, _ = _make_series_app(
            title_info=self._TITLE,
            plugins={"aniworld": aniworld, "kinoking": kinoking, "sto": sto},
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:5.json")

        streams = resp.json()["streams"]
        # 2 + 2 + 1 = 5 streams total
        assert len(streams) == 5
        for s in streams:
            assert "S01E05" in s["name"]

        # Verify all source plugins appear in descriptions
        all_descs = " ".join(s["description"] for s in streams)
        assert "aniworld" in all_descs
        assert "kinoking" in all_descs
        assert "sto" in all_descs

    def test_no_hundreds_of_results_from_bad_plugin(self) -> None:
        """A broken plugin dumps 50 episodes; filter reduces to 1 episode's hosters."""
        spam_results = [
            _naruto_ep(
                1,
                ep,
                _make_hosters("VOE", "Filemoon", "Streamtape"),
                plugin_name="spammy",
            )
            for ep in range(1, 51)  # 50 episodes x 3 hosters = 150 potential streams
        ]
        spammy = _FakeSeriesPlugin("spammy", results=spam_results)

        app, _ = _make_series_app(title_info=self._TITLE, plugins={"spammy": spammy})
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:10.json")

        streams = resp.json()["streams"]
        # Only episode 10's 3 hosters should survive
        assert len(streams) == 3
        for s in streams:
            assert "S01E10" in s["name"]

    def test_plugins_receive_correct_season_episode(self) -> None:
        """Verify plugins are called with the correct season/episode params."""
        aniworld = _FakeSeriesPlugin("aniworld", results=[])
        kinoking = _FakeSeriesPlugin("kinoking", results=[])

        app, plugins = _make_series_app(
            title_info=self._TITLE,
            plugins={"aniworld": aniworld, "kinoking": kinoking},
        )
        client = TestClient(app)

        client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:3:12.json")

        for name, plugin in plugins.items():
            assert len(plugin.calls) == 1, f"{name} should be called once"
            call = plugin.calls[0]
            assert call["query"] == "Naruto Shippuden"
            assert call["category"] == 5000
            assert call["season"] == 3
            assert call["episode"] == 12

    def test_unparseable_titles_kept(self) -> None:
        """Results with non-standard titles (no SxxExx) pass through the filter."""
        results = [
            SearchResult(
                title="Naruto Shippuden - Komplettbox",
                download_link="https://voe.sx/e/pack",
                download_links=[
                    {
                        "hoster": "VOE",
                        "link": "https://voe.sx/e/pack",
                        "language": "German Dub",
                    }
                ],
                category=5070,
                metadata={"source_plugin": "aniworld"},
            ),
            _naruto_ep(1, 5, _make_hosters("Filemoon"), plugin_name="aniworld"),
        ]
        aniworld = _FakeSeriesPlugin("aniworld", results=results)

        app, _ = _make_series_app(
            title_info=self._TITLE, plugins={"aniworld": aniworld}
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:5.json")

        streams = resp.json()["streams"]
        # Both kept: pack title (unparseable) + S01E05
        assert len(streams) == 2

    def test_title_mismatch_filtered(self) -> None:
        """Results for a different anime are dropped by title-match filter."""
        wrong_title = SearchResult(
            title="Dragon Ball Z S01E05",
            download_link="https://voe.sx/e/dbz",
            download_links=[
                {
                    "hoster": "VOE",
                    "link": "https://voe.sx/e/dbz",
                    "language": "German Dub",
                }
            ],
            category=5070,
            metadata={"source_plugin": "aniworld"},
        )
        correct = _naruto_ep(1, 5, _make_hosters("Filemoon"), plugin_name="aniworld")
        aniworld = _FakeSeriesPlugin("aniworld", results=[wrong_title, correct])

        app, _ = _make_series_app(
            title_info=self._TITLE, plugins={"aniworld": aniworld}
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:5.json")

        streams = resp.json()["streams"]
        # Dragon Ball Z should be filtered by title matcher
        assert len(streams) == 1
        assert "Naruto" in streams[0]["name"]

    def test_plugin_error_does_not_crash_others(self) -> None:
        """One plugin failing does not prevent others from returning streams."""
        good = _FakeSeriesPlugin(
            "aniworld",
            results=[
                _naruto_ep(1, 5, _make_hosters("VOE"), plugin_name="aniworld"),
            ],
        )

        class _CrashingPlugin(_FakeSeriesPlugin):
            async def search(self, *_a: Any, **_kw: Any) -> list[SearchResult]:
                raise RuntimeError("site is down")

        bad = _CrashingPlugin("broken", results=[])

        app, _ = _make_series_app(
            title_info=self._TITLE,
            plugins={"aniworld": good, "broken": bad},
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:5.json")

        streams = resp.json()["streams"]
        assert len(streams) == 1
        assert "S01E05" in streams[0]["name"]

    def test_empty_results_return_empty_streams(self) -> None:
        """When no plugin returns results, response is an empty streams list."""
        empty = _FakeSeriesPlugin("aniworld", results=[])

        app, _ = _make_series_app(title_info=self._TITLE, plugins={"aniworld": empty})
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:5.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_streams_sorted_by_language_quality(self) -> None:
        """German Dub 720p ranks higher than English Sub 1080p."""
        results = [
            SearchResult(
                title="Naruto Shippuden S01E05",
                download_link="https://voe.sx/e/en",
                download_links=[
                    {
                        "hoster": "VOE",
                        "link": "https://voe.sx/e/en",
                        "quality": "1080p",
                        "language": "English Sub",
                    },
                ],
                category=5070,
                metadata={"source_plugin": "aniworld"},
            ),
            SearchResult(
                title="Naruto Shippuden S01E05",
                download_link="https://filemoon.sx/e/de",
                download_links=[
                    {
                        "hoster": "Filemoon",
                        "link": "https://filemoon.sx/e/de",
                        "quality": "720p",
                        "language": "German Dub",
                    },
                ],
                category=5070,
                metadata={"source_plugin": "aniworld"},
            ),
        ]
        aniworld = _FakeSeriesPlugin("aniworld", results=results)

        app, _ = _make_series_app(
            title_info=self._TITLE, plugins={"aniworld": aniworld}
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:5.json")

        streams = resp.json()["streams"]
        assert len(streams) == 2
        # German Dub should be first (higher language score)
        assert "German Dub" in streams[0]["description"]

    def test_proxy_play_urls_generated(self) -> None:
        """With a stream_link_repo, URLs become proxy /play/ links."""
        aniworld = _FakeSeriesPlugin(
            "aniworld",
            results=[
                _naruto_ep(1, 5, _make_hosters("VOE"), plugin_name="aniworld"),
            ],
        )
        app, _ = _make_series_app(
            title_info=self._TITLE, plugins={"aniworld": aniworld}
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:5.json")

        streams = resp.json()["streams"]
        assert len(streams) == 1
        # URL should be a proxy play link (base_url derived from request)
        assert "/api/v1/stremio/play/" in streams[0]["url"]

    def test_cors_headers_on_series_stream(self) -> None:
        """CORS headers must be present on series stream responses."""
        aniworld = _FakeSeriesPlugin("aniworld", results=[])
        app, _ = _make_series_app(
            title_info=self._TITLE, plugins={"aniworld": aniworld}
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:5.json")

        assert resp.headers.get("access-control-allow-origin") == "*"


# ---------------------------------------------------------------------------
# Breaking Bad - Western series (non-anime)
# ---------------------------------------------------------------------------


class TestBreakingBadE2E:
    """Breaking Bad series stream E2E tests.

    IMDb: tt0903747, Year: 2008.
    Verifies the episode filter works for non-anime western series too.
    """

    _IMDB = "tt0903747"
    _TITLE = TitleMatchInfo(title="Breaking Bad", year=2008)

    def _bb_ep(
        self,
        season: int,
        episode: int,
        hosters: list[dict[str, str]],
        plugin_name: str = "hdfilme",
    ) -> SearchResult:
        title = f"Breaking.Bad.S{season:02d}E{episode:02d}.720p.WEB-DL"
        return SearchResult(
            title=title,
            download_link=hosters[0]["link"] if hosters else "",
            download_links=hosters,
            release_name=title,
            category=5000,
            metadata={"source_plugin": plugin_name},
        )

    def test_release_name_style_filtered(self) -> None:
        """Episode filter parses dotted release names like Breaking.Bad.S05E03."""
        all_eps = [
            self._bb_ep(5, ep, _make_hosters("VOE", "Filemoon"), plugin_name="hdfilme")
            for ep in range(1, 17)  # 16 episodes in S05
        ]
        hdfilme = _FakeSeriesPlugin("hdfilme", results=all_eps)

        app, _ = _make_series_app(title_info=self._TITLE, plugins={"hdfilme": hdfilme})
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:5:3.json")

        streams = resp.json()["streams"]
        # Only S05E03's 2 hosters survive
        assert len(streams) == 2
        for s in streams:
            assert "S05E03" in s["name"]

    def test_multi_site_realistic_scenario(self) -> None:
        """Realistic scenario: 3 sites each return their own hosters for S05E03.

        hdfilme: VOE + Filemoon (German Dub)
        kinoger: VOE + Streamtape (German Dub)
        streamcloud: SuperVideo + DoodStream (German Sub)

        Expected: 6 streams total, sorted by language then hoster.
        """
        hdfilme = _FakeSeriesPlugin(
            "hdfilme",
            results=[
                self._bb_ep(
                    5,
                    3,
                    _make_hosters("VOE", "Filemoon", language="German Dub"),
                    plugin_name="hdfilme",
                ),
            ],
        )
        kinoger = _FakeSeriesPlugin(
            "kinoger",
            results=[
                self._bb_ep(
                    5,
                    3,
                    _make_hosters("Streamtape", "VOE", language="German Dub"),
                    plugin_name="kinoger",
                ),
            ],
        )
        streamcloud = _FakeSeriesPlugin(
            "streamcloud",
            results=[
                self._bb_ep(
                    5,
                    3,
                    _make_hosters("SuperVideo", "DoodStream", language="German Sub"),
                    plugin_name="streamcloud",
                ),
            ],
            default_language="de",
        )

        app, _ = _make_series_app(
            title_info=self._TITLE,
            plugins={
                "hdfilme": hdfilme,
                "kinoger": kinoger,
                "streamcloud": streamcloud,
            },
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:5:3.json")

        streams = resp.json()["streams"]
        # 5 unique hosters: VOE, Filemoon, Streamtape, SuperVideo, DoodStream
        # (per-hoster dedup removes the duplicate VOE from kinoger)
        assert len(streams) == 5

        # German Dub results should rank before German Sub
        dub_indices = [
            i for i, s in enumerate(streams) if "German Dub" in s["description"]
        ]
        sub_indices = [
            i for i, s in enumerate(streams) if "German Sub" in s["description"]
        ]
        if dub_indices and sub_indices:
            assert max(dub_indices) < min(sub_indices), (
                "German Dub should rank before German Sub"
            )

    def test_massive_spam_capped(self) -> None:
        """A plugin dumping all seasons/episodes is reduced to the target episode."""
        all_results: list[SearchResult] = []
        for season in range(1, 6):
            for ep in range(1, 14):
                all_results.append(
                    self._bb_ep(
                        season,
                        ep,
                        _make_hosters("VOE", "Filemoon", "Streamtape"),
                        plugin_name="spammy",
                    )
                )
        # 5 seasons x 13 eps x 3 hosters = 195 potential streams
        spammy = _FakeSeriesPlugin("spammy", results=all_results)

        app, _ = _make_series_app(title_info=self._TITLE, plugins={"spammy": spammy})
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/{self._IMDB}:3:7.json")

        streams = resp.json()["streams"]
        # Only S03E07 x 3 hosters = 3 streams
        assert len(streams) == 3
        for s in streams:
            assert "S03E07" in s["name"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestSeriesEdgeCases:
    """Edge cases for series stream resolution."""

    _TITLE = TitleMatchInfo(title="Test Series", year=2020)

    def test_partial_id_without_episode_no_filtering(self) -> None:
        """Stremio always sends tt:season:episode (3 parts) for series.

        A 2-part ID like ``tt1234567:2`` is NOT a valid Stremio series format.
        The router falls back to season=None, episode=None, so all results
        pass through the episode filter unfiltered.
        """
        _hosters = ["VOE", "Streamtape", "Filemoon", "DoodStream", "Mixdrop"]
        _domains = [
            "voe.sx",
            "streamtape.com",
            "filemoon.sx",
            "dood.re",
            "mixdrop.ag",
        ]
        results = [
            SearchResult(
                title=f"Test Series S02E{ep:02d}",
                download_link=f"https://{_domains[ep - 1]}/e/s02e{ep:02d}",
                download_links=[
                    {
                        "hoster": _hosters[ep - 1],
                        "link": f"https://{_domains[ep - 1]}/e/s02e{ep:02d}",
                        "language": "German Dub",
                    }
                ],
                category=5000,
                metadata={"source_plugin": "plugin"},
            )
            for ep in range(1, 6)
        ] + [
            SearchResult(
                title="Test Series S01E01",
                download_link="https://vidmoly.me/e/s01e01",
                download_links=[
                    {
                        "hoster": "Vidmoly",
                        "link": "https://vidmoly.me/e/s01e01",
                        "language": "German Dub",
                    }
                ],
                category=5000,
                metadata={"source_plugin": "plugin"},
            ),
        ]
        plugin = _FakeSeriesPlugin("plugin", results=results)

        app, _ = _make_series_app(title_info=self._TITLE, plugins={"plugin": plugin})
        client = TestClient(app)

        # 2-part ID: router parses as season=None, episode=None (no filtering)
        resp = client.get(f"{_PREFIX}/stremio/stream/series/tt1234567:2.json")

        assert resp.status_code == 200
        # All 6 results pass through (different hosters, no episode filter)
        streams = resp.json()["streams"]
        assert len(streams) == 6
        call = plugin.calls[0]
        assert call["season"] is None
        assert call["episode"] is None

    def test_tmdb_id_series(self) -> None:
        """TMDB ID format for series also works."""
        results = [
            SearchResult(
                title="Test Series S01E01",
                download_link="https://voe.sx/e/abc",
                download_links=[
                    {
                        "hoster": "VOE",
                        "link": "https://voe.sx/e/abc",
                        "language": "German Dub",
                    }
                ],
                category=5000,
                metadata={"source_plugin": "plugin"},
            ),
        ]
        plugin = _FakeSeriesPlugin("plugin", results=results)

        app, _ = _make_series_app(title_info=self._TITLE, plugins={"plugin": plugin})
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/tmdb:12345:1:1.json")

        assert resp.status_code == 200
        streams = resp.json()["streams"]
        assert len(streams) == 1

    def test_title_not_found_returns_empty(self) -> None:
        """When TMDB returns no title, response is empty."""
        plugin = _FakeSeriesPlugin("plugin", results=[])

        app, _ = _make_series_app(title_info=None, plugins={"plugin": plugin})
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/tt0000001:1:1.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []
        # Plugin should not have been called
        assert len(plugin.calls) == 0

    def test_high_season_high_episode(self) -> None:
        """High season/episode numbers work correctly (e.g. long-running anime)."""
        results = [
            SearchResult(
                title="One Piece S21E1042",
                download_link="https://voe.sx/e/op",
                download_links=[
                    {
                        "hoster": "VOE",
                        "link": "https://voe.sx/e/op",
                        "language": "German Sub",
                    }
                ],
                category=5070,
                metadata={"source_plugin": "aniworld"},
            ),
            SearchResult(
                title="One Piece S21E1043",
                download_link="https://voe.sx/e/op2",
                download_links=[
                    {
                        "hoster": "VOE",
                        "link": "https://voe.sx/e/op2",
                        "language": "German Sub",
                    }
                ],
                category=5070,
                metadata={"source_plugin": "aniworld"},
            ),
        ]
        plugin = _FakeSeriesPlugin("aniworld", results=results)

        title = TitleMatchInfo(title="One Piece", year=1999)
        app, _ = _make_series_app(title_info=title, plugins={"aniworld": plugin})
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/tt0388629:21:1042.json")

        streams = resp.json()["streams"]
        assert len(streams) == 1
        assert "S21E1042" in streams[0]["name"]
