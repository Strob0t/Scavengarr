"""The post-deploy check's evaluation: pass or fail per check, one table.

``prodctl.py check`` gathers the inputs (the container's row and start time,
its log since the start, four stream answers through the public endpoint)
and this module judges them, so the judgement is tested from fixtures
without a network:

1. the container is running and healthy;
2. the newest ``app_startup_complete`` record is younger than the
   container's start and names the version;
3. ``config_unknown_keys`` of that start: a warning, not a failure;
4. ``hoster_state_restored`` and ``plugin_history_restored`` of that start;
5. the three anime ids answer streams whose description carries the
   episode code (``S05E02``: the sites' release name, or the code the
   stream builder adds); the request's ``http_request`` record shows it
   reached the container, and its ``stremio_episode_ref`` record (DEBUG)
   names the placement when the log level has it;
6. the movie id answers within the deadline with at least one stream.

Stream descriptions are the only site data in the table; the caller masks
the output (``portainer.mask``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

Status = Literal["pass", "fail", "warn"]

# The ids the deploy check of 2026-10-10 settled on: an IMDb long-runner at
# the season's position, the same episode as a kitsu id (absolute 1089),
# a short series, and a movie
ANIME_IDS: tuple[tuple[str, str], ...] = (
    ("series/tt0388629:5:2", "S05E02"),
    ("series/kitsu:12:1089", "S22E04"),
    ("series/tt9335498:2:1", "S02E01"),
)
MOVIE_ID = "movie/tt0816692"

_STARTUP = "app_startup_complete"
_RESTORED = ("hoster_state_restored", "plugin_history_restored")
_CODE = re.compile(r"S\d{2}E\d{2}", re.I)


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


@dataclass(frozen=True)
class Answer:
    """One stream request through the public endpoint."""

    id: str
    status: int | None
    streams: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0
    request_id: str | None = None
    error: str | None = None


def parse_time(value: str) -> float:
    """Unix seconds of an ISO timestamp (Docker's nanoseconds cut to micro)."""
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, _, rest = text.partition(".")
        digits = ""
        while rest and rest[0].isdigit():
            digits += rest[0]
            rest = rest[1:]
        text = f"{head}.{digits[:6].ljust(6, '0')}{rest}"
    stamp = datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.timestamp()


def container_check(containers: Iterable[dict[str, Any]], name: str) -> Check:
    """Running and healthy, from the ``State`` and ``Status`` of ``ps``."""
    for row in containers:
        if name in (n.lstrip("/") for n in row.get("Names", [])):
            state, status = row.get("State", ""), row.get("Status", "")
            ok = state == "running" and "(healthy)" in status
            return Check("container", "pass" if ok else "fail", f"{state}, {status}")
    return Check("container", "fail", f"no container {name!r}")


def _records_of(records: Iterable[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [r for r in records if r.get("event") == event]


def startup_checks(records: Sequence[dict[str, Any]], started_at: float) -> list[Check]:
    """Checks 2 to 4 from the log since the container's start."""
    checks: list[Check] = []
    startups = _records_of(records, _STARTUP)
    newest = startups[-1] if startups else None
    stamp = parse_time(str(newest.get("timestamp", ""))) if newest else None
    version = newest.get("version") if newest else None
    if newest is None:
        checks.append(Check("startup", "fail", "no app_startup_complete record"))
    elif stamp is None or stamp < started_at:
        checks.append(Check("startup", "fail", "older than the container's start"))
    elif not version or version == "unknown":
        checks.append(Check("startup", "fail", "no version in the startup record"))
    else:
        age = datetime.fromtimestamp(stamp, UTC).strftime("%H:%M:%S")
        checks.append(Check("startup", "pass", f"version {version} at {age} UTC"))

    unknown = _records_of(records, "config_unknown_keys")
    keys = unknown[-1].get("keys") if unknown else None
    checks.append(
        Check("unknown keys", "warn", ", ".join(map(str, keys)))
        if keys
        else Check("unknown keys", "pass", "none")
    )

    for event in _RESTORED:
        found = _records_of(records, event)
        if found:
            last = found[-1]
            fields = ", ".join(
                f"{k} {last[k]}"
                for k in ("resolutions", "redirects", "breakers", "plugins", "days")
                if k in last
            )
            checks.append(Check(event, "pass", fields or "restored"))
        else:
            checks.append(Check(event, "fail", "no record since the start"))
    return checks


def _description(stream: dict[str, Any]) -> str:
    return str(stream.get("description") or stream.get("title") or "")


def _first_line(stream: dict[str, Any]) -> str:
    return _description(stream).split("\n", 1)[0]


def _request_records(
    records: Iterable[dict[str, Any]], request_id: str | None
) -> list[dict[str, Any]]:
    if not request_id:
        return []
    return [r for r in records if r.get("request_id") == request_id]


def stream_checks(
    answers: Sequence[Answer],
    records: Sequence[dict[str, Any]],
    *,
    deadline_s: float,
) -> list[Check]:
    """Checks 5 and 6: the anime ids' episode codes, the movie's streams."""
    checks: list[Check] = []
    expected = dict(ANIME_IDS)
    for answer in answers:
        name = answer.id
        if answer.error or answer.status != 200:
            reason = answer.error or f"HTTP {answer.status}"
            checks.append(Check(name, "fail", f"{reason} after {answer.seconds:.1f} s"))
            continue
        own = _request_records(records, answer.request_id)
        seen = (
            "logged"
            if any(r.get("event") == "http_request" for r in own)
            else ("not in the log")
        )
        placed = next((r for r in own if r.get("event") == "stremio_episode_ref"), None)
        placement = (
            f"placed S{int(placed['season']):02d}E{int(placed['episode']):02d}"
            + (f" abs {placed['absolute']}" if placed.get("absolute") else "")
            if placed and placed.get("season") is not None
            else "no placement record (DEBUG)"
        )
        count = len(answer.streams)
        if name in expected:
            code = expected[name]
            with_code = [
                s for s in answer.streams if code.lower() in _description(s).lower()
            ]
            other = sorted(
                {
                    m.group(0).upper()
                    for s in answer.streams
                    for m in [_CODE.search(_description(s))]
                    if m
                }
                - {code}
            )
            if with_code and not other:
                detail = f"{count} streams, all {code}; {seen}; {placement}"
                status: Status = "pass"
            elif with_code:
                detail = (
                    f"{len(with_code)} of {count} streams {code}, others"
                    f" {', '.join(other)}; {seen}; {placement}"
                )
                status = "fail"
            else:
                sample = _first_line(answer.streams[0]) if answer.streams else "none"
                detail = f"{count} streams, none {code} (first: {sample}); {seen}"
                status = "fail"
            checks.append(Check(name, status, f"{detail}, {answer.seconds:.1f} s"))
        else:
            within = answer.seconds <= deadline_s
            if count and within:
                checks.append(
                    Check(
                        name,
                        "pass",
                        f"{count} streams in {answer.seconds:.1f} s; {seen}",
                    )
                )
            elif not count:
                checks.append(
                    Check(name, "fail", f"no streams, {answer.seconds:.1f} s")
                )
            else:
                checks.append(
                    Check(
                        name,
                        "fail",
                        f"{count} streams but {answer.seconds:.1f} s over the"
                        f" {deadline_s:.0f} s deadline",
                    )
                )
    return checks


def failed(checks: Iterable[Check]) -> bool:
    return any(c.status == "fail" for c in checks)


def render(checks: Sequence[Check]) -> str:
    """The Markdown table and a verdict line."""
    width = max(len(c.name) for c in checks) if checks else 5
    lines = [
        f"| {'Check'.ljust(width)} | Result | Detail |",
        f"|{'-' * (width + 2)}|--------|--------|",
    ]
    for c in checks:
        lines.append(
            f"| {c.name.ljust(width)} | {c.status.upper().ljust(6)} | {c.detail} |"
        )
    fails = sum(c.status == "fail" for c in checks)
    warns = sum(c.status == "warn" for c in checks)
    verdict = "FAIL" if fails else "PASS"
    lines.append("")
    lines.append(f"{verdict}: {len(checks)} checks, {fails} failed, {warns} warnings")
    return "\n".join(lines)
