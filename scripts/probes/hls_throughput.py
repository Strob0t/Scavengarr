"""HLS throughput from the Pi: one connection, read-ahead, byte ranges, both.

    probe hls_throughput [--hoster firestream] [--segments 10]
                         [--stream-id ID | --imdb [movie/|series/]ID]

The newest stored HLS link of the hoster (the Redis backend: the probe scans
the stream link keys; ``--stream-id`` names one) is fetched from its CDN
with the link's headers, as the HLS proxy does; the variant with the
highest bandwidth gives the segments. Four runs download ``--segments``
consecutive media segments each, from the middle of the title: one after
another (one connection), with a read-ahead of two (three in flight), each
segment in three byte ranges at once, and read-ahead two with three ranges
each (up to nine in flight). The probe prints Mbit/s per run, the title's
bitrate (segment bytes × 8 over their ``EXTINF`` durations) and whether
the CDN honoured the ranges (``206`` with ``Content-Range``; once a range
is answered with the whole segment, the range runs stop).

With ``--imdb`` the probe first asks the app (``http://127.0.0.1:$PORT``)
for the title's streams so that fresh links exist; that fills the app's
caches like any Stremio request. Otherwise the probe changes nothing. Run
it while nothing plays: the runs share the Pi's connection with the app.
Prints no URLs, only the CDN's domain.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import structlog

from scavengarr.domain.entities.stremio import CachedStreamLink
from scavengarr.infrastructure.cache.cache_factory import create_cache
from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.persistence.stream_link_cache import (
    CacheStreamLinkRepository,
)
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

RANGES = 3
READ_AHEAD = 2
CHUNK = 65536
_STREAM_ID_RE = re.compile(r"/(?:proxy|play)/([^/?#]+)")
_BANDWIDTH_RE = re.compile(r"BANDWIDTH=(\d+)")
_RESOLUTION_RE = re.compile(r"RESOLUTION=(\d+x\d+)")
_TOTAL_RE = re.compile(r"bytes \d+-\d+/(\d+)")


@dataclass(frozen=True)
class Segment:
    url: str
    duration: float


@dataclass
class Run:
    name: str
    in_flight: int  # segments in flight at once
    ranged: bool = False  # each segment in RANGES byte ranges at once
    bytes: int = 0
    seconds: float = 0.0
    duration: float = 0.0  # EXTINF seconds of the segments fetched
    per_segment: list[float] = field(default_factory=list)  # Mbit/s each
    ranges: int = 0  # range answers
    honoured: int = 0  # of them 206 with Content-Range
    ignored: bool = False  # a range was answered 200 with the whole segment
    failed: int = 0

    @property
    def connections(self) -> int:
        return self.in_flight * (RANGES if self.ranged else 1)

    @property
    def mbit(self) -> float:
        return _mbit(self.bytes, self.seconds)


def _mbit(count: int, seconds: float) -> float:
    return count * 8 / seconds / 1e6 if seconds > 0 else 0.0


def _mib(count: float) -> str:
    return f"{count / 1048576:.1f} MiB"


def cdn(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return ".".join(host.split(".")[-2:])


def _with_query(url: str, query: str) -> str:
    """The proxy appends the stream URL's query to a bare CDN path."""
    if query and not urlsplit(url).query:
        return f"{url}?{query}"
    return url


def _variants(text: str, base: str) -> list[tuple[int, str, str]]:
    """``(bandwidth, resolution, url)`` of a master playlist's variants."""
    out: list[tuple[int, str, str]] = []
    attrs = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-STREAM-INF"):
            attrs = line
        elif line and not line.startswith("#") and attrs:
            bandwidth = _BANDWIDTH_RE.search(attrs)
            resolution = _RESOLUTION_RE.search(attrs)
            out.append(
                (
                    int(bandwidth.group(1)) if bandwidth else 0,
                    resolution.group(1) if resolution else "?",
                    urljoin(base, line),
                )
            )
            attrs = ""
    return out


def _segments(text: str, base: str, query: str) -> list[Segment]:
    out: list[Segment] = []
    duration = 0.0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            duration = float(line[8:].split(",")[0] or 0)
        elif line and not line.startswith("#"):
            out.append(Segment(_with_query(urljoin(base, line), query), duration))
            duration = 0.0
    return out


def _cdn_headers(link: CachedStreamLink) -> dict[str, str]:
    headers = json.loads(link.video_headers) if link.video_headers else {}
    return {"User-Agent": DEFAULT_USER_AGENT, **headers}


