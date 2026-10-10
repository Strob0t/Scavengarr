"""Run a Stremio round: first answer, cached answer and playable streams per title.

Usage (against a running server):
    poetry run python scripts/stremio_round.py --base https://scavengarr.lan \\
        [--insecure] [--ids-file docs/plans/round-titles.txt] [--out FILE] \\
        [--no-playcheck] [--portainer [--container scavengarr]]

Per id of the ids file (one after another, never in parallel): the stream
request once (the first answer: wall time, streams and the ``X-Cache`` and
``X-Search-Complete`` headers when the route sends them), again right after
it (the cached answer), then the play check of the first answer's streams
(``stremio_playcheck.py``'s check per stream, from the machine the script
runs on). Prints the round table of
``docs/plans/stremio-latency.md``: one row per id and a summary row with
medians and totals; ``--out`` appends it to a Markdown file.

``--portainer`` adds the CPU seconds the container's Python and Chromium
processes used during the round (``stremio_profile.py``'s reading;
``PORTAINER_URL`` and ``PORTAINER_API_KEY`` as in ``portainer.py``).

Load: a round asks every plugin's site for every title, so run it against
production at most once an hour.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from portainer import Portainer, credentials
from stremio_playcheck import check_stream
from stremio_profile import cpu_seconds

DEFAULT_IDS_FILE = Path(__file__).resolve().parents[1] / "docs/plans/round-titles.txt"

# A first answer waits for the plugins' search (deadline 60 s in production)
_STREAM_TIMEOUT = 120.0
# Per connect, read and write of a play check's requests; the check as a
# whole ends at stremio_playcheck's own deadline (_CHECK_TIMEOUT there)
_HTTP_TIMEOUT = 30.0


@dataclass(frozen=True)
class Row:
    """One title's measurements."""

    sid: str
    title: str
    first_s: float
    first_streams: int
    cache: str
    complete: str  # X-Search-Complete of the first answer
    cached_s: float
    cached_streams: int
    playable: int | None  # None without the play check


def read_ids(path: Path) -> list[tuple[str, str]]:
    """``(id, title)`` per line of an ids file: ``movie/tt0130827  # Lola rennt``.

    Blank lines and lines starting with ``#`` are skipped; a line without a
    comment uses the id as its title.
    """
    ids: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        sid, _, title = line.partition("#")
        sid = sid.strip()
        if sid:
            ids.append((sid, title.strip() or sid))
    return ids


async def _request(
    client: httpx.AsyncClient, base: str, sid: str
) -> tuple[float, list[dict[str, Any]], str, str]:
    """One stream request: wall time, streams, ``X-Cache`` and
    ``X-Search-Complete`` (``–`` without them)."""
    start = time.monotonic()
    resp = await client.get(
        f"{base}/api/v1/stremio/stream/{sid}.json", timeout=_STREAM_TIMEOUT
    )
    wall = time.monotonic() - start
    streams = resp.json().get("streams", []) if resp.is_success else []
    return (
        wall,
        streams,
        resp.headers.get("x-cache", "–"),
        resp.headers.get("x-search-complete", "–"),
    )


async def measure(
    client: httpx.AsyncClient, base: str, sid: str, title: str, *, playcheck: bool
) -> Row:
    """First answer, cached answer and (with *playcheck*) its playable streams."""
    first_s, streams, cache, complete = await _request(client, base, sid)
    cached_s, cached, _, _ = await _request(client, base, sid)
    playable = None
    if playcheck:
        verdicts = [await check_stream(client, stream) for stream in streams]
        playable = sum(v.startswith("OK") for v in verdicts)
    return Row(
        sid=sid,
        title=title,
        first_s=first_s,
        first_streams=len(streams),
        cache=cache,
        complete=complete,
        cached_s=cached_s,
        cached_streams=len(cached),
        playable=playable,
    )


def _playable(playable: int | None, streams: int) -> str:
    return "–" if playable is None else f"{playable} of {streams}"


