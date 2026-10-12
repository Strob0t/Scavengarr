"""Stored stream qualities: production's resolutions counted per resolver.

    probe stream_quality

Reads the hoster resolver registry's snapshot (``hoster_state:v1``, which
``HosterStateStore`` writes every 30 s when something changed: the
resolutions of the last hour, the dead links of the last 15 minutes) from
the app's cache backend and counts per resolver its resolutions, dead links
(no stream), streams, HLS streams, streams per known quality class and
streams with a size (``size_bytes``), then the totals.

filemoon, supervideo and voe name a quality themselves, and the registry
keeps the higher of theirs and the measured one (``_measured``): only the
other resolvers' known qualities are the playback check's own (I2,
openspec add-stream-media-quality), so the three are counted apart.

Read-only (a snapshot it cannot read is reported, never deleted); prints
resolver names and counts only: no URLs, no headers.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from scavengarr.domain.entities.stremio import StreamQuality
from scavengarr.infrastructure.cache.cache_factory import create_cache
from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.hoster_resolvers.state_store import _KEY, _invalid

# The resolvers that name a quality themselves (the registry keeps the
# higher of theirs and the measured one)
SELF_NAMED = ("filemoon", "supervideo", "voe")
# The known classes, best first, with their column names
CLASSES = (
    (StreamQuality.UHD_4K, "4K"),
    (StreamQuality.HD_1080P, "1080p"),
    (StreamQuality.HD_720P, "720p"),
    (StreamQuality.SD, "SD"),
    (StreamQuality.TS, "TS"),
    (StreamQuality.CAM, "CAM"),
)
_NO_RESOLVER = "(none)"


@dataclass
class Counts:
    """One resolver's stored resolutions."""

    resolutions: int = 0
    dead: int = 0
    streams: int = 0
    hls: int = 0
    sized: int = 0
    quality: Counter[StreamQuality] = field(default_factory=Counter)

    @property
    def known(self) -> int:
        return sum(self.quality.values())


def _quality(value: Any) -> StreamQuality:
    try:
        return StreamQuality(int(value))
    except (TypeError, ValueError):
        return StreamQuality.UNKNOWN


def count(snapshot: Mapping[str, Any]) -> dict[str, Counts]:
    """The snapshot's resolutions counted per resolver."""
    counts: dict[str, Counts] = {}
    for entry in snapshot.get("results") or []:
        resolver = str(entry.get("resolver") or _NO_RESOLVER)
        tally = counts.setdefault(resolver, Counts())
        tally.resolutions += 1
        stream = entry.get("stream")
        if not isinstance(stream, Mapping):
            tally.dead += 1
            continue
        tally.streams += 1
        tally.hls += bool(stream.get("is_hls"))
        tally.sized += bool(stream.get("size_bytes"))
        quality = _quality(stream.get("quality"))
        if quality is not StreamQuality.UNKNOWN:
            tally.quality[quality] += 1
    return counts


_HEADER = (
    "| resolver | resolutions | dead | streams | HLS | "
    + " | ".join(name for _, name in CLASSES)
    + " | known | size |"
)
_RULE = "|---" * (6 + len(CLASSES)) + "|"


def _row(resolver: str, tally: Counts) -> str:
    cells = [tally.resolutions, tally.dead, tally.streams, tally.hls]
    cells += [tally.quality[quality] for quality, _ in CLASSES]
    cells += [tally.known, tally.sized]
    return f"| {resolver} | " + " | ".join(str(cell) for cell in cells) + " |"


def _share(label: str, group: list[Counts]) -> str:
    streams = sum(tally.streams for tally in group)
    if not streams:
        return f"{label}: no streams"
    known = sum(tally.known for tally in group)
    sized = sum(tally.sized for tally in group)
    return (
        f"{label}: {known} of {streams} streams with a known quality"
        f" ({100 * known / streams:.0f} %), {sized} with a size"
    )


def report_lines(snapshot: Mapping[str, Any], *, now: float) -> list[str]:
    counts = count(snapshot)
    total = sum(tally.resolutions for tally in counts.values())
    age = now - float(snapshot.get("taken_at") or now)
    lines = [
        f"{_KEY} taken {age:.0f} s ago: {total} resolutions by {len(counts)} resolvers"
    ]
    measured = {r: c for r, c in counts.items() if r not in SELF_NAMED}
    self_named = {r: c for r, c in counts.items() if r in SELF_NAMED}
    names = ", ".join(SELF_NAMED)
    for title, group in (
        (f"measured (all but {names})", measured),
        (f"named by the resolver ({names})", self_named),
    ):
        lines += ["", f"### {title}", _HEADER, _RULE]
        lines += [_row(resolver, group[resolver]) for resolver in sorted(group)]
        lines += ["", _share(title, list(group.values()))]
    return lines


async def main() -> None:
    env = os.getenv("SCAVENGARR_CONFIG")
    config = load_config(config_path=Path(env) if env else None)
    cache = create_cache(
        backend=config.cache.backend,
        directory=str(config.cache.directory),
        redis_url=config.cache.redis_url,
    )
    async with cache:
        snapshot = await cache.get(_KEY)
    if snapshot is None:
        print(f"no {_KEY} snapshot")
        return
    if (reason := _invalid(snapshot)) is not None:
        print(f"{_KEY} not readable: {reason}")
        return
    print("\n".join(report_lines(snapshot, now=time.time())))


if __name__ == "__main__":
    # The cache logs every read at debug level
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
    )
    asyncio.run(main())