class Fetcher:
    def __init__(self, http: httpx.AsyncClient, headers: dict[str, str]) -> None:
        self._http = http
        self._headers = headers

    async def whole(self, url: str) -> int:
        """Download *url* on one connection; the byte count."""
        count = 0
        async with self._http.stream("GET", url, headers=self._headers) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(CHUNK):
                count += len(chunk)
        return count

    async def size(self, url: str) -> int:
        """The segment's size from a HEAD, else from a one-byte range."""
        resp = await self._http.head(url, headers=self._headers)
        length = resp.headers.get("content-length")
        if resp.status_code == 200 and length:
            return int(length)
        headers = {**self._headers, "Range": "bytes=0-0"}
        resp = await self._http.get(url, headers=headers)
        resp.raise_for_status()
        total = _TOTAL_RE.search(resp.headers.get("content-range", ""))
        if resp.status_code == 206 and total:
            return int(total.group(1))
        return len(resp.content)

    async def ranged(self, url: str, run: Run) -> int:
        """Download *url* in RANGES byte ranges at once; the byte count."""
        size = await self.size(url)
        step = math.ceil(size / RANGES)
        bounds = [(lo, min(lo + step, size) - 1) for lo in range(0, size, step)]
        counts = await asyncio.gather(
            *(self._range(url, lo, hi, run) for lo, hi in bounds)
        )
        return sum(counts)

    async def _range(self, url: str, lo: int, hi: int, run: Run) -> int:
        headers = {**self._headers, "Range": f"bytes={lo}-{hi}"}
        count = 0
        async with self._http.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            run.ranges += 1
            if resp.status_code == 206 and "content-range" in resp.headers:
                run.honoured += 1
            else:
                run.ignored = True
            async for chunk in resp.aiter_bytes(CHUNK):
                count += len(chunk)
        return count


async def execute(fetcher: Fetcher, segments: list[Segment], run: Run) -> None:
    """Fetch *segments* in order with at most ``run.in_flight`` at once."""
    gate = asyncio.Semaphore(run.in_flight)

    async def one(segment: Segment) -> None:
        async with gate:
            if run.ignored:
                return
            started = time.monotonic()
            try:
                if run.ranged:
                    count = await fetcher.ranged(segment.url, run)
                else:
                    count = await fetcher.whole(segment.url)
            except httpx.HTTPError as exc:
                run.failed += 1
                print(f"  {run.name}: a segment failed: {type(exc).__name__}")
                return
            run.bytes += count
            run.duration += segment.duration
            if run.in_flight == 1:
                run.per_segment.append(_mbit(count, time.monotonic() - started))

    started = time.monotonic()
    await asyncio.gather(*(one(segment) for segment in segments))
    run.seconds = time.monotonic() - started


async def _stored_ids(redis_url: str) -> list[str]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url)
    try:
        return sorted(
            [
                key.decode().rsplit("streamlink:", 1)[1]
                async for key in client.scan_iter(match="*streamlink:*", count=500)
            ]
        )
    finally:
        await client.aclose()


async def _request_streams(imdb: str) -> list[str]:
    """Ask the app for the title's streams; the stream ids of its answer."""
    kind, _, ident = imdb.rpartition("/")
    if not kind:
        kind = "series" if ":" in ident else "movie"
    base = "http://127.0.0.1:" + os.environ.get("PORT", "7979")
    async with httpx.AsyncClient(timeout=120) as http:
        answer = await http.get(f"{base}/api/v1/stremio/stream/{kind}/{ident}.json")
    answer.raise_for_status()
    ids: list[str] = []
    for stream in answer.json().get("streams", []):
        match = _STREAM_ID_RE.search(str(stream.get("url", "")))
        if match:
            ids.append(match.group(1))
    print(f"the app answered {len(ids)} streams for {imdb}")
    return ids


async def _pick_link(
    repo: CacheStreamLinkRepository,
    backend: str,
    redis_url: str,
    args: argparse.Namespace,
) -> CachedStreamLink:
    if args.stream_id:
        ids = [args.stream_id]
    elif args.imdb:
        ids = await _request_streams(args.imdb)
    elif backend == "redis":
        ids = await _stored_ids(redis_url)
    else:
        sys.exit("pass --stream-id or --imdb: only the Redis backend can list links")
    wanted = args.hoster.lower()
    candidates: list[CachedStreamLink] = []
    for stream_id in ids:
        link = await repo.get(stream_id)
        if link and link.is_hls and link.video_url and wanted in link.hoster.lower():
            candidates.append(link)
    if not candidates:
        sys.exit(
            f"no stored HLS link of {args.hoster} among {len(ids)} links;"
            " pass --imdb ID of a title with one"
        )
    return max(candidates, key=lambda link: link.resolved_at)


