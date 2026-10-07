"""Tests for when a Stremio request's streams resolve and its answer is due
(``ResolveFlow``).

The tests drive the whole use case through ``execute()`` (factories in
``stremio_support.py``).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase
from scavengarr.domain.entities.stremio import (
    ResolvedStream,
    StreamQuality,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.concurrency import ConcurrencyPool

from .stremio_support import (
    DOOD,
    VOE,
    ClaimSeeing,
    Resolutions,
    answering_use_case,
    cached_links,
    cached_titles,
    eventually,
    fake_site,
    hit,
    hoster_link,
    make_config,
    make_request,
    make_search_result,
    make_use_case,
    memory_cache,
    resolved,
    resolving_use_case,
    video,
)

_BEST = {
    "url": "https://voe.sx/e/best",
    "hoster": "VOE",
    "release": "Iron.Man.2008.German.1080p.BluRay",
}
_SECOND = {
    "url": "https://voe.sx/e/second",
    "hoster": "VOE",
    "release": "Iron.Man.2008.German.720p.WEB",
}
_SLOW = {
    "url": "https://streamtape.com/e/slow",
    "hoster": "Streamtape",
    "release": "Iron.Man.2008.German.1080p.BluRay",
}
_FAST = dict(_SECOND, url="https://voe.sx/e/fast")
_VOE_B = "https://voe.sx/e/other"
_DOOD_B = "https://dood.to/e/other"
_OTHER_TITLE = "tt7654321"


class TestResolverEchoFiltering:
    """Streams whose resolver only validates (echoes the URL) must be skipped."""

    async def test_echo_url_streams_skipped(self) -> None:
        """XFS-style resolver returns same URL → stream excluded from output."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        # One stream with an XFS embed URL (veev)
        sr = make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://veev.to/e/abc123456789", "hoster": "VEEV"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # Resolver echoes the URL back (XFS behaviour)
        async def _echo_resolve(url: str, hoster: str = "") -> ResolvedStream:
            return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)

        repo = AsyncMock()
        uc = make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=AsyncMock(side_effect=_echo_resolve),
        )

        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        # Stream should be skipped — not included in output
        assert len(result) == 0

    async def test_direct_video_url_streams_kept(self) -> None:
        """Resolver extracting a real video URL → stream included."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://voe.sx/e/abc123", "hoster": "VOE"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # Resolver extracts a real HLS video URL
        async def _real_resolve(url: str, hoster: str = "") -> ResolvedStream:
            return ResolvedStream(
                video_url="https://cdn.voe.sx/hls/master.m3u8",
                is_hls=True,
                headers={"Referer": "https://voe.sx/"},
            )

        repo = AsyncMock()
        uc = make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=AsyncMock(side_effect=_real_resolve),
        )

        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert len(result) >= 1
        assert video(uc, result[0]) == "https://cdn.voe.sx/hls/master.m3u8"

    async def test_mixed_streams_only_playable_kept(self) -> None:
        """Mix of echo and real resolvers → only playable streams in output."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://veev.to/e/abc123456789", "hoster": "VEEV"},
                {"url": "https://voe.sx/e/def456", "hoster": "VOE"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # VOE extracts real URL, Veev echoes
        async def _mixed_resolve(url: str, hoster: str = "") -> ResolvedStream:
            if "voe.sx" in url:
                return ResolvedStream(
                    video_url="https://cdn.voe.sx/hls/master.m3u8",
                    is_hls=True,
                    headers={"Referer": "https://voe.sx/"},
                )
            return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)

        repo = AsyncMock()
        uc = make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=AsyncMock(side_effect=_mixed_resolve),
        )

        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        # Only the VOE stream should remain
        assert len(result) == 1
        assert video(uc, result[0]).endswith("master.m3u8")

    async def test_unresolved_streams_dropped_when_resolver_configured(self) -> None:
        """Streams that fail resolution (None) are dropped to avoid 502 proxy."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://unknown-hoster.com/v/abc", "hoster": "UNKNOWN"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        mock_plugin.isolated_search = mock_plugin.search

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_languages.return_value = ["de"]
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # Resolver returns None (no resolver found for hoster)
        repo = AsyncMock()
        uc = make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=AsyncMock(return_value=None),
        )

        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        # Unresolvable streams are dropped — the /play/ proxy would always 502
        assert len(result) == 0


class TestResolvePhase:
    async def test_next_stream_of_a_hoster_replaces_a_failed_one(self) -> None:
        """Dedup happens after resolution: a failing best VOE stream must not
        take the working second VOE stream with it."""

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream | None:
            return None if url.endswith("/best") else resolved(url)

        uc = resolving_use_case([dict(_BEST), dict(_SECOND)], _resolve)

        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert [video(uc, s) for s in result] == ["https://cdn.example/second.mp4"]

    async def test_one_stream_per_hoster(self) -> None:
        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            return resolved(url)

        uc = resolving_use_case([dict(_BEST), dict(_SECOND)], _resolve)

        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert [video(uc, s) for s in result] == ["https://cdn.example/best.mp4"]

    async def test_one_stream_per_hoster_and_language(self) -> None:
        """Dub and sub on the same hoster are different content (anime)."""

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            return resolved(url)

        sub = {
            "url": "https://voe.sx/e/sub",
            "hoster": "VOE",
            "release": "Iron.Man.2008.GERMAN.SUBBED.720p.WEB",
        }
        uc = resolving_use_case([dict(_BEST), dict(_SECOND), sub], _resolve)

        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert [video(uc, s) for s in result] == [
            "https://cdn.example/best.mp4",
            "https://cdn.example/sub.mp4",
        ]

    async def test_further_streams_of_a_resolved_hoster_are_not_resolved(
        self,
    ) -> None:
        """Resolving every candidate at once opens dozens of connections to
        distinct CDNs within a second, which home routers block as a port
        scan; a hoster's next stream is only tried after its best failed."""
        calls: list[str] = []

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            calls.append(url)
            return resolved(url)

        uc = resolving_use_case([dict(_BEST), dict(_SECOND)], _resolve)

        await uc.execute(make_request(), base_url="http://localhost:8080")

        assert calls == [_BEST["url"]]

    async def test_deadline_returns_what_is_resolved(self) -> None:
        async def _resolve(url: str, hoster: str = "") -> ResolvedStream | None:
            if "slow" in url:
                await asyncio.sleep(10)
            return resolved(url)

        uc = resolving_use_case(
            [dict(_SLOW), dict(_FAST)],
            _resolve,
            config=make_config(stream_deadline_seconds=0.2),
        )

        loop = asyncio.get_running_loop()
        start = loop.time()
        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert loop.time() - start < 2
        assert [video(uc, s) for s in result] == ["https://cdn.example/fast.mp4"]

    def test_answer_policy_defaults(self) -> None:
        """5 streams or everything done, at most 60 s, plugins 30 s
        (maintainer's decision after the fifth round)."""
        config = make_config()
        assert config.resolve_target_count == 5
        assert config.stream_deadline_seconds == 60.0
        assert config.plugin_timeout_seconds == 30.0

    async def test_the_answer_goes_out_at_the_target(self) -> None:
        """Titles with many streams waited for the soft deadline and the
        grace (11 s) although 5 streams were there after about 5 s."""

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            if "slow" in url:
                await asyncio.sleep(10)
            return resolved(url)

        uc = resolving_use_case(
            [dict(_SLOW), dict(_FAST)],
            _resolve,
            config=make_config(resolve_target_count=1),
        )

        loop = asyncio.get_running_loop()
        start = loop.time()
        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert loop.time() - start < 2
        assert [video(uc, s) for s in result] == ["https://cdn.example/fast.mp4"]

    async def test_below_the_target_the_answer_waits_for_every_resolution(
        self,
    ) -> None:
        """A title whose only other hoster is slow keeps it."""

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            if "slow" in url:
                await asyncio.sleep(0.3)
            return resolved(url)

        uc = resolving_use_case([dict(_SLOW), dict(_FAST)], _resolve)

        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert sorted(video(uc, s) for s in result) == [
            "https://cdn.example/fast.mp4",
            "https://cdn.example/slow.mp4",
        ]


