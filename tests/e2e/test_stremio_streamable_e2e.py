"""E2E tests verifying the Stremio stream pipeline produces streamable links.

Every stream URL returned to Stremio must be genuinely playable — either:
  1. A direct video URL (.mp4/.m3u8) with ``behaviorHints`` (proxyHeaders), or
  2. A ``/play/{id}`` proxy URL that resolves on-demand.

Tests exercise the full pipeline:
    HTTP → TMDB mock → plugin search → convert → sort → dedup → resolve → hints

All real components are used except external I/O:
  - Real: StremioStreamUseCase, StreamSorter, stream_converter, title_matcher,
          episode filter, ConcurrencyPool, PluginCircuitBreaker.
  - Mocked: TmdbClientPort, PluginRegistryPort, SearchEnginePort,
            StreamLinkRepository, resolve_fn.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase
from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    ResolvedStream,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.circuit_breaker import PluginCircuitBreaker
from scavengarr.infrastructure.concurrency import ConcurrencyPool
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
# Fake plugin (supports both search() and isolated_search())
# ---------------------------------------------------------------------------


class _FakeStreamPlugin:
    """Configurable fake plugin for streamable-link E2E tests."""

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

    async def isolated_search(
        self,
        query: str,
        category: int | None = None,
        *,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        return await self.search(query, category, season=season, episode=episode)


class _CrashingPlugin(_FakeStreamPlugin):
    """Plugin that always raises."""

    async def search(self, *_a: Any, **_kw: Any) -> list[SearchResult]:
        raise RuntimeError("site is down")


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _make_registry(
    plugins: dict[str, _FakeStreamPlugin],
) -> MagicMock:
    """Build a PluginRegistryPort mock with explicit language/mode returns."""
    names = sorted(plugins.keys())
    registry = MagicMock()
    registry.get_by_provides.side_effect = lambda p: names if p == "stream" else []
    registry.get.side_effect = lambda n: plugins[n]
    registry.get_languages.return_value = ["de"]
    registry.get_mode.return_value = "httpx"
    return registry


def _make_streamable_app(
    *,
    title_info: TitleMatchInfo | None,
    plugins: dict[str, _FakeStreamPlugin],
    resolve_fn: Any | None = None,
    circuit_breaker: PluginCircuitBreaker | None = None,
    config: StremioConfig | None = None,
    validate_passthrough: bool = True,
    pool: ConcurrencyPool | None = None,
) -> FastAPI:
    """Build a FastAPI app with a real StremioStreamUseCase.

    Accepts optional resolve_fn, circuit_breaker, and config overrides.
    """
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(return_value=title_info)
    if title_info:
        tmdb.get_title_by_tmdb_id = AsyncMock(return_value=title_info.title)
    else:
        tmdb.get_title_by_tmdb_id = AsyncMock(return_value=None)

    registry = _make_registry(plugins)

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
        resolve_fn=resolve_fn,
        pool=pool or ConcurrencyPool(),
        circuit_breaker=circuit_breaker,
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

    return app


# ---------------------------------------------------------------------------
# SearchResult factories
# ---------------------------------------------------------------------------


def _movie_result(
    title: str = "Test Movie",
    hoster: str = "VOE",
    domain: str = "voe.sx",
    *,
    file_id: str = "abc123",
    language: str = "German Dub",
    quality: str = "1080p",
    plugin_name: str = "hdfilme",
) -> SearchResult:
    """Build a movie SearchResult with a single hoster link."""
    url = f"https://{domain}/e/{file_id}"
    return SearchResult(
        title=title,
        download_link=url,
        download_links=[
            {
                "hoster": hoster,
                "link": url,
                "language": language,
                "quality": quality,
            },
        ],
        category=2000,
        metadata={"source_plugin": plugin_name},
    )


def _series_result(
    title: str,
    season: int,
    episode: int,
    hoster: str = "VOE",
    domain: str = "voe.sx",
    *,
    file_id: str = "ep1",
    language: str = "German Dub",
    quality: str = "720p",
    plugin_name: str = "aniworld",
) -> SearchResult:
    """Build a series SearchResult for a specific episode."""
    ep_title = f"{title} S{season:02d}E{episode:02d}"
    url = f"https://{domain}/e/{file_id}"
    return SearchResult(
        title=ep_title,
        download_link=url,
        download_links=[
            {
                "hoster": hoster,
                "link": url,
                "language": language,
                "quality": quality,
            },
        ],
        category=5070,
        metadata={"source_plugin": plugin_name},
    )


# ---------------------------------------------------------------------------
# Resolve function factories
# ---------------------------------------------------------------------------


async def _resolve_to_mp4(url: str, hoster: str) -> ResolvedStream:
    """Resolve to a direct .mp4 video URL with Referer header."""
    return ResolvedStream(
        video_url=f"https://cdn.{hoster}.sx/delivery/video.mp4",
        headers={"Referer": f"https://{hoster}.sx/"},
        is_hls=False,
    )


async def _resolve_to_hls(url: str, hoster: str) -> ResolvedStream:
    """Resolve to a .m3u8 HLS playlist URL."""
    return ResolvedStream(
        video_url=f"https://cdn.{hoster}.sx/hls/master.m3u8",
        headers={"Referer": f"https://{hoster}.sx/"},
        is_hls=True,
    )


async def _resolve_echo(url: str, hoster: str) -> ResolvedStream:
    """Echo back the original embed URL (NOT streamable)."""
    return ResolvedStream(video_url=url, is_hls=False)


async def _resolve_fail(url: str, hoster: str) -> ResolvedStream | None:
    """Simulate resolver failure."""
    return None


# ---------------------------------------------------------------------------
# Central streamable assertion
# ---------------------------------------------------------------------------


def _assert_streamable(stream: dict[str, Any]) -> None:
    """Assert that a stream dict represents a genuinely playable URL.

    A stream is considered streamable if EITHER:
      1. URL is a direct video (.mp4/.m3u8/etc.) with behaviorHints, OR
      2. URL is a /play/ proxy endpoint.
    """
    url = stream["url"]
    hints = stream.get("behaviorHints")

    is_proxy = "/play/" in url
    is_video = any(
        ext in url.lower()
        for ext in (".mp4", ".m3u8", ".mkv", ".ts", ".webm")
    )

    if is_proxy:
        # Proxy URLs don't need behaviorHints (resolved on-demand)
        return

    if is_video:
        # Direct video URLs MUST have behaviorHints with proxyHeaders
        assert hints is not None, f"Direct video URL missing behaviorHints: {url}"
        assert hints.get("notWebReady") is True, "notWebReady should be True"
        proxy_headers = hints.get("proxyHeaders", {})
        request_headers = proxy_headers.get("request", {})
        assert "User-Agent" in request_headers, "Missing User-Agent in proxyHeaders"
        return

    # Neither proxy nor recognizable video → not streamable
    msg = f"Stream URL is neither a /play/ proxy nor a direct video URL: {url}"
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Movie stream resolution tests
# ---------------------------------------------------------------------------


class TestMovieStreamableResolution:
    """Verify movie streams produce genuinely streamable URLs."""

    _TITLE = TitleMatchInfo(title="Iron Man", year=2008)

    def test_resolve_produces_mp4_urls(self) -> None:
        """All streams should have .mp4 video URLs with behaviorHints."""
        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(title="Iron Man", plugin_name="hdfilme"),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        streams = resp.json()["streams"]
        assert len(streams) >= 1
        for s in streams:
            _assert_streamable(s)
            assert ".mp4" in s["url"]

    def test_resolve_produces_hls_urls(self) -> None:
        """All streams should have .m3u8 HLS URLs with behaviorHints."""
        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(title="Iron Man", plugin_name="hdfilme"),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_to_hls,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        assert len(streams) >= 1
        for s in streams:
            _assert_streamable(s)
            assert ".m3u8" in s["url"]

    def test_resolve_mixed_mp4_and_hls(self) -> None:
        """Both MP4 and HLS work in same response."""
        call_count = 0

        async def _alternating_resolve(url: str, hoster: str) -> ResolvedStream:
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                return await _resolve_to_hls(url, hoster)
            return await _resolve_to_mp4(url, hoster)

        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(
                    title="Iron Man",
                    hoster="VOE",
                    domain="voe.sx",
                    file_id="v1",
                    plugin_name="hdfilme",
                ),
                _movie_result(
                    title="Iron Man",
                    hoster="Filemoon",
                    domain="filemoon.sx",
                    file_id="f1",
                    plugin_name="hdfilme",
                ),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=_alternating_resolve,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        assert len(streams) >= 1
        for s in streams:
            _assert_streamable(s)

    def test_resolve_none_excludes_stream(self) -> None:
        """When resolve_fn returns None, stream is excluded entirely."""
        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(title="Iron Man", plugin_name="hdfilme"),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_fail,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_resolve_echo_excludes_stream(self) -> None:
        """Echoed embed URL is filtered by _is_direct_video_url()."""
        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(title="Iron Man", plugin_name="hdfilme"),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_echo,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_resolve_partial_echo_partial_video(self) -> None:
        """1 echo + 2 real videos -> only 2 streams returned."""
        call_idx = 0

        async def _partial_resolve(
            url: str, hoster: str
        ) -> ResolvedStream | None:
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                # First call: echo (not streamable)
                return ResolvedStream(video_url=url, is_hls=False)
            # Others: real video
            return await _resolve_to_mp4(url, hoster)

        results = [
            _movie_result(
                title="Iron Man",
                hoster=h,
                domain=f"{h.lower()}.sx",
                file_id=f"id{i}",
                plugin_name="hdfilme",
            )
            for i, h in enumerate(["VOE", "Filemoon", "Streamtape"])
        ]
        plugin = _FakeStreamPlugin("hdfilme", results=results)
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=_partial_resolve,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        assert len(streams) == 2
        for s in streams:
            _assert_streamable(s)

    def test_no_resolve_fn_produces_proxy_urls(self) -> None:
        """Without resolve_fn, all URLs are /play/{id} proxy URLs."""
        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(title="Iron Man", plugin_name="hdfilme"),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=None,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        assert len(streams) >= 1
        for s in streams:
            _assert_streamable(s)
            assert "/api/v1/stremio/play/" in s["url"]

    def test_behavior_hints_have_user_agent(self) -> None:
        """proxyHeaders.request.User-Agent must be present on resolved streams."""
        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(title="Iron Man", plugin_name="hdfilme"),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        assert len(streams) >= 1
        for s in streams:
            hints = s["behaviorHints"]
            ua = hints["proxyHeaders"]["request"]["User-Agent"]
            assert ua and len(ua) > 10

    def test_behavior_hints_have_referer(self) -> None:
        """proxyHeaders.request.Referer must be present when resolver sets it."""
        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(title="Iron Man", plugin_name="hdfilme"),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        assert len(streams) >= 1
        for s in streams:
            referer = s["behaviorHints"]["proxyHeaders"]["request"]["Referer"]
            assert referer.startswith("https://")

    def test_dedup_keeps_best_per_hoster(self) -> None:
        """2 plugins returning same hoster -> only 1 stream (dedup)."""
        plugin_a = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(
                    title="Iron Man",
                    hoster="VOE",
                    domain="voe.sx",
                    file_id="a1",
                    plugin_name="hdfilme",
                ),
            ],
        )
        plugin_b = _FakeStreamPlugin(
            "kinoger",
            results=[
                _movie_result(
                    title="Iron Man",
                    hoster="VOE",
                    domain="voe.sx",
                    file_id="b1",
                    plugin_name="kinoger",
                ),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin_a, "kinoger": plugin_b},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        assert len(streams) == 1
        _assert_streamable(streams[0])

    def test_not_web_ready_flag(self) -> None:
        """behaviorHints.notWebReady must be True for resolved streams."""
        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(title="Iron Man", plugin_name="hdfilme"),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        assert len(streams) >= 1
        for s in streams:
            assert s["behaviorHints"]["notWebReady"] is True


# ---------------------------------------------------------------------------
# Series stream resolution tests
# ---------------------------------------------------------------------------


class TestSeriesStreamableResolution:
    """Verify series streams produce genuinely streamable URLs."""

    _TITLE = TitleMatchInfo(title="Breaking Bad", year=2008)
    _IMDB = "tt0903747"

    def test_series_correct_episode_filtered(self) -> None:
        """Only matching S01E05 streams returned, all streamable."""
        results = [
            _series_result(
                "Breaking Bad", 1, ep, hoster="VOE", file_id=f"ep{ep}",
                plugin_name="hdfilme",
            )
            for ep in range(1, 11)
        ]
        plugin = _FakeStreamPlugin("hdfilme", results=results)
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:5.json"
        )

        streams = resp.json()["streams"]
        assert len(streams) == 1
        assert "S01E05" in streams[0]["name"]
        _assert_streamable(streams[0])

    def test_series_multi_plugin_resolved(self) -> None:
        """2 plugins with different hosters, all streamable."""
        plugin_a = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _series_result(
                    "Breaking Bad", 5, 3, hoster="VOE", domain="voe.sx",
                    file_id="a1", plugin_name="hdfilme",
                ),
            ],
        )
        plugin_b = _FakeStreamPlugin(
            "kinoger",
            results=[
                _series_result(
                    "Breaking Bad", 5, 3, hoster="Filemoon", domain="filemoon.sx",
                    file_id="b1", plugin_name="kinoger",
                ),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin_a, "kinoger": plugin_b},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/stream/series/{self._IMDB}:5:3.json"
        )

        streams = resp.json()["streams"]
        assert len(streams) == 2
        for s in streams:
            assert "S05E03" in s["name"]
            _assert_streamable(s)

    def test_series_resolve_filters_dead_hosters(self) -> None:
        """resolve_fn returning None for some -> only resolved returned."""
        call_idx = 0

        async def _resolve_every_other(
            url: str, hoster: str
        ) -> ResolvedStream | None:
            nonlocal call_idx
            call_idx += 1
            if call_idx % 2 == 0:
                return None
            return await _resolve_to_mp4(url, hoster)

        results = [
            _series_result(
                "Breaking Bad", 1, 5, hoster=h, domain=f"{h.lower()}.sx",
                file_id=f"id{i}", plugin_name="hdfilme",
            )
            for i, h in enumerate(["VOE", "Filemoon", "Streamtape"])
        ]
        plugin = _FakeStreamPlugin("hdfilme", results=results)
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_every_other,
        )
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/stream/series/{self._IMDB}:1:5.json"
        )

        streams = resp.json()["streams"]
        # Some resolved, some filtered — all remaining are streamable
        assert len(streams) >= 1
        for s in streams:
            _assert_streamable(s)

    def test_series_high_episode_number(self) -> None:
        """High S/E numbers work (One Piece style)."""
        title = TitleMatchInfo(title="One Piece", year=1999)
        results = [
            _series_result(
                "One Piece", 21, 1042, hoster="VOE", file_id="op1042",
                plugin_name="aniworld",
            ),
            _series_result(
                "One Piece", 21, 1043, hoster="VOE", file_id="op1043",
                plugin_name="aniworld",
            ),
        ]
        plugin = _FakeStreamPlugin("aniworld", results=results)
        app = _make_streamable_app(
            title_info=title,
            plugins={"aniworld": plugin},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(
            f"{_PREFIX}/stremio/stream/series/tt0388629:21:1042.json"
        )

        streams = resp.json()["streams"]
        assert len(streams) == 1
        assert "S21E1042" in streams[0]["name"]
        _assert_streamable(streams[0])


# ---------------------------------------------------------------------------
# Circuit breaker E2E tests
# ---------------------------------------------------------------------------


class TestCircuitBreakerE2E:
    """Verify circuit breaker integration with the stream pipeline."""

    _TITLE = TitleMatchInfo(title="Iron Man", year=2008)

    def test_open_circuit_skips_plugin(self) -> None:
        """Pre-opened circuit -> plugin not called, other plugin produces streams."""
        cb = PluginCircuitBreaker(failure_threshold=1, cooldown_seconds=3600)
        # Open the circuit for "bad_plugin"
        cb.record_failure("bad_plugin")

        bad = _FakeStreamPlugin(
            "bad_plugin",
            results=[
                _movie_result(title="Iron Man", plugin_name="bad_plugin"),
            ],
        )
        good = _FakeStreamPlugin(
            "good_plugin",
            results=[
                _movie_result(
                    title="Iron Man",
                    hoster="Filemoon",
                    domain="filemoon.sx",
                    file_id="g1",
                    plugin_name="good_plugin",
                ),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"bad_plugin": bad, "good_plugin": good},
            resolve_fn=_resolve_to_mp4,
            circuit_breaker=cb,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        assert len(streams) >= 1
        for s in streams:
            _assert_streamable(s)
        # Bad plugin should not have been called
        assert len(bad.calls) == 0
        assert len(good.calls) >= 1

    def test_circuit_does_not_affect_good_plugins(self) -> None:
        """Open circuit on one, two others still searched."""
        cb = PluginCircuitBreaker(failure_threshold=1, cooldown_seconds=3600)
        cb.record_failure("broken")

        broken = _FakeStreamPlugin("broken", results=[])
        good_a = _FakeStreamPlugin(
            "plugin_a",
            results=[
                _movie_result(
                    title="Iron Man", hoster="VOE", file_id="a1",
                    plugin_name="plugin_a",
                ),
            ],
        )
        good_b = _FakeStreamPlugin(
            "plugin_b",
            results=[
                _movie_result(
                    title="Iron Man", hoster="Filemoon", domain="filemoon.sx",
                    file_id="b1", plugin_name="plugin_b",
                ),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"broken": broken, "plugin_a": good_a, "plugin_b": good_b},
            resolve_fn=_resolve_to_mp4,
            circuit_breaker=cb,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        assert len(streams) == 2
        for s in streams:
            _assert_streamable(s)

    def test_all_circuits_open_returns_empty(self) -> None:
        """All plugins open -> empty (not error)."""
        cb = PluginCircuitBreaker(failure_threshold=1, cooldown_seconds=3600)
        cb.record_failure("plugin_a")
        cb.record_failure("plugin_b")

        plugins = {
            "plugin_a": _FakeStreamPlugin(
                "plugin_a",
                results=[_movie_result(title="Iron Man", plugin_name="plugin_a")],
            ),
            "plugin_b": _FakeStreamPlugin(
                "plugin_b",
                results=[_movie_result(title="Iron Man", plugin_name="plugin_b")],
            ),
        }
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins=plugins,
            resolve_fn=_resolve_to_mp4,
            circuit_breaker=cb,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []


# ---------------------------------------------------------------------------
# Concurrency pool E2E tests
# ---------------------------------------------------------------------------


class TestConcurrencyPoolE2E:
    """Verify ConcurrencyPool integration with the stream pipeline."""

    _TITLE = TitleMatchInfo(title="Iron Man", year=2008)

    def test_pool_with_tight_slots(self) -> None:
        """httpx_slots=1 -> plugins still searched (serially), results returned."""
        pool = ConcurrencyPool(httpx_slots=1, pw_slots=1)
        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(title="Iron Man", plugin_name="hdfilme"),
            ],
        )
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_to_mp4,
            pool=pool,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        assert len(streams) >= 1
        for s in streams:
            _assert_streamable(s)

    def test_pool_does_not_deadlock(self) -> None:
        """Multiple plugins with pool -> all complete without deadlock."""
        pool = ConcurrencyPool(httpx_slots=2, pw_slots=1)
        plugins = {
            f"plugin_{i}": _FakeStreamPlugin(
                f"plugin_{i}",
                results=[
                    _movie_result(
                        title="Iron Man",
                        hoster=h,
                        domain=f"{h.lower()}.sx",
                        file_id=f"id{i}",
                        plugin_name=f"plugin_{i}",
                    ),
                ],
            )
            for i, h in enumerate(["VOE", "Filemoon", "Streamtape"])
        }
        app = _make_streamable_app(
            title_info=self._TITLE,
            plugins=plugins,
            resolve_fn=_resolve_to_mp4,
            pool=pool,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        streams = resp.json()["streams"]
        assert len(streams) >= 1
        for s in streams:
            _assert_streamable(s)


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestStreamableEdgeCases:
    """Edge cases that must return empty or handle gracefully."""

    def test_no_matching_title_returns_empty(self) -> None:
        """TMDB returns None -> empty streams."""
        plugin = _FakeStreamPlugin("hdfilme", results=[])
        app = _make_streamable_app(
            title_info=None,
            plugins={"hdfilme": plugin},
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0000001.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_all_plugins_error_returns_empty(self) -> None:
        """Every plugin raises -> empty streams, 200 status."""
        title = TitleMatchInfo(title="Test Movie", year=2024)
        plugins = {
            "broken_a": _CrashingPlugin("broken_a", results=[]),
            "broken_b": _CrashingPlugin("broken_b", results=[]),
        }
        app = _make_streamable_app(
            title_info=title,
            plugins=plugins,
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt1234567.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_all_resolvers_fail_returns_empty(self) -> None:
        """resolve_fn always None -> empty streams."""
        title = TitleMatchInfo(title="Iron Man", year=2008)
        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(title="Iron Man", plugin_name="hdfilme"),
            ],
        )
        app = _make_streamable_app(
            title_info=title,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_fail,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_all_resolvers_echo_returns_empty(self) -> None:
        """resolve_fn always echoes -> empty streams."""
        title = TitleMatchInfo(title="Iron Man", year=2008)
        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(title="Iron Man", plugin_name="hdfilme"),
            ],
        )
        app = _make_streamable_app(
            title_info=title,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_echo,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_empty_download_links(self) -> None:
        """SearchResult with download_links=[] -> no crash."""
        title = TitleMatchInfo(title="Iron Man", year=2008)
        result = SearchResult(
            title="Iron Man",
            download_link="https://voe.sx/e/abc",
            download_links=[],
            category=2000,
            metadata={"source_plugin": "hdfilme"},
        )
        plugin = _FakeStreamPlugin("hdfilme", results=[result])
        app = _make_streamable_app(
            title_info=title,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        # Empty download_links means no RankedStreams are created
        assert resp.status_code == 200

    def test_plugin_returns_no_results(self) -> None:
        """Plugin returns [] -> empty streams."""
        title = TitleMatchInfo(title="Iron Man", year=2008)
        plugin = _FakeStreamPlugin("hdfilme", results=[])
        app = _make_streamable_app(
            title_info=title,
            plugins={"hdfilme": plugin},
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        assert resp.json()["streams"] == []

    def test_mixed_success_and_failure(self) -> None:
        """1 good plugin + 1 failing -> streams from good only."""
        title = TitleMatchInfo(title="Iron Man", year=2008)
        good = _FakeStreamPlugin(
            "good",
            results=[
                _movie_result(title="Iron Man", plugin_name="good"),
            ],
        )
        bad = _CrashingPlugin("bad", results=[])
        app = _make_streamable_app(
            title_info=title,
            plugins={"bad": bad, "good": good},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")

        assert resp.status_code == 200
        streams = resp.json()["streams"]
        assert len(streams) >= 1
        for s in streams:
            _assert_streamable(s)


# ---------------------------------------------------------------------------
# Full pipeline tests
# ---------------------------------------------------------------------------


class TestFullPipelineStreamable:
    """Complete pipeline verification: every stream passes _assert_streamable()."""

    def test_movie_full_pipeline(self) -> None:
        """Complete: HTTP -> TMDB -> plugin -> convert -> sort -> dedup -> resolve -> hints."""
        title = TitleMatchInfo(title="Interstellar", year=2014)
        results = [
            _movie_result(
                title="Interstellar",
                hoster=h,
                domain=f"{h.lower()}.sx",
                file_id=f"id{i}",
                quality=q,
                plugin_name="hdfilme",
            )
            for i, (h, q) in enumerate([
                ("VOE", "1080p"),
                ("Filemoon", "720p"),
                ("Streamtape", "1080p"),
            ])
        ]
        plugin = _FakeStreamPlugin("hdfilme", results=results)
        app = _make_streamable_app(
            title_info=title,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0816692.json")

        assert resp.status_code == 200
        streams = resp.json()["streams"]
        assert len(streams) == 3
        for s in streams:
            _assert_streamable(s)
            assert "Interstellar" in s["name"]

    def test_series_full_pipeline(self) -> None:
        """Complete series flow with episode filtering."""
        title = TitleMatchInfo(title="Dark", year=2017)
        results = [
            _series_result(
                "Dark", 1, ep, hoster="VOE", file_id=f"dark_ep{ep}",
                plugin_name="hdfilme",
            )
            for ep in range(1, 11)
        ]
        plugin = _FakeStreamPlugin("hdfilme", results=results)
        app = _make_streamable_app(
            title_info=title,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/series/tt5753856:1:3.json")

        assert resp.status_code == 200
        streams = resp.json()["streams"]
        assert len(streams) == 1
        assert "S01E03" in streams[0]["name"]
        _assert_streamable(streams[0])

    def test_multi_hoster_all_resolved(self) -> None:
        """5 different hosters, all resolved -> 5 streamable streams."""
        title = TitleMatchInfo(title="Dune", year=2021)
        hosters = [
            ("VOE", "voe.sx"),
            ("Filemoon", "filemoon.sx"),
            ("Streamtape", "streamtape.com"),
            ("DoodStream", "dood.re"),
            ("SuperVideo", "supervideo.tv"),
        ]
        results = [
            _movie_result(
                title="Dune",
                hoster=h,
                domain=d,
                file_id=f"dune{i}",
                plugin_name="hdfilme",
            )
            for i, (h, d) in enumerate(hosters)
        ]
        plugin = _FakeStreamPlugin("hdfilme", results=results)
        app = _make_streamable_app(
            title_info=title,
            plugins={"hdfilme": plugin},
            resolve_fn=_resolve_to_mp4,
        )
        client = TestClient(app)

        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt1160419.json")

        assert resp.status_code == 200
        streams = resp.json()["streams"]
        assert len(streams) == 5
        for s in streams:
            _assert_streamable(s)

    def test_proxy_play_full_roundtrip(self) -> None:
        """Stream endpoint -> proxy URLs -> play endpoint -> 302 to video."""
        title = TitleMatchInfo(title="Iron Man", year=2008)
        plugin = _FakeStreamPlugin(
            "hdfilme",
            results=[
                _movie_result(title="Iron Man", plugin_name="hdfilme"),
            ],
        )
        # No resolve_fn -> produces proxy /play/ URLs
        app = _make_streamable_app(
            title_info=title,
            plugins={"hdfilme": plugin},
            resolve_fn=None,
        )

        # Wire up play endpoint dependencies
        resolved = ResolvedStream(
            video_url="https://cdn.voe.sx/delivery/video.mp4",
            is_hls=False,
        )
        play_repo = AsyncMock()
        play_registry = AsyncMock()
        play_registry.resolve = AsyncMock(return_value=resolved)
        app.state.stream_link_repo = play_repo
        app.state.hoster_resolver_registry = play_registry

        client = TestClient(app)

        # Step 1: Get stream list
        resp = client.get(f"{_PREFIX}/stremio/stream/movie/tt0371746.json")
        streams = resp.json()["streams"]
        assert len(streams) >= 1
        stream_url = streams[0]["url"]
        assert "/api/v1/stremio/play/" in stream_url

        # Step 2: Extract stream ID and set up the play repo mock
        stream_id = stream_url.split("/play/")[-1]
        link = CachedStreamLink(
            stream_id=stream_id,
            hoster_url="https://voe.sx/e/abc123",
            title="Iron Man",
            hoster="voe",
        )
        play_repo.get = AsyncMock(return_value=link)

        # Step 3: Hit the play endpoint
        play_client = TestClient(app, follow_redirects=False)
        play_resp = play_client.get(f"{_PREFIX}/stremio/play/{stream_id}")

        assert play_resp.status_code == 302
        assert play_resp.headers["location"] == "https://cdn.voe.sx/delivery/video.mp4"
