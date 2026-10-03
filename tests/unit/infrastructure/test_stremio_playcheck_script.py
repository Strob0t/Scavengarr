"""Tests for scripts/stremio_playcheck.py (no live requests)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import respx

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))  # the script imports stremio_measure
    try:
        spec = importlib.util.spec_from_file_location(
            "stremio_playcheck", _SCRIPTS / "stremio_playcheck.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(_SCRIPTS))


_mod = _load()


class TestMediaKind:
    def test_mpeg_ts(self) -> None:
        packet = b"\x47" + b"\x00" * 187
        assert _mod.media_kind(packet * 2) == "mpegts"

    def test_mp4(self) -> None:
        assert _mod.media_kind(b"\x00\x00\x00\x20ftypisom") == "mp4"

    @pytest.mark.parametrize(
        ("data", "kind"),
        [
            (b"<html><body>403</body></html>", "html"),
            (b"error_wrong_ip", "bytes:6572726f725f7772"),
        ],
    )
    def test_not_media(self, data: bytes, kind: str) -> None:
        """DoodStream's CDN answers a foreign IP with 200 "error_wrong_ip"."""
        assert _mod.media_kind(data) == kind
        assert not _mod.is_media(kind)


class TestCheckStream:
    @respx.mock
    async def test_hls_variant_from_root(self) -> None:
        """The case that found the proxy bug: the master is fine, the
        variant is not."""
        respx.get("https://cdn.example/hls/master.m3u8").respond(
            200, text="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\n/secure/v.m3u8\n"
        )
        respx.get("https://cdn.example/secure/v.m3u8").respond(404)
        stream = {"url": "https://cdn.example/hls/master.m3u8"}

        async with httpx.AsyncClient() as client:
            verdict = await _mod.check_stream(client, stream)

        assert verdict == "FAIL variant 404"

    @respx.mock
    async def test_file_with_seek_and_headers(self) -> None:
        route = respx.get("https://cdn.example/v.mp4")
        route.side_effect = [
            httpx.Response(
                206,
                content=b"\x00\x00\x00\x20ftypisom" + b"\x00" * 100,
                headers={"content-range": "bytes 0-2047/3000000000"},
            ),
            httpx.Response(206, content=b"\x00" * 1024),
        ]
        stream = {
            "url": "https://cdn.example/v.mp4",
            "behaviorHints": {"proxyHeaders": {"request": {"Referer": "https://h/"}}},
        }

        async with httpx.AsyncClient() as client:
            verdict = await _mod.check_stream(client, stream)

        assert verdict == "OK mp4 3.00 GB, seek 206"
        assert route.calls[0].request.headers["Referer"] == "https://h/"
        assert route.calls[1].request.headers["Range"] == "bytes=1500000000-1500001023"
