"""Tests for scripts/probes/vidhide_segments.py: header sets and proxy targets."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_PROBE = Path(__file__).resolve().parents[3] / "scripts/probes/vidhide_segments.py"

# VidHide's stream URL names the CDN host in mixed case (production, 2026-10-07)
_STREAM = "https://2ZO6sb3MYz7fapc.acek-cdn.com/hls2/01/abc_h/master.m3u8?t=tok"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vidhide_segments", _PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load()


def test_header_variants_derive_origin_from_the_referer() -> None:
    variants = probe.header_variants({"Referer": "https://moflix-stream.click/v/x"})

    assert len(variants) == 6
    assert variants["referer + origin + player UA"]["Origin"] == (
        "https://moflix-stream.click"
    )
    assert variants["no headers"] == {}
    assert variants["proxy (stored + player UA)"]["User-Agent"] == (
        probe.DEFAULT_USER_AGENT
    )


def test_header_variants_without_referer_skip_the_referer_sets() -> None:
    variants = probe.header_variants({})

    assert list(variants) == [
        "proxy (stored + player UA)",
        "player UA only",
        "no headers",
    ]


def test_segment_on_the_lower_case_host_is_proxied() -> None:
    uri = "https://2zo6sb3myz7fapc.acek-cdn.com/hls2/01/abc_h/seg-1-v1-a1.ts?s=1"

    url, path = probe.proxy_target(uri, "", _STREAM)

    assert path == "/hls2/01/abc_h/seg-1-v1-a1.ts"
    assert (
        url == "https://2ZO6sb3MYz7fapc.acek-cdn.com/hls2/01/abc_h/seg-1-v1-a1.ts?s=1"
    )


def test_relative_uri_takes_the_stream_query_and_the_playlist_directory() -> None:
    url, path = probe.proxy_target("seg-1.ts", "720p/", _STREAM)

    assert path == "720p/seg-1.ts"
    assert url.endswith("/hls2/01/abc_h/720p/seg-1.ts?t=tok")


def test_uri_of_another_host_stays_direct() -> None:
    url, path = probe.proxy_target("https://other.example/seg-1.ts", "", _STREAM)

    assert path is None
    assert url == "https://other.example/seg-1.ts"


def test_uri_shape_names_no_url() -> None:
    shape = probe.uri_shape("/hls2/other/seg.ts", _STREAM)

    assert shape == "root-relative URI, outside the stream's directory"
    assert probe.uri_shape("seg.ts", _STREAM) == (
        "relative URI, under the stream's directory"
    )


def test_first_uri_and_master() -> None:
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nindex-v1.m3u8\n"

    assert probe.is_master(master)
    assert probe.first_uri(master) == "index-v1.m3u8"
    assert probe.first_uri("#EXTM3U\n") == ""
