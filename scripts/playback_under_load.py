"""Segment latency of a proxied HLS playback while the server searches.

Usage (from a machine on the server's network):
    poetry run python scripts/playback_under_load.py --base https://scavengarr.lan \\
        --stream-id ID [--insecure] [--label idle] [--segments 20] \\
        [--search movie/tt0816692 ...] [--search-at 10] [--tail 30] \\
        [--portainer [--container scavengarr]] [--out FILE]

The player: the stream's master playlist through the proxy
(``/api/v1/stremio/proxy/<ID>/scavengarr.m3u8``; ``ID`` is a stored stream
link's id, ``prodctl.py probe links`` lists the recent ones), the variant
with the highest bandwidth, then ``--segments`` media segments from the
start of the title, one after another at the playback pace: the next fetch
starts when the previous segment's duration (``EXTINF``) has passed since
the previous fetch began, as a player with a full buffer asks. Per segment
the time to the response headers and to the last byte; a segment slower
than its own duration is a stall (a player with an empty buffer would
stop). The proxy's read-ahead follows this playback as it follows a player
(``hls_readahead`` hits from the second segment on).

Load: the ``--search`` ids are requested one after another
(``/api/v1/stremio/stream/<id>.json``: a search of every plugin for an
uncached title, resolutions in the background) from ``--search-at`` seconds
into the playback, which goes on until ``--tail`` seconds after the last
answer so that the background work is covered too; each answer's wall
time, streams and ``X-Cache`` and ``X-Search-Complete`` headers are
reported. Without ``--search`` the run is the idle reference.

Server view: ``/metrics`` before and after the run gives the run's deltas:
the proxy's segment stage (``hls_proxy``, kind ``segment``: until the
answer starts), the read-ahead's fetches and outcomes, the plugin
searches' durations and the event-loop lag; the quantiles are histogram
bucket bounds (upper bounds). ``--portainer`` samples the container's CPU
(``docker stats`` through Portainer about every 5 s; ``PORTAINER_URL`` and
``PORTAINER_API_KEY`` as in ``portainer.py``). Prints the two Markdown rows
of ``docs/plans/pi-performance.md`` → "Playback under search load" (the
player's view, the server's view); ``--out`` appends them. Prints no URLs.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import httpx
from portainer import Portainer
from prodctl import cpu_cores, portainer_client
from stremio_playcheck import _PLAYER_UA

MASTER = "scavengarr.m3u8"
PLAYER_HEADER = (
    "| Run | Segments (video) | Stalls | Headers p50 / p90 (ms) "
    "| Segment p50 / p90 / max (ms) | Mbit/s | CPU p50 / max (cores) | Searches |\n"
    "|---|---|---|---|---|---|---|---|"
)
SERVER_HEADER = (
    "| Run | plugin_search n / mean / p90 | Proxy segment n / mean / p90 "
    "| Read-ahead n / mean | ok / hit / miss / failed | Loop lag p50 / p90 / max |\n"
    "|---|---|---|---|---|---|"
)
_STATS_EVERY = 5.0
_SEARCH_TIMEOUT = 180.0
_STREAM_INF = re.compile(r"#EXT-X-STREAM-INF:(?P<attrs>.*)")
_BANDWIDTH = re.compile(r"BANDWIDTH=(\d+)")
_EXTINF = re.compile(r"#EXTINF:([\d.]+)")
_SERIES = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?P<labels>\{[^}]*\})?\s+(?P<value>\S+)"
)
_LABEL = re.compile(r'(\w+)="([^"]*)"')


@dataclass(frozen=True)
class SegmentFetch:
    """One segment the player fetched: its video duration and the timings."""

    duration: float
    headers_after: float
    total: float
    size: int
    status: int

    @property
    def stalled(self) -> bool:
        return self.duration > 0 and self.total > self.duration


@dataclass(frozen=True)
class SearchAnswer:
    """One stream request's answer."""

    stream_id: str
    wall: float
    status: int
    streams: int
    cache: str
    complete: str


@dataclass
class Run:
    """What one run measured."""

    label: str
    segments: list[SegmentFetch] = field(default_factory=list)
    answers: list[SearchAnswer] = field(default_factory=list)
    cpu: list[float] = field(default_factory=list)
    before: dict[str, float] = field(default_factory=dict)
    after: dict[str, float] = field(default_factory=dict)


