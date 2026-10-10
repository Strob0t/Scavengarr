"""Fetch every stream of a Scavengarr instance the way a player would.

Usage (against a running server):
    poetry run python scripts/stremio_playcheck.py [--base URL] [--insecure] [IDS]

IDS are Stremio ids (``movie/tt15398776``, ``series/tt0903747:1:1``); without
them the title set of ``stremio_measure.py`` is checked. Each stream is
fetched with its ``proxyHeaders``: for HLS the master playlist, the first
variant and its first two segments, for a file the first KiB and one in the
middle (a seek). Prints a verdict per stream and per source
(``HOSTER · plugin``). The playback check of the server reads only the start
of a stream; this one follows it to the media bytes, from the machine it
runs on (a stream bound to the resolving IP fails from elsewhere).

Every answer is streamed and read only up to the bytes the judgement needs
(a playlist up to ``_PLAYLIST_MAX``, media up to ``_HEAD``, a seek none),
then closed: a CDN or the proxy that answers a Range request with 200 and
the whole file costs a few KiB, and the verdict says ``200 instead of 206``.
One deadline per check, ``_CHECK_TIMEOUT``, covers all its requests.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import Counter

import httpx
from stremio_measure import GROUPS, _source

_PLAYER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_HEAD = 2048
_PLAYLIST_MAX = 1024 * 1024
# The whole check of one stream, headers and bodies of all its requests
_CHECK_TIMEOUT = 30.0


async def _fetch(
    client: httpx.AsyncClient, url: str, headers: dict, limit: int
) -> tuple[httpx.Response, bytes]:
    """GET *url*; read at most *limit* body bytes (none for an error status)
    and close the answer, so a body of any length costs *limit* bytes."""
    async with client.stream("GET", url, headers=headers) as resp:
        parts: list[bytes] = []
        size = 0
        if resp.status_code < 400 and limit > 0:
            async for chunk in resp.aiter_bytes():
                parts.append(chunk[: limit - size])
                size += len(parts[-1])
                if size >= limit:
                    break
        return resp, b"".join(parts)


def _range_note(status: int) -> str:
    """A Range request answered with 200 (the whole file) is a finding."""
    return " instead of 206" if status == 200 else ""


def media_kind(data: bytes) -> str:
    """What the first bytes of a response are: media or not."""
    if data[:1] == b"\x47" and (len(data) < 189 or data[188:189] == b"\x47"):
        return "mpegts"
    if data[4:8] in (b"ftyp", b"styp", b"moof", b"sidx", b"moov", b"free", b"mdat"):
        return "mp4"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "mkv"
    if data.lstrip()[:1] == b"<":
        return "html"
    return "bytes:" + data[:8].hex()


def is_media(kind: str) -> bool:
    return kind in ("mpegts", "mp4", "mkv")


def _uris(playlist: str) -> list[str]:
    return [ln for ln in playlist.splitlines() if ln and not ln.startswith("#")]


async def _check_hls(client: httpx.AsyncClient, url: str, headers: dict) -> str:
    resp, body = await _fetch(client, url, headers, _PLAYLIST_MAX)
    text = body.decode("utf-8", "replace")
    if resp.status_code >= 400 or not text.startswith("#EXTM3U"):
        return f"FAIL master {resp.status_code}"
    if "#EXT-X-STREAM-INF" in text:
        resp, body = await _fetch(
            client, str(resp.url.join(_uris(text)[0])), headers, _PLAYLIST_MAX
        )
        text = body.decode("utf-8", "replace")
        if resp.status_code >= 400 or not text.startswith("#EXTM3U"):
            return f"FAIL variant {resp.status_code}"
    segments = _uris(text)
    kinds = []
    full = 0
    for segment in segments[:2]:
        seg, data = await _fetch(
            client,
            str(resp.url.join(segment)),
            {**headers, "Range": f"bytes=0-{_HEAD - 1}"},
            _HEAD,
        )
        full += seg.status_code == 200
        kinds.append(
            f"{seg.status_code}" if seg.status_code >= 400 else media_kind(data)
        )
    ok = bool(kinds) and all(is_media(k) for k in kinds)
    note = f"; {full} of {len(kinds)} segments 200 instead of 206" if full else ""
    return (
        f"{'OK' if ok else 'FAIL'} hls {len(segments)} segments: {', '.join(kinds)}"
        + note
    )


async def _check_file(client: httpx.AsyncClient, url: str, headers: dict) -> str:
    resp, data = await _fetch(
        client, url, {**headers, "Range": f"bytes=0-{_HEAD - 1}"}, _HEAD
    )
    if resp.status_code >= 400:
        return f"FAIL {resp.status_code} {resp.headers.get('content-type')}"
    kind = media_kind(data)
    if not is_media(kind):
        return f"FAIL {kind} {data[:40]!r}"
    head = ", head 200" + _range_note(200) if resp.status_code == 200 else ""
    total = resp.headers.get("content-range", "").rsplit("/", 1)[-1]
    if head:  # the whole file: its length is the size
        total = resp.headers.get("content-length", "")
    if not total.isdigit():
        return f"OK {kind}{head}, no seek (size unknown)"
    middle = int(total) // 2
    seek, _ = await _fetch(
        client, url, {**headers, "Range": f"bytes={middle}-{middle + 1023}"}, 0
    )
    verdict = "OK" if seek.status_code == 206 else "FAIL"
    return (
        f"{verdict} {kind} {int(total) / 1e9:.2f} GB{head}"
        f", seek {seek.status_code}{_range_note(seek.status_code)}"
    )


async def check_stream(client: httpx.AsyncClient, stream: dict) -> str:
    """The verdict for one stream, within ``_CHECK_TIMEOUT`` as a whole."""
    url = stream.get("url") or ""
    hints = stream.get("behaviorHints") or {}
    headers = {"User-Agent": _PLAYER_UA}
    headers.update((hints.get("proxyHeaders") or {}).get("request") or {})
    is_hls = any(m in url for m in (".m3u8", "master.txt", "/stremio/proxy/"))
    try:
        async with asyncio.timeout(_CHECK_TIMEOUT):
            if is_hls:
                return await _check_hls(client, url, headers)
            return await _check_file(client, url, headers)
    except TimeoutError:
        return f"FAIL timeout after {_CHECK_TIMEOUT:g} s"
    except httpx.HTTPError as exc:
        return f"FAIL {type(exc).__name__}"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch every stream like a player.")
    parser.add_argument("--base", default="http://127.0.0.1:7979")
    parser.add_argument("--insecure", action="store_true", help="skip TLS checks")
    parser.add_argument("ids", nargs="*")
    args = parser.parse_args()
    ids = args.ids or [f"{t}/{sid}" for g in GROUPS.values() for t, sid, _ in g]

    ok: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    async with httpx.AsyncClient(
        verify=not args.insecure, follow_redirects=True, timeout=30
    ) as client:
        for item in ids:
            resp = await client.get(f"{args.base}/api/v1/stremio/stream/{item}.json")
            streams = resp.json().get("streams", [])
            print(f"== {item}: {len(streams)} streams")
            for stream in streams:
                start = time.monotonic()
                verdict = await check_stream(client, stream)
                source = _source(stream)
                (ok if verdict.startswith("OK") else failed)[source] += 1
                took = time.monotonic() - start
                print(f"   {source:28} {took:5.1f}s {verdict}")
    print("per source (ok/failed):")
    for source in sorted(set(ok) | set(failed)):
        print(f"   {source:28} {ok[source]:3} {failed[source]:3}")
    playable = sum(ok.values())
    print(f"streams playable {playable} of {playable + sum(failed.values())}")


if __name__ == "__main__":
    asyncio.run(main())