async def _segments_of(
    http: httpx.AsyncClient, link: CachedStreamLink, headers: dict[str, str]
) -> tuple[list[Segment], str]:
    """The media segments behind the link's playlist and the variant chosen."""
    query = urlsplit(link.video_url).query
    resp = await http.get(link.video_url, headers=headers)
    resp.raise_for_status()
    text, url = resp.text, str(resp.url)
    variant = ""
    if "#EXT-X-STREAM-INF" in text:
        variants = _variants(text, url)
        if not variants:
            sys.exit("the master playlist lists no variant")
        bandwidth, resolution, variant_url = max(variants)
        variant = f", variant {resolution} (declared {bandwidth / 1e6:.1f} Mbit/s)"
        resp = await http.get(_with_query(variant_url, query), headers=headers)
        resp.raise_for_status()
        text, url = resp.text, str(resp.url)
    return _segments(text, url, query), variant


def _report(runs: list[Run]) -> None:
    print("| run | connections | bytes | time | Mbit/s | ranges |")
    print("|---|---|---|---|---|---|")
    for run in runs:
        if run.seconds == 0:
            continue
        rate = f"{run.mbit:.1f}"
        if run.per_segment:
            low, high = min(run.per_segment), max(run.per_segment)
            rate += f" ({low:.1f}–{high:.1f} per segment)"
        ranges = "–"
        if run.ranged:
            ranges = f"{run.honoured} of {run.ranges} answered 206"
            if run.ignored:
                ranges += "; one got the whole segment, the rest skipped"
        failed = f", {run.failed} failed" if run.failed else ""
        print(
            f"| {run.name} | {run.connections} | {_mib(run.bytes)}"
            f" | {run.seconds:.1f} s{failed} | {rate} | {ranges} |"
        )
    fetched = [run for run in runs if run.duration and not run.ignored]
    bitrate = _mbit(
        sum(run.bytes for run in fetched), sum(run.duration for run in fetched)
    )
    one = runs[0].mbit
    needed = math.ceil(1.5 * bitrate / one) if one else 0
    print(
        f"Bitrate {bitrate:.1f} Mbit/s (segment bytes over EXTINF); one connection"
        f" {one:.1f} Mbit/s: a stutter-free playback needs"
        f" ceil(1.5 × {bitrate:.1f} / {one:.1f}) = {needed} connections."
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="probe hls_throughput", description=(__doc__ or "").split("\n\n")[1]
    )
    parser.add_argument("--hoster", default="firestream")
    parser.add_argument("--segments", type=int, default=10, help="per run")
    parser.add_argument("--stream-id", help="a stored stream link's id")
    parser.add_argument(
        "--imdb", help="[movie/|series/]ID: ask the app for the title's streams first"
    )
    return parser


async def main(argv: list[str]) -> None:
    args = _parser().parse_args(argv)
    # The cache and repository log every read at debug level
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    env = os.getenv("SCAVENGARR_CONFIG")
    config = load_config(config_path=Path(env) if env else None)
    cache = create_cache(
        backend=config.cache.backend,
        directory=str(config.cache.directory),
        redis_url=config.cache.redis_url,
    )
    async with cache:
        repo = CacheStreamLinkRepository(cache)
        link = await _pick_link(
            repo, config.cache.backend, config.cache.redis_url, args
        )
    headers = _cdn_headers(link)
    age = (time.time() - link.resolved_at) / 60 if link.resolved_at else 0
    print(
        f"# hls_throughput: {link.hoster} · {link.title[:60]} · cdn"
        f" {cdn(link.video_url)} · link resolved {age:.0f} min ago"
    )
    async with httpx.AsyncClient(
        follow_redirects=True,
        http2=config.http_http2,
        timeout=httpx.Timeout(60.0, connect=15.0),
        limits=httpx.Limits(max_connections=16, max_keepalive_connections=16),
    ) as http:
        segments, variant = await _segments_of(http, link, headers)
        per_run = min(args.segments, len(segments) // 4)
        if per_run == 0:
            sys.exit(f"the playlist has {len(segments)} segments, 4 needed")
        start = (len(segments) - 4 * per_run) // 2
        chosen = segments[start : start + 4 * per_run]
        mean = sum(segment.duration for segment in chosen) / len(chosen)
        print(
            f"{per_run} segments per run, {mean:.1f} s each (EXTINF), from segment"
            f" {start} of {len(segments)}{variant}"
        )
        runs = [
            Run("one connection", 1),
            Run(f"read-ahead {READ_AHEAD}", READ_AHEAD + 1),
            Run(f"{RANGES} ranges", 1, ranged=True),
            Run(f"read-ahead {READ_AHEAD} × {RANGES} ranges", READ_AHEAD + 1, True),
        ]
        fetcher = Fetcher(http, headers)
        ignored = False
        for index, run in enumerate(runs):
            if run.ranged and ignored:
                print(f"  {run.name}: skipped, the CDN ignores ranges")
                continue
            await execute(fetcher, chosen[index * per_run : (index + 1) * per_run], run)
            ignored = ignored or run.ignored
    _report(runs)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