def render(rows: list[Row], cpu: dict[str, float] | None = None) -> str:
    """The round table: one row per title, a summary row, titles without stream."""
    lines = [
        "| Title | First answer | Streams | X-Cache | Complete "
        "| Cached answer | Streams | Playable |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.title} (`{r.sid}`) | {r.first_s:.1f} s | {r.first_streams} "
            f"| {r.cache} | {r.complete} | {r.cached_s:.2f} s "
            f"| {r.cached_streams} "
            f"| {_playable(r.playable, r.first_streams)} |"
        )
    checked = [r for r in rows if r.playable is not None]
    playable = (
        _playable(
            sum(r.playable or 0 for r in checked),
            sum(r.first_streams for r in checked),
        )
        if checked
        else "–"
    )
    hits = sum(r.cache == "HIT" for r in rows)
    sent = [r for r in rows if r.complete != "–"]
    complete = sum(r.complete == "true" for r in sent)
    lines.append(
        f"| **Median / total** ({len(rows)} titles) "
        f"| {statistics.median(r.first_s for r in rows):.1f} s "
        f"| {sum(r.first_streams for r in rows)} "
        f"| {f'{hits} HIT' if any(r.cache != '–' for r in rows) else '–'} "
        f"| {f'{complete} of {len(sent)} complete' if sent else '–'} "
        f"| {statistics.median(r.cached_s for r in rows):.2f} s "
        f"| {sum(r.cached_streams for r in rows)} | {playable} |"
    )
    lines.append("")
    lines.append(
        "Titles without stream: "
        f"{sum(r.first_streams == 0 for r in rows)} of {len(rows)} (first answer), "
        f"{sum(r.cached_streams == 0 for r in rows)} of {len(rows)} (cached answer); "
        f"max first answer {max(r.first_s for r in rows):.1f} s."
    )
    if cpu is not None:
        lines.append(
            f"CPU of the container during the round: Python {cpu['python']:.1f} s, "
            f"Chromium {cpu['chrome']:.1f} s."
        )
    return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> str:
    """Measure every id of the ids file one after another; return the table."""
    ids = read_ids(args.ids_file)
    if not ids:
        raise SystemExit(f"no ids in {args.ids_file}")
    container = Portainer(args.container, *credentials()) if args.portainer else None
    before = cpu_seconds(container) if container else None
    rows: list[Row] = []
    async with httpx.AsyncClient(
        verify=not args.insecure, follow_redirects=True, timeout=_HTTP_TIMEOUT
    ) as client:
        for sid, title in ids:
            row = await measure(
                client, args.base, sid, title, playcheck=not args.no_playcheck
            )
            print(
                f"{sid}: first {row.first_s:.1f} s, {row.first_streams} streams; "
                f"cached {row.cached_s:.2f} s, {row.cached_streams} streams; "
                f"playable {_playable(row.playable, row.first_streams)}"
            )
            rows.append(row)
    cpu = None
    if container and before:
        after = cpu_seconds(container)
        cpu = {kind: after[kind] - before[kind] for kind in after}
    return render(rows, cpu)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base", required=True, help="the instance's base URL")
    parser.add_argument("--insecure", action="store_true", help="skip TLS checks")
    parser.add_argument("--ids-file", type=Path, default=DEFAULT_IDS_FILE)
    parser.add_argument("--out", type=Path, help="Markdown file to append to")
    parser.add_argument("--no-playcheck", action="store_true")
    parser.add_argument(
        "--portainer", action="store_true", help="the container's CPU (Portainer)"
    )
    parser.add_argument("--container", default="scavengarr")
    args = parser.parse_args(argv)

    table = asyncio.run(run(args))
    print(table)
    if args.out:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        with args.out.open("a", encoding="utf-8") as out:
            out.write(f"\nRound of {stamp} (`scripts/stremio_round.py`):\n\n{table}")


if __name__ == "__main__":
    main()
