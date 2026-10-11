"""Tests for the playlist parsing and the report of scripts/probes/hls_throughput.py."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import respx

from scavengarr.domain.entities.stremio import CachedStreamLink

_PROBES = Path(__file__).resolve().parents[3] / "scripts" / "probes"


def _load() -> ModuleType:
    # By path: on sys.path the probes' redis.py would shadow the redis package
    spec = importlib.util.spec_from_file_location(
        "hls_throughput_probe", _PROBES / "hls_throughput.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses resolve their annotations through sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load()

_MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=1280x720
720p/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080
1080p/index.m3u8
"""

_MEDIA = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXTINF:6.000,
seg-1.ts
#EXTINF:5.500,
seg-2.ts?x=1
#EXT-X-ENDLIST
"""


class TestPlaylists:
    def test_the_highest_variant_wins(self) -> None:
        variants = _mod._variants(_MASTER, "https://cdn.example/v/master.m3u8")

        assert max(variants) == (
            3000000,
            "1920x1080",
            "https://cdn.example/v/1080p/index.m3u8",
        )

    def test_segments_carry_the_duration_and_the_query(self) -> None:
        segments = _mod._segments(
            _MEDIA, "https://cdn.example/v/1080p/index.m3u8", "t=abc"
        )

        assert [(s.url, s.duration) for s in segments] == [
            ("https://cdn.example/v/1080p/seg-1.ts?t=abc", 6.0),
            ("https://cdn.example/v/1080p/seg-2.ts?x=1", 5.5),
        ]

    def test_cdn_is_the_registered_domain(self) -> None:
        assert _mod.cdn("https://s3.cdn.example.io/a/b.ts?t=1") == "example.io"

    def test_the_masters_variants_are_listed_highest_first(self) -> None:
        """Row 68 asks whether a master lists lower variants (a player's
        bandwidth estimate may hold the highest on a slow CDN)."""
        variants = _mod._variants(_MASTER, "https://cdn.example/v/master.m3u8")

        assert _mod.describe_variants(variants) == (
            "1920x1080 3.0, 1280x720 1.2 Mbit/s"
        )

    @respx.mock
    async def test_the_master_names_its_variants(self) -> None:
        respx.get("https://cdn.example/v/master.m3u8").respond(200, text=_MASTER)
        respx.get("https://cdn.example/v/1080p/index.m3u8").respond(200, text=_MEDIA)
        link = CachedStreamLink(
            "id",
            "https://hoster.example/e/1",
            video_url="https://cdn.example/v/master.m3u8",
        )

        async with httpx.AsyncClient() as http:
            segments, variant = await _mod._segments_of(http, link, {})

        assert len(segments) == 2
        assert variant == (
            ", variant 1920x1080 (declared 3.0 Mbit/s;"
            " the master lists 1920x1080 3.0, 1280x720 1.2 Mbit/s)"
        )

    @respx.mock
    async def test_a_media_playlist_says_it_has_no_variants(self) -> None:
        """FireStream's links are media playlists: a player cannot step
        down to a lower variant there."""
        respx.get("https://cdn.example/v/index.m3u8").respond(200, text=_MEDIA)
        link = CachedStreamLink(
            "id",
            "https://hoster.example/e/1",
            video_url="https://cdn.example/v/index.m3u8",
        )

        async with httpx.AsyncClient() as http:
            _, variant = await _mod._segments_of(http, link, {})

        assert variant == ", a media playlist (no variants to step down to)"


class TestInFlight:
    """Row 68: whether a fourth and fifth segment in flight still add
    throughput, one read-ahead run per count."""

    def test_counts_are_read_from_a_list(self) -> None:
        assert _mod.in_flight_counts("3,4, 5") == [3, 4, 5]

    @pytest.mark.parametrize("value", ["1,3", "x", ""])
    def test_a_count_below_two_or_no_number_is_refused(self, value: str) -> None:
        with pytest.raises(argparse.ArgumentTypeError):
            _mod.in_flight_counts(value)

    def test_one_read_ahead_run_per_count(self) -> None:
        runs = _mod.plan_runs([3, 4, 5])

        assert [(run.name, run.in_flight, run.ranged) for run in runs] == [
            ("one connection", 1, False),
            ("read-ahead 2", 3, False),
            ("read-ahead 3", 4, False),
            ("read-ahead 4", 5, False),
            ("3 ranges", 1, True),
            ("read-ahead 3 × 3 ranges", 4, True),
        ]

    def test_without_counts_the_four_runs_stay(self) -> None:
        args = _mod._parser().parse_args([])

        assert [run.name for run in _mod.plan_runs(args.in_flight)] == [
            "one connection",
            "read-ahead 3",
            "3 ranges",
            "read-ahead 3 × 3 ranges",
        ]

    @respx.mock
    async def test_a_refused_segment_counts_by_its_status(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A deeper read-ahead may draw 429s: the run names them."""
        respx.get("https://cdn.example/s1.ts").respond(200, content=b"x" * 100)
        respx.get("https://cdn.example/s3.ts").respond(429)
        respx.get("https://cdn.example/s2.ts").mock(
            side_effect=httpx.ReadTimeout("slow")
        )
        segments = [
            _mod.Segment(f"https://cdn.example/s{n}.ts", 6.0) for n in (1, 2, 3)
        ]
        run = _mod.Run("read-ahead 3", 4)

        async with httpx.AsyncClient() as http:
            await _mod.execute(_mod.Fetcher(http, {}), segments, run)

        assert run.bytes == 100
        assert run.errors == {"429": 1, "ReadTimeout": 1}
        assert run.failed == 2
        assert "a segment failed: 429" in capsys.readouterr().out

    @respx.mock
    async def test_after_a_429_the_run_asks_no_more(self) -> None:
        """A throttling CDN gets no further requests: the Pi's address
        shares it with the playbacks."""
        respx.get("https://cdn.example/s1.ts").respond(429)
        later = respx.get(url__regex=r"https://cdn\.example/s[23]\.ts").respond(
            200, content=b"x"
        )
        segments = [
            _mod.Segment(f"https://cdn.example/s{n}.ts", 6.0) for n in (1, 2, 3)
        ]
        run = _mod.Run("one connection", 1)

        async with httpx.AsyncClient() as http:
            await _mod.execute(_mod.Fetcher(http, {}), segments, run)

        assert run.errors == {"429": 1}
        assert not later.called

    @respx.mock
    async def test_a_429_ends_the_remaining_runs(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        respx.get("https://cdn.example/s1.ts").respond(200, content=b"x" * 10)
        respx.get("https://cdn.example/s2.ts").respond(429)
        rest = respx.get(url__regex=r"https://cdn\.example/s[3-8]\.ts").respond(
            200, content=b"x"
        )
        segments = [
            _mod.Segment(f"https://cdn.example/s{n}.ts", 6.0) for n in range(1, 9)
        ]
        runs = _mod.plan_runs([3])

        async with httpx.AsyncClient() as http:
            await _mod.run_all(_mod.Fetcher(http, {}), segments, runs, per_run=2)

        assert runs[0].errors == {"429": 1}
        assert all(run.seconds == 0 for run in runs[1:])
        assert not rest.called
        assert "the CDN answered 429: the remaining runs are skipped" in (
            capsys.readouterr().out
        )


class TestReport:
    def test_connections_and_rate(self) -> None:
        run = _mod.Run("read-ahead 2 × 3 ranges", 3, ranged=True)
        run.bytes, run.seconds = 10_000_000, 8.0

        assert run.connections == 9
        assert run.mbit == pytest.approx(10.0)

    def test_the_rule_counts_the_connections_needed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        one = _mod.Run("one connection", 1)
        one.bytes, one.seconds, one.duration = 7_500_000, 30.0, 20.0
        one.per_segment = [1.5, 2.5]
        ahead = _mod.Run("read-ahead 2", 3)
        ahead.bytes, ahead.seconds, ahead.duration = 7_500_000, 10.0, 20.0
        ranged = _mod.Run("3 ranges", 1, ranged=True)
        ranged.ranges, ranged.honoured, ranged.ignored = 1, 0, True
        ranged.bytes, ranged.seconds = 2_500_000, 5.0

        _mod._report([one, ahead, ranged])

        out = capsys.readouterr().out
        assert (
            "| one connection | 1 | 7.2 MiB | 30.0 s | 2.0 (1.5–2.5 per segment)"
            " | 0.67 | – |" in out
        )
        assert "| read-ahead 2 | 3 | 7.2 MiB | 10.0 s | 6.0 | 2.00 | – |" in out
        # A range run answered with whole segments measures no real time
        assert "| 3 ranges | 3 | 2.4 MiB | 5.0 s | 4.0 | – | 0 of 1" in out
        assert "0 of 1 answered 206; one got the whole segment" in out
        # 15 MB over 40 s of video: 3.0 Mbit/s; 1.5 × 3.0 / 2.0 = 2.25 → 3
        assert "Bitrate 3.0 Mbit/s" in out
        assert "= 3 connections." in out

    def test_failures_are_named_in_the_row(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run = _mod.Run("read-ahead 4", 5)
        run.bytes, run.seconds, run.duration = 7_500_000, 10.0, 20.0
        run.errors.update({"429": 2, "ReadTimeout": 1})

        _mod._report([run])

        out = capsys.readouterr().out
        assert "| 10.0 s, 3 failed (429 ×2, ReadTimeout ×1) |" in out


_FILE = "https://cdn.example/v/file.mp4"
_MIB = 1048576


def _ranged_file(size: int):  # noqa: ANN202 - a respx side effect
    def answer(request: httpx.Request) -> httpx.Response:
        asked = request.headers.get("range")
        if not asked:
            return httpx.Response(200, content=b"x" * size)
        lo, hi = (int(n) for n in asked.removeprefix("bytes=").split("-"))
        hi = min(hi, size - 1)
        return httpx.Response(
            206,
            content=b"x" * (hi - lo + 1),
            headers={"content-range": f"bytes {lo}-{hi}/{size}"},
        )

    return answer


class TestFileMode:
    def test_headers_put_the_stored_ones_over_the_defaults(self) -> None:
        link = _mod.CachedStreamLink(
            stream_id="s", hoster_url="h", video_headers='{"Referer": "https://m/"}'
        )

        headers = _mod.file_headers(link)

        assert headers == {
            "User-Agent": _mod.DEFAULT_USER_AGENT,
            "Accept-Encoding": "identity",
            "Referer": "https://m/",
        }

    def test_range_bounds_step_through_the_limit(self) -> None:
        assert _mod.range_bounds(2 * _MIB + 10, _MIB) == [
            (0, _MIB - 1),
            (_MIB, 2 * _MIB - 1),
            (2 * _MIB, 2 * _MIB + 9),
        ]

    def test_honoured_needs_206_with_the_asked_range(self) -> None:
        assert _mod.honoured(206, "bytes 0-99/500", 0, 99)
        assert not _mod.honoured(206, "bytes 0-499/500", 0, 99)
        assert not _mod.honoured(200, "", 0, 99)

    @respx.mock
    async def test_runs_read_the_limit_once_and_in_ranges(self) -> None:
        respx.get(_FILE).mock(side_effect=_ranged_file(5 * _MIB))

        async with httpx.AsyncClient() as http:
            one, ranged, _ = await _mod.file_runs(http, _FILE, {}, 3 * _MIB + 5)

        assert one.bytes == 3 * _MIB + 5
        assert ranged.bytes == 3 * _MIB + 5
        assert (ranged.ranges, ranged.honoured, ranged.ignored) == (4, 4, False)
        assert ranged.connections == 3

    @respx.mock
    async def test_a_cdn_without_ranges_stops_the_range_run(self) -> None:
        respx.get(_FILE).respond(200, content=b"x" * (4 * _MIB))

        async with httpx.AsyncClient() as http:
            _, ranged, _ = await _mod.file_runs(http, _FILE, {}, 4 * _MIB)

        assert ranged.ignored
        assert ranged.honoured == 0
        assert ranged.ranges <= 3

    def test_report_names_rates_and_ranges(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        one = _mod.Run("one connection", 1)
        one.bytes, one.seconds = 32 * _MIB, 10.0
        ranged = _mod.Run("3 ranges", 1, ranged=True)
        ranged.bytes, ranged.seconds, ranged.ranges, ranged.honoured = (
            32 * _MIB,
            5.0,
            32,
            32,
        )

        _mod.report_file([one, ranged], size=900 * _MIB)

        out = capsys.readouterr().out
        assert "26.8" in out and "53.7" in out
        assert "32 of 32 answered 206 with the asked range" in out
        assert "900.0 MiB" in out
