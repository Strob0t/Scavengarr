"""Tests for the streams of a Stremio answer (``answer.py``): ranking, the
measured quality, stream links and proxy URLs.

Most tests drive the whole use case through ``execute()`` (factories in
``stremio_support.py``).
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from scavengarr.application.stremio.answer import rank_streams
from scavengarr.application.use_cases.stremio_stream import StremioStreamUseCase
from scavengarr.domain.entities.stremio import (
    CachedStreamLink,
    RankedStream,
    ResolvedStream,
    StreamQuality,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult

from .stremio_support import (
    VOE,
    Resolutions,
    from_cache,
    hoster_link,
    make_request,
    make_search_result,
    make_use_case,
    resolved,
    resolving_use_case,
    video,
)


class TestStreamLinkProxy:
    async def test_proxy_urls_generated_with_base_url(self) -> None:
        """When stream_link_repo and base_url are provided, URLs are proxied."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/abc"}],
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

        repo = AsyncMock()
        uc = make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
        )
        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert len(result) >= 1
        assert result[0].url.startswith("http://localhost:8080/api/v1/stremio/play/")
        assert "voe.sx" not in result[0].url
        # The play link keeps the stream's hints (autoplay of the next episode)
        assert result[0].behavior_hints is not None
        assert result[0].behavior_hints["bingeGroup"].startswith("scavengarr|")
        repo.save.assert_awaited()

    async def test_no_proxy_without_base_url(self) -> None:
        """Without base_url, original hoster URLs are returned."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/abc"}],
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

        repo = AsyncMock()
        uc = make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
        )
        # No base_url → no proxying
        result = await uc.execute(make_request())

        assert len(result) >= 1
        assert result[0].url == "https://voe.sx/e/abc"
        repo.save.assert_not_awaited()

    async def test_no_proxy_without_repo(self) -> None:
        """Without stream_link_repo, original hoster URLs are returned."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/abc"}],
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

        # No repo → no proxying
        uc = make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert len(result) >= 1
        assert result[0].url == "https://voe.sx/e/abc"


class TestStreamLinkSaveFailures:
    """A failed stream-link save drops only the streams that need the link."""

    @staticmethod
    def _use_case(
        repo: AsyncMock, resolve_fn: AsyncMock | None = None
    ) -> StremioStreamUseCase:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )
        sr = make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://voe.sx/e/abc", "hoster": "VOE"},
                {"url": "https://streamtape.com/v/xyz", "hoster": "Streamtape"},
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
        return make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=resolve_fn,
        )

    async def test_play_stream_without_saved_link_is_dropped(self) -> None:
        async def _save(link: CachedStreamLink) -> None:
            if "voe" in link.hoster_url:
                raise RuntimeError("cache down")

        repo = AsyncMock()
        repo.save = AsyncMock(side_effect=_save)

        result = await self._use_case(repo).execute(
            make_request(), base_url="http://localhost:8080"
        )

        assert len(result) == 1
        assert result[0].url.startswith("http://localhost:8080/api/v1/stremio/play/")

    async def test_only_the_answered_streams_save_a_link(self) -> None:
        """Saving a link per ranked stream (73 for one film) delayed the
        answer by 6 s; each answered stream needs one, behind /play/ or the
        proxy."""
        repo = AsyncMock()

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream | None:
            if "voe" in url:
                return ResolvedStream(
                    video_url="https://cdn.voe.example/hls/master.m3u8",
                    headers={"Referer": "https://voe.sx/"},
                    is_hls=True,
                )
            return None

        result = await self._use_case(repo, AsyncMock(side_effect=_resolve)).execute(
            make_request(), base_url="http://localhost:8080"
        )

        assert len(result) == 1
        saved = [c.args[0].hoster_url for c in repo.save.await_args_list]
        assert saved == ["https://voe.sx/e/abc"]

    async def test_a_stream_whose_link_is_not_saved_is_dropped(self) -> None:
        """/play/ and the proxy would not find it."""

        async def _save(link: CachedStreamLink) -> None:
            if "voe" in link.hoster_url:
                raise RuntimeError("cache down")

        repo = AsyncMock()
        repo.save = AsyncMock(side_effect=_save)

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            return ResolvedStream(
                video_url=f"{url}.mp4".replace("https://", "https://cdn.")
            )

        uc = self._use_case(repo, AsyncMock(side_effect=_resolve))
        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert [video(uc, s) for s in result] == [
            "https://cdn.streamtape.com/v/xyz.mp4"
        ]

    async def test_a_link_keeps_its_id_in_every_answer(self) -> None:
        """Stremio keeps the stream object (Continue Watching): its link is
        the same one, refreshed by each answer."""
        repo = AsyncMock()

        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            return ResolvedStream(video_url=f"{url}.mp4")

        uc = self._use_case(repo, AsyncMock(side_effect=_resolve))
        first = await uc.execute(make_request(), base_url="http://localhost:8080")
        second = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert sorted(s.url for s in first) == sorted(s.url for s in second)