class TestBackgroundResolutions:
    """Cached answers resolve their other links in the background, one title
    at a time: 17 cached titles asked within seconds started 17 runs at once,
    and 12 Filemoon resolutions queued for the stealth browser's 2 pages
    until their 10 s timeout, which opened its breaker (dev-server
    end-to-end run, 2026-10-05)."""

    _TITLES = {
        "tt1234567": (VOE, DOOD),
        _OTHER_TITLE: (_VOE_B, _DOOD_B),
    }

    def _use_case(
        self, resolutions: Resolutions, **config: object
    ) -> StremioStreamUseCase:
        titles = {
            imdb_id: [hoster_link(url) for url in urls]
            for imdb_id, urls in self._TITLES.items()
        }
        return cached_titles(titles, resolutions, **config)

    async def _ask_both(self, uc: StremioStreamUseCase) -> None:
        for imdb_id in self._TITLES:
            await uc.execute(
                make_request(imdb_id=imdb_id), base_url="http://localhost:8080"
            )

    async def test_one_title_resolves_in_the_background_at_a_time(self) -> None:
        resolutions = Resolutions(alive=(VOE, _VOE_B), delay=0.2)
        uc = self._use_case(resolutions)

        await self._ask_both(uc)

        await eventually(lambda: {DOOD, _DOOD_B} <= resolutions.store.keys())
        assert resolutions.most_at_once == 1

    async def test_a_waiting_title_gets_its_whole_deadline(self) -> None:
        """Counted from its request, the second title's 0.5 s would end 0.2 s
        after the first title's resolution, before its own (0.3 s)."""
        resolutions = Resolutions(alive=(VOE, _VOE_B), delay=0.3)
        uc = self._use_case(resolutions, stream_deadline_seconds=0.5)

        await self._ask_both(uc)

        await eventually(lambda: _DOOD_B in resolutions.store)

    async def test_they_claim_browser_pages_as_background_work(self) -> None:
        resolutions = ClaimSeeing(alive=(VOE, _VOE_B))
        uc = self._use_case(resolutions)

        await self._ask_both(uc)

        await eventually(lambda: DOOD in resolutions.store)
        claim = resolutions.claims[DOOD]
        assert claim is not None
        assert claim.kind == "background"