def best_variant(master: str, base: str) -> str:
    """The URL of the master playlist's variant with the highest bandwidth;
    *base* itself when *master* is a media playlist."""
    lines = [line.strip() for line in master.splitlines()]
    best: tuple[int, str] | None = None
    for position, line in enumerate(lines):
        found = _STREAM_INF.match(line)
        if found is None:
            continue
        uri = next(
            (ln for ln in lines[position + 1 :] if ln and not ln.startswith("#")),
            None,
        )
        if uri is None:
            continue
        bandwidth = _BANDWIDTH.search(found.group("attrs"))
        rank = int(bandwidth.group(1)) if bandwidth else 0
        if best is None or rank > best[0]:
            best = (rank, urljoin(base, uri))
    return best[1] if best else base


def media_segments(playlist: str, base: str) -> list[tuple[float, str]]:
    """``(duration, URL)`` per segment of a media playlist, in order."""
    segments: list[tuple[float, str]] = []
    duration = 0.0
    for raw in playlist.splitlines():
        line = raw.strip()
        if not line:
            continue
        timed = _EXTINF.match(line)
        if timed is not None:
            duration = float(timed.group(1))
            continue
        if line.startswith("#"):
            continue
        segments.append((duration, urljoin(base, line)))
        duration = 0.0
    return segments


def parse_metrics(text: str) -> dict[str, float]:
    """Prometheus text: series (the name with its labels) -> value."""
    series: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        found = _SERIES.match(line)
        if found is None:
            continue
        try:
            value = float(found.group("value"))
        except ValueError:
            continue
        series[found.group("name") + (found.group("labels") or "")] = value
    return series


