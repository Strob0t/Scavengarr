"""Tests for the playlist parsing and the report of scripts/probes/hls_throughput.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

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
            "| one connection | 1 | 7.2 MiB | 30.0 s | 2.0 (1.5–2.5 per segment) | – |"
            in out
        )
        assert "| read-ahead 2 | 3 | 7.2 MiB | 10.0 s | 6.0 | – |" in out
        assert "0 of 1 answered 206; one got the whole segment" in out
        # 15 MB over 40 s of video: 3.0 Mbit/s; 1.5 × 3.0 / 2.0 = 2.25 → 3
        assert "Bitrate 3.0 Mbit/s" in out
        assert "= 3 connections." in out
