"""Stremio-style measurement: latency, streams and sources per title.

Usage (against a running server, e.g. ``poetry run start --port 7979``):
    poetry run python scripts/stremio_measure.py [--pause 150] [--base URL]

Requests each title's streams like Stremio does. Title groups run one after
another with a pause in between (home routers block bursts of new
connections). Prints one line per title and a summary: the harness of the
runs in ``docs/plans/stremio-latency.md``.
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections import Counter

import httpx

GROUPS: dict[str, list[tuple[str, str, str]]] = {
    "german-movies": [
        ("movie", "tt0248408", "Der Schuh des Manitu"),
        ("movie", "tt0130827", "Lola rennt"),
        ("movie", "tt0301357", "Good Bye, Lenin!"),
        ("movie", "tt1016150", "Im Westen nichts Neues"),
    ],
    "popular-movies": [
        ("movie", "tt15398776", "Oppenheimer"),
        ("movie", "tt15239678", "Dune: Part Two"),
        ("movie", "tt1375666", "Inception"),
        ("movie", "tt0816692", "Interstellar"),
    ],
    "series": [
        ("series", "tt0903747:1:1", "Breaking Bad S01E01"),
        ("series", "tt5753856:1:1", "Dark S01E01"),
        ("series", "tt4574334:4:1", "Stranger Things S04E01"),
        ("series", "tt6468322:1:1", "Haus des Geldes S01E01"),
        ("series", "tt3581920:1:1", "The Last of Us S01E01"),
    ],
    "anime": [
        ("series", "tt0388629:1:1", "One Piece S01E01"),
        ("series", "tt2560140:1:1", "Attack on Titan S01E01"),
        ("series", "tt9335498:1:1", "Demon Slayer S01E01"),
        ("series", "tt22248376:1:1", "Frieren S01E01"),
    ],
}


def _source(stream: dict) -> str:
    lines = (stream.get("description") or "").splitlines()
    return lines[-1] if lines else "?"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:7979")
    parser.add_argument("--pause", type=float, default=120)
    parser.add_argument("--groups", nargs="*", default=list(GROUPS))
    args = parser.parse_args()

    latencies: list[float] = []
    counts: list[int] = []
    sources: Counter[str] = Counter()
    with httpx.Client(timeout=60) as client:
        for index, group in enumerate(args.groups):
            if index:
                time.sleep(args.pause)
            print(f"== {group}", flush=True)
            for ctype, sid, label in GROUPS[group]:
                url = f"{args.base}/api/v1/stremio/stream/{ctype}/{sid}.json"
                start = time.monotonic()
                resp = client.get(url)
                elapsed = time.monotonic() - start
                streams = resp.json().get("streams", []) if resp.is_success else []
                latencies.append(elapsed)
                counts.append(len(streams))
                found = [_source(s) for s in streams]
                sources.update(f.split(" · ")[-1] for f in found)
                names = [s["name"].replace("Scavengarr\n", "") for s in streams]
                listed = ", ".join(
                    f"{f} {n}" for f, n in zip(found, names, strict=True)
                )
                print(
                    f"  {label:28} {elapsed:5.1f}s {len(streams):2} streams {listed}",
                    flush=True,
                )
    print(
        f"latency median {statistics.median(latencies):.1f}s "
        f"max {max(latencies):.1f}s; streams total {sum(counts)}, "
        f"titles without stream {sum(1 for c in counts if c == 0)}/{len(counts)}"
    )
    print("streams per plugin:", dict(sources.most_common()))


if __name__ == "__main__":
    main()