def deltas(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    """What the series grew by between the two readings."""
    return {key: value - before.get(key, 0.0) for key, value in after.items()}


def _labels(text: str) -> dict[str, str]:
    return dict(_LABEL.findall(text))


def histogram(
    series: dict[str, float], name: str, **match: str
) -> tuple[int, float, dict[float, float]]:
    """Count, mean and cumulative buckets (bound -> count) of the histogram
    *name*, over its series whose labels carry *match*, summed over the
    other labels."""
    count = total = 0.0
    buckets: dict[float, float] = {}
    for key, value in series.items():
        if not key.startswith(f"{name}_"):
            continue
        suffix, _, rest = key[len(name) + 1 :].partition("{")
        labels = _labels(rest)
        if any(labels.get(k) != v for k, v in match.items()):
            continue
        if suffix == "count":
            count += value
        elif suffix == "sum":
            total += value
        elif suffix == "bucket":
            bound = float(labels.get("le", "+Inf").replace("+Inf", "inf"))
            buckets[bound] = buckets.get(bound, 0.0) + value
    return int(count), (total / count if count else 0.0), buckets


def quantile(buckets: dict[float, float], q: float) -> float | None:
    """The bucket bound under which the share *q* of the observations falls;
    ``None`` without observations."""
    total = buckets.get(float("inf"), 0.0)
    if total <= 0:
        return None
    for bound in sorted(buckets):
        if buckets[bound] >= q * total:
            return bound
    return float("inf")


def outcomes(series: dict[str, float], name: str, **match: str) -> dict[str, int]:
    """Outcome -> count of the counter *name* (its ``_total`` series)."""
    counts: dict[str, int] = {}
    prefix = f"{name}_total"
    for key, value in series.items():
        if not key.startswith(prefix):
            continue
        labels = _labels(key[len(prefix) :])
        if any(labels.get(k) != v for k, v in match.items()):
            continue
        outcome = labels.get("outcome", "")
        counts[outcome] = counts.get(outcome, 0) + int(value)
    return counts


def pct(values: list[float], q: float) -> float:
    """The value at the share *q* of the sorted *values* (nearest rank)."""
    ordered = sorted(values)
    return ordered[round(q * (len(ordered) - 1))]


async def play(
    client: httpx.AsyncClient,
    master_url: str,
    count: int,
    run: Run,
    busy: asyncio.Event,
) -> None:
    """Play at least *count* segments at the playback pace, and on while
    *busy* is set (the load phase)."""
    resp = await client.get(master_url)
    if resp.status_code != 200 or not resp.text.startswith("#EXTM3U"):
        raise SystemExit(f"master playlist: HTTP {resp.status_code}")
    variant_url = best_variant(resp.text, str(resp.url))
    if variant_url != str(resp.url):
        resp = await client.get(variant_url)
        if resp.status_code != 200 or not resp.text.startswith("#EXTM3U"):
            raise SystemExit(f"variant playlist: HTTP {resp.status_code}")
    segments = media_segments(resp.text, str(resp.url))
    if not segments:
        raise SystemExit("the playlist lists no segments")
    for position, (duration, url) in enumerate(segments):
        if position >= count and not busy.is_set():
            break
        started = time.monotonic()
        size = 0
        try:
            async with client.stream("GET", url) as seg:
                headers_after = time.monotonic() - started
                async for chunk in seg.aiter_bytes():
                    size += len(chunk)
                status = seg.status_code
        except httpx.HTTPError as exc:
            raise SystemExit(f"segment {position + 1}: {type(exc).__name__}") from exc
        total = time.monotonic() - started
        run.segments.append(SegmentFetch(duration, headers_after, total, size, status))
        print(
            f"  segment {position + 1}: {status}, {size / 1e6:.2f} MB in {total:.2f} s"
            f" (headers after {headers_after:.2f} s, {duration:.0f} s of video)",
            file=sys.stderr,
        )
        wait = duration - (time.monotonic() - started)
        if wait > 0:
            await asyncio.sleep(wait)


async def search(
    client: httpx.AsyncClient,
    base: str,
    ids: list[str],
    start_at: float,
    tail: float,
    run: Run,
    busy: asyncio.Event,
) -> None:
    """Request the *ids* one after another from *start_at* seconds on;
    *busy* is set until *tail* seconds after the last answer."""
    if not ids:
        return
    busy.set()
    try:
        await asyncio.sleep(start_at)
        for stream_id in ids:
            kind, _, rest = stream_id.partition("/")
            started = time.monotonic()
            try:
                resp = await client.get(
                    f"{base}/api/v1/stremio/stream/{kind}/{rest}.json",
                    timeout=_SEARCH_TIMEOUT,
                )
            except httpx.HTTPError as exc:
                run.answers.append(
                    SearchAnswer(
                        stream_id,
                        time.monotonic() - started,
                        0,
                        0,
                        type(exc).__name__,
                        "-",
                    )
                )
                continue
            try:
                streams = len(resp.json().get("streams", []))
            except ValueError:
                streams = 0
            answer = SearchAnswer(
                stream_id,
                time.monotonic() - started,
                resp.status_code,
                streams,
                resp.headers.get("x-cache", "-"),
                resp.headers.get("x-search-complete", "-"),
            )
            run.answers.append(answer)
            print(
                f"  search {stream_id}: HTTP {answer.status}, {streams} streams"
                f" in {answer.wall:.1f} s",
                file=sys.stderr,
            )
        await asyncio.sleep(tail)
    finally:
        busy.clear()


async def sample_cpu(container: Portainer, run: Run, stop: asyncio.Event) -> None:
    """One ``docker stats`` sample about every ``_STATS_EVERY`` seconds."""
    while not stop.is_set():
        try:
            sample = await asyncio.to_thread(container.stats)
        except httpx.HTTPError as exc:
            print(f"  stats: {type(exc).__name__}", file=sys.stderr)
        else:
            run.cpu.append(cpu_cores(sample)[0])
        try:
            await asyncio.wait_for(stop.wait(), _STATS_EVERY)
        except TimeoutError:
            continue


def _bound(value: float | None) -> str:
    if value is None:
        return "-"
    return "inf" if value == float("inf") else f"≤{value:g}"


def player_row(run: Run) -> str:
    """The player's view of the run."""
    fetched = run.segments
    if not fetched:
        return f"| {run.label} | 0 | - | - | - | - | - | - |"
    totals = [s.total for s in fetched]
    heads = [s.headers_after for s in fetched]
    video = sum(s.duration for s in fetched)
    mbit = sum(s.size for s in fetched) * 8 / sum(totals) / 1e6 if sum(totals) else 0.0
    cpu = f"{pct(run.cpu, 0.5):.2f} / {max(run.cpu):.2f}" if run.cpu else "-"
    searches = (
        ", ".join(
            f"{a.wall:.1f} s ({a.streams} streams, {a.cache})" for a in run.answers
        )
        or "none"
    )
    return (
        f"| {run.label} | {len(fetched)} ({video:.0f} s)"
        f" | {sum(s.stalled for s in fetched)}"
        f" | {pct(heads, 0.5) * 1000:.0f} / {pct(heads, 0.9) * 1000:.0f}"
        f" | {pct(totals, 0.5) * 1000:.0f} / {pct(totals, 0.9) * 1000:.0f}"
        f" / {max(totals) * 1000:.0f} | {mbit:.1f} | {cpu} | {searches} |"
    )


def server_row(run: Run) -> str:
    """The server's view of the run: the metrics' deltas."""
    grown = deltas(run.before, run.after)
    searches, search_mean, search_buckets = histogram(
        grown, "scavengarr_plugin_search_seconds"
    )
    segments, segment_mean, segment_buckets = histogram(
        grown, "scavengarr_hls_proxy_seconds", kind="segment"
    )
    ahead, ahead_mean, _ = histogram(grown, "scavengarr_hls_readahead_seconds")
    ahead_outcomes = outcomes(grown, "scavengarr_hls_readahead")
    _, _, lag_buckets = histogram(grown, "scavengarr_event_loop_lag_seconds")
    return (
        f"| {run.label} | {searches} / {search_mean:.1f} s"
        f" / {_bound(quantile(search_buckets, 0.9))} s"
        f" | {segments} / {segment_mean * 1000:.0f} ms"
        f" / {_bound(quantile(segment_buckets, 0.9))} s"
        f" | {ahead} / {ahead_mean:.2f} s"
        f" | {ahead_outcomes.get('ok', 0)} / {ahead_outcomes.get('hit', 0)}"
        f" / {ahead_outcomes.get('miss', 0)} / {ahead_outcomes.get('failed', 0)}"
        f" | {_bound(quantile(lag_buckets, 0.5))}"
        f" / {_bound(quantile(lag_buckets, 0.9))}"
        f" / {_bound(quantile(lag_buckets, 1.0))} s |"
    )


async def _metrics(client: httpx.AsyncClient, base: str) -> dict[str, float]:
    resp = await client.get(f"{base}/metrics")
    if resp.status_code != 200:
        raise SystemExit(f"metrics: HTTP {resp.status_code}")
    return parse_metrics(resp.text)


async def run_once(args: argparse.Namespace) -> Run:
    base = args.base.rstrip("/")
    run = Run(args.label or ("search" if args.search else "idle"))
    container = portainer_client(args.container, timeout=30) if args.portainer else None
    async with httpx.AsyncClient(
        verify=not args.insecure,
        timeout=httpx.Timeout(60.0, connect=10.0),
        headers={"User-Agent": _PLAYER_UA},
        follow_redirects=True,
    ) as client:
        run.before = await _metrics(client, base)
        busy = asyncio.Event()
        stop = asyncio.Event()
        sampler = (
            asyncio.create_task(sample_cpu(container, run, stop)) if container else None
        )
        try:
            await asyncio.gather(
                play(
                    client,
                    f"{base}/api/v1/stremio/proxy/{args.stream_id}/{MASTER}",
                    args.segments,
                    run,
                    busy,
                ),
                search(client, base, args.search, args.search_at, args.tail, run, busy),
            )
        finally:
            stop.set()
            if sampler is not None:
                await sampler
        run.after = await _metrics(client, base)
    return run


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", default="http://127.0.0.1:7979")
    parser.add_argument(
        "--insecure", action="store_true", help="skip TLS verification (a local CA)"
    )
    parser.add_argument(
        "--stream-id", required=True, help="a stored stream link's id (probe links)"
    )
    parser.add_argument("--label", help="the rows' label; default idle or search")
    parser.add_argument(
        "--segments", type=int, default=20, help="segments to play at least"
    )
    parser.add_argument(
        "--search",
        nargs="*",
        default=[],
        help="Stremio ids to request during the playback",
    )
    parser.add_argument(
        "--search-at", type=float, default=10.0, help="seconds into the playback"
    )
    parser.add_argument(
        "--tail",
        type=float,
        default=30.0,
        help="seconds to play on after the last answer",
    )
    parser.add_argument(
        "--portainer", action="store_true", help="sample the container's CPU"
    )
    parser.add_argument("--container", default="scavengarr")
    parser.add_argument(
        "--out", type=Path, help="append the rows to this Markdown file"
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    run = asyncio.run(run_once(args))
    block = "\n".join(
        [PLAYER_HEADER, player_row(run), "", SERVER_HEADER, server_row(run)]
    )
    print(block)
    for answer in run.answers:
        print(
            f"search {answer.stream_id}: HTTP {answer.status},"
            f" {answer.streams} streams,"
            f" {answer.wall:.1f} s, X-Cache {answer.cache},"
            f" X-Search-Complete {answer.complete}"
        )
    if args.out is not None:
        with args.out.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{block}\n")


if __name__ == "__main__":
    main()