class TestAnswerPolicy:
    """The answer goes out at 5 streams, when the search and every
    resolution are done, at the latest at the deadline; it no longer waits
    for the soft deadline (7 s) and the grace (4 s)."""

    async def test_the_answers_resolutions_claim_captures_due_at_the_deadline(
        self,
    ) -> None:
        resolutions = ClaimSeeing()
        sites = {"a": fake_site([hit("https://voe.sx/e/1")])}
        uc = answering_use_case(
            sites, memory_cache(), resolutions, stream_deadline_seconds=7.0
        )
        before = time.monotonic()

        await uc.execute(make_request(), base_url="http://localhost:8080")

        claim = resolutions.claims["https://voe.sx/e/1"]
        assert claim is not None
        assert claim.kind == "capture"
        assert before + 7.0 <= claim.due <= time.monotonic() + 7.0

    async def test_at_the_target_while_a_slow_plugin_still_searches(
        self,
    ) -> None:
        cache = memory_cache()
        sites = {
            "fast": fake_site([hit("https://voe.sx/e/fast")]),
            "slow": fake_site([hit("https://dood.to/e/slow")], delay=0.5),
        }
        uc = answering_use_case(sites, cache, Resolutions(), resolve_target_count=1)

        started = time.monotonic()
        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert time.monotonic() - started < 0.4
        assert [video(uc, s) for s in streams] == ["https://cdn.example/fast.mp4"]
        # The slow plugin goes on and its results reach the cache
        await eventually(lambda: len(cached_links(cache)) == 2)

    async def test_links_resolve_while_the_search_runs(self) -> None:
        """The fast plugin's link is resolved before the slow one answers."""
        resolutions = Resolutions(delay=0.05)
        # The slow plugin answers once the fast link resolved, not after a
        # fixed 0.3 s: on a busy machine (a full parallel run) the fast
        # resolution took longer than that
        slow_may_answer = asyncio.Event()

        async def _slow(*_args: object, **_kwargs: object) -> list[SearchResult]:
            await slow_may_answer.wait()
            return [hit("https://dood.to/e/slow")]

        slow = fake_site([])
        slow.isolated_search = AsyncMock(side_effect=_slow)
        sites = {"fast": fake_site([hit("https://voe.sx/e/fast")]), "slow": slow}
        uc = answering_use_case(sites, memory_cache(), resolutions)
        request = asyncio.create_task(
            uc.execute(make_request(), base_url="http://localhost:8080")
        )

        await eventually(lambda: "https://voe.sx/e/fast" in resolutions.store)
        slow_may_answer.set()
        streams = await request

        assert sorted(video(uc, s) for s in streams) == [
            "https://cdn.example/fast.mp4",
            "https://cdn.example/slow.mp4",
        ]

    async def test_when_everything_is_done_below_the_target(self) -> None:
        sites = {
            "a": fake_site([hit("https://voe.sx/e/1")]),
            "b": fake_site([hit("https://dood.to/e/2")], delay=0.2),
        }
        uc = answering_use_case(sites, memory_cache(), Resolutions())

        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert sorted(video(uc, s) for s in streams) == [
            "https://cdn.example/1.mp4",
            "https://cdn.example/2.mp4",
        ]

    async def test_at_the_deadline_with_what_is_resolved(self) -> None:
        sites = {
            "fast": fake_site([hit("https://voe.sx/e/fast")]),
            "stuck": fake_site([], delay=30),
        }
        uc = answering_use_case(
            sites,
            memory_cache(),
            Resolutions(),
            plugin_timeout_seconds=10.0,
            stream_deadline_seconds=0.3,
        )

        started = time.monotonic()
        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert 0.25 < time.monotonic() - started < 1.5
        assert [video(uc, s) for s in streams] == ["https://cdn.example/fast.mp4"]
        await uc.aclose()

    async def test_at_the_budget_with_the_results_so_far(self) -> None:
        """One slot: the second plugin starts after the budget. The answer
        goes out at the budget with the first plugin's stream, the second
        plugin runs on into the cache (continue-cut-searches)."""
        cache = memory_cache()
        sites = {
            "first": fake_site([hit("https://voe.sx/e/first")], delay=0.3),
            "second": fake_site([hit("https://dood.to/e/second")], delay=0.2),
        }
        uc = answering_use_case(
            sites,
            cache,
            Resolutions(),
            plugin_timeout_seconds=0.4,
            stream_deadline_seconds=2.0,
            pool=ConcurrencyPool(httpx_slots=1, pw_slots=1),
        )

        started = time.monotonic()
        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert 0.3 <= time.monotonic() - started < 0.7
        assert [video(uc, s) for s in streams] == ["https://cdn.example/first.mp4"]
        await eventually(lambda: len(cached_links(cache)) == 2)

    async def test_a_retry_past_the_budget_answers_at_once(self) -> None:
        cache = memory_cache()
        sites = {
            "first": fake_site([hit("https://voe.sx/e/first")], delay=0.3),
            "second": fake_site([hit("https://dood.to/e/second")], delay=0.3),
        }
        uc = answering_use_case(
            sites,
            cache,
            Resolutions(),
            plugin_timeout_seconds=0.4,
            stream_deadline_seconds=2.0,
            pool=ConcurrencyPool(httpx_slots=1, pw_slots=1),
        )
        await uc.execute(make_request(), base_url="http://localhost:8080")

        started = time.monotonic()
        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert time.monotonic() - started < 0.1
        assert [video(uc, s) for s in streams] == ["https://cdn.example/first.mp4"]
        await eventually(lambda: len(cached_links(cache)) == 2)

    async def test_requests_on_one_search_both_answer_at_the_target(self) -> None:
        cache = memory_cache()
        fast = fake_site([hit("https://voe.sx/e/fast")])
        sites = {"fast": fast, "slow": fake_site([], delay=0.5)}
        uc = answering_use_case(sites, cache, Resolutions(), resolve_target_count=1)

        started = time.monotonic()
        first, second = await asyncio.gather(
            uc.execute(make_request(), base_url="http://localhost:8080"),
            uc.execute(make_request(), base_url="http://localhost:8080"),
        )

        assert time.monotonic() - started < 0.4
        assert [video(uc, s) for s in first] == ["https://cdn.example/fast.mp4"]
        assert [video(uc, s) for s in second] == ["https://cdn.example/fast.mp4"]
        assert fast.isolated_search.await_count == 1
        await uc.aclose()
