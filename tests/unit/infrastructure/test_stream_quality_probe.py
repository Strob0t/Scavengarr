"""Tests for scripts/probes/stream_quality.py (the stored resolutions'
qualities per resolver)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from scavengarr.domain.entities.stremio import StreamQuality

_PROBES = Path(__file__).resolve().parents[3] / "scripts" / "probes"


def _load() -> ModuleType:
    # By path: scripts/probes/ is no package
    spec = importlib.util.spec_from_file_location(
        "stream_quality_probe", _PROBES / "stream_quality.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses resolve their annotations through sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load()


def _stream(
    quality: StreamQuality = StreamQuality.UNKNOWN,
    *,
    hls: bool = False,
    size: int | None = None,
) -> dict[str, Any]:
    return {
        "video_url": "https://cdn.example/SECRET-TOKEN/master.m3u8",
        "headers": {"Referer": "https://hoster.example/SECRET"},
        "is_hls": hls,
        "quality": int(quality),
        "resolved_at": 1.0,
        "size_bytes": size,
        "address_bound": False,
    }


def _entry(resolver: str, stream: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "url": "https://hoster.example/e/SECRET",
        "stream": stream,
        "remaining": 1800.0,
        "resolver": resolver,
    }


_SNAPSHOT: dict[str, Any] = {
    "version": 1,
    "taken_at": 1_000.0,
    "results": [
        _entry("streamtape", _stream(StreamQuality.HD_1080P, size=2_000_000_000)),
        _entry("streamtape", _stream(size=1_000_000)),
        _entry("streamtape", None),
        _entry("vidhide", _stream(StreamQuality.HD_720P, hls=True)),
        _entry("vidhide", _stream(hls=True)),
        _entry("voe", _stream(StreamQuality.HD_1080P, hls=True)),
        _entry("voe", _stream(StreamQuality.UHD_4K, hls=True)),
        _entry("filemoon", _stream(hls=True)),
        _entry("filemoon", None),
    ],
    "redirects": [],
    "breakers": {},
}


class TestCount:
    def test_per_resolver(self) -> None:
        counts = _mod.count(_SNAPSHOT)

        tape = counts["streamtape"]
        assert (tape.resolutions, tape.dead, tape.streams, tape.hls, tape.sized) == (
            3,
            1,
            2,
            0,
            2,
        )
        assert tape.quality == {StreamQuality.HD_1080P: 1}
        assert tape.known == 1
        assert counts["vidhide"].hls == 2
        assert counts["voe"].quality == {
            StreamQuality.HD_1080P: 1,
            StreamQuality.UHD_4K: 1,
        }
        assert (counts["filemoon"].streams, counts["filemoon"].dead) == (1, 1)

    def test_an_unknown_quality_value_counts_as_unknown(self) -> None:
        stream = _stream()
        stream["quality"] = 99
        counts = _mod.count({"results": [_entry("x", stream)]})

        assert counts["x"].known == 0

    def test_an_entry_without_a_resolver_is_counted_apart(self) -> None:
        counts = _mod.count({"results": [_entry("", _stream())]})

        assert list(counts) == ["(none)"]


class TestReport:
    def test_the_lines_split_measured_and_self_named_qualities(self) -> None:
        lines = _mod.report_lines(_SNAPSHOT, now=1_090.0)
        text = "\n".join(lines)

        assert lines[0] == (
            "hoster_state:v1 taken 90 s ago: 9 resolutions by 4 resolvers"
        )
        assert "| streamtape | 3 | 1 | 2 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 2 |" in lines
        assert "| vidhide | 2 | 0 | 2 | 2 | 0 | 0 | 1 | 0 | 0 | 0 | 1 | 0 |" in lines
        assert (
            "measured (all but filemoon, supervideo, voe):"
            " 2 of 4 streams with a known quality (50 %), 2 with a size" in lines
        )
        assert (
            "named by the resolver (filemoon, supervideo, voe):"
            " 2 of 3 streams with a known quality (67 %), 0 with a size" in lines
        )
        # The self-named table comes after the measured one
        assert text.index("| voe |") > text.index("| vidhide |")

    def test_nothing_secret_is_printed(self) -> None:
        text = "\n".join(_mod.report_lines(_SNAPSHOT, now=1_090.0))

        assert "SECRET" not in text
        assert "https://" not in text

    def test_no_streams(self) -> None:
        lines = _mod.report_lines(
            {"version": 1, "taken_at": 1_000.0, "results": []}, now=1_000.0
        )

        assert "measured (all but filemoon, supervideo, voe): no streams" in lines