class TestMeasuredQuality:
    """The playback check's measurement replaces the badge in the answer."""

    _PLAIN = "Iron.Man.2008.German"  # no quality in the release name
    _VOE_LINK = {"url": "https://voe.sx/e/voe1", "hoster": "VOE"}
    _TAPE_LINK = {"url": "https://streamtape.com/e/tape1", "hoster": "Streamtape"}

    @pytest.mark.parametrize(
        ("measured", "names", "first"),
        [
            (True, ["Scavengarr\n1080p", "Scavengarr"], "STREAMTAPE"),
            # Without it the hoster score decides (voe above streamtape)
            (False, ["Scavengarr", "Scavengarr"], "VOE"),
        ],
    )
    async def test_a_measured_1080p_ranks_first(
        self, measured: bool, names: list[str], first: str
    ) -> None:
        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            if measured and "streamtape" in url:
                return replace(resolved(url), quality=StreamQuality.HD_1080P)
            return resolved(url)

        links = [
            dict(self._VOE_LINK, release=self._PLAIN),
            dict(self._TAPE_LINK, release=self._PLAIN),
        ]
        uc = resolving_use_case(links, _resolve)

        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert [s.name for s in result] == names
        assert first in result[0].description
        # The saved links follow their streams to the new places
        assert [video(uc, s) for s in result][0].endswith(
            "tape1.mp4" if measured else "voe1.mp4"
        )

    async def test_a_badge_stays_without_a_measurement(self) -> None:
        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            return resolved(url)

        link = dict(self._VOE_LINK, release="Iron.Man.2008.German.720p.WEB")
        uc = resolving_use_case([link], _resolve)

        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert [s.name for s in result] == ["Scavengarr\n720p"]

    async def test_the_measured_size_shows_when_the_plugin_gave_none(self) -> None:
        async def _resolve(url: str, hoster: str = "") -> ResolvedStream:
            return replace(resolved(url), size_bytes=1_500_000_000)

        links = [
            dict(self._VOE_LINK, release=self._PLAIN),
            dict(self._TAPE_LINK, release=self._PLAIN, size="1.5 GB"),
        ]
        uc = resolving_use_case(links, _resolve)

        result = await uc.execute(make_request(), base_url="http://localhost:8080")

        voe, tape = sorted(
            (s.description for s in result), key=lambda d: "STREAMTAPE" in d
        )
        assert "1.4 GB" in voe
        assert "1.5 GB" in tape
        assert "1.4 GB" not in tape

    async def test_a_cached_answer_carries_the_measurement(self) -> None:
        resolutions = Resolutions(alive=(VOE,))
        resolutions.store[VOE] = replace(resolved(VOE), quality=StreamQuality.HD_1080P)
        uc = from_cache([hoster_link(VOE, self._PLAIN)], resolutions)

        streams = await uc.execute(make_request(), base_url="http://localhost:8080")

        assert [s.name for s in streams] == ["Scavengarr\n1080p"]


class TestWorkerThreads:
    """The stream conversion runs in a worker thread; its log lines keep the
    request's ``request_id``."""

    @pytest.mark.asyncio
    async def test_the_conversion_keeps_the_log_context(self) -> None:
        seen: dict[str, object] = {}

        def _convert(results: list[SearchResult], **_kw: object) -> list[RankedStream]:
            seen.update(structlog.contextvars.get_contextvars())
            return []

        with structlog.contextvars.bound_contextvars(request_id="r1"):
            await rank_streams(
                [make_search_result()], {}, convert_fn=_convert, sort=list
            )

        assert seen["request_id"] == "r1"
